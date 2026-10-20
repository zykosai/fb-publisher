from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .db import Database, utc_now
from .groups import clean_group_name, normalize_group_url


def group_restore_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def restorable_group_count(db: Database, account_id: str) -> int:
    return db.one('''SELECT COUNT(*) count FROM account_hidden_groups h
        JOIN groups_ g ON g.id=h.group_id
        JOIN account_groups a ON a.group_id=h.group_id AND a.account_id=h.account_id
        WHERE h.account_id=? AND g.archived=0 AND julianday(h.hidden_at)>julianday(?)''',
        (account_id, group_restore_cutoff()))['count']


def save_group_check(db: Database, account_id: str, group_id: int, result: dict) -> None:
    if not account_id:
        raise ValueError('Не удалось определить Facebook-аккаунт для проверки группы')
    now = utc_now()
    with db.connect() as conn:
        conn.execute('INSERT OR IGNORE INTO account_groups(account_id,group_id,added_at) VALUES(?,?,?)', (account_id, group_id, now))
        conn.execute("INSERT INTO group_checks(account_id,group_id,result) VALUES(?,?,?) ON CONFLICT(account_id,group_id) DO UPDATE SET result=excluded.result",
                     (account_id, group_id, json.dumps({**result, 'checked_at': now}, ensure_ascii=False)))
        if result.get('joined') is True:
            conn.execute("INSERT INTO joined_groups(account_id,group_id,allowed,present,seen_at) VALUES(?,?,0,1,?) ON CONFLICT(account_id,group_id) DO UPDATE SET present=1,seen_at=excluded.seen_at", (account_id, group_id, now))
        elif result.get('joined') is False:
            conn.execute('UPDATE joined_groups SET present=0,allowed=0,seen_at=? WHERE account_id=? AND group_id=?', (now, account_id, group_id))
        if result['state'] != 'ready':
            conn.execute('UPDATE joined_groups SET allowed=0 WHERE account_id=? AND group_id=?', (account_id, group_id))


def save_snapshot(db: Database, account_id: str, found: list[dict]) -> int:
    if not account_id:
        raise ValueError('Не удалось определить, к какому Facebook-аккаунту относятся эти группы.')
    now=utc_now(); count=0
    with db.connect() as conn:
        conn.execute('UPDATE joined_groups SET present=0,seen_at=? WHERE account_id=?',(now,account_id))
        for item in found:
            url=normalize_group_url(item.get('url',''))
            name=clean_group_name(item.get('name',''))[:200]
            if not url or not name:
                continue
            alias=conn.execute('SELECT identity FROM group_aliases WHERE alias=?',(url,)).fetchone()
            resolved=alias['identity'] if alias else url
            row=conn.execute('SELECT id,identity FROM groups_ WHERE url=? OR identity=? ORDER BY id LIMIT 1',(url,resolved)).fetchone()
            if row:
                group_id=row['id']
                identity=row['identity'] or url
            else:
                cur=conn.execute("""INSERT INTO groups_(name,url,enabled,archived,catalog,identity,reason,created_at,updated_at)
                VALUES(?,?,1,0,0,?,?,?,?)""",(name,url,url,'Моя группа Facebook',now,now))
                group_id=cur.lastrowid; identity=url
            conn.execute('INSERT OR IGNORE INTO account_groups(account_id,group_id,added_at) VALUES(?,?,?)', (account_id, group_id, now))
            conn.execute("""INSERT INTO joined_groups(account_id,group_id,allowed,present,seen_at) VALUES(?,?,0,1,?)
                ON CONFLICT(account_id,group_id) DO UPDATE SET present=1,seen_at=excluded.seen_at""",(account_id,group_id,now))
            conn.execute('INSERT OR IGNORE INTO group_aliases(alias,identity) VALUES(?,?)',(url,identity))
            count+=1
    db.log('joined_groups_refreshed',f'Список групп Facebook обновлён: {count}')
    return count


def update_permissions(db: Database, account_id: str, group_ids: list[int]) -> int:
    allowed=set(int(item) for item in group_ids)
    with db.connect() as conn:
        rows=conn.execute('''SELECT group_id FROM joined_groups j WHERE account_id=? AND present=1
            AND NOT EXISTS (SELECT 1 FROM account_hidden_groups h WHERE h.account_id=j.account_id AND h.group_id=j.group_id)''',(account_id,)).fetchall()
        conn.executemany('UPDATE joined_groups SET allowed=? WHERE account_id=? AND group_id=?',
            [(int(row['group_id'] in allowed),account_id,row['group_id']) for row in rows])
    return len(allowed)


def remove_account_groups(db: Database, account_id: str, group_ids: list[int]) -> dict:
    """Hide groups and cancel waiting jobs only for this Facebook account."""
    if not account_id:
        raise ValueError('Войди в Facebook перед удалением групп')
    ids = list(dict.fromkeys(int(identity) for identity in group_ids))
    removed = cancelled = 0
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for identity in ids:
            if conn.execute('''SELECT 1 FROM queue q JOIN bikes b ON b.id=q.bike_id
                WHERE q.group_id=? AND b.owner_account_id=? AND q.status='running' LIMIT 1''',
                (identity, account_id)).fetchone():
                raise ValueError('Поставь бота на паузу и дождись завершения текущей публикации перед удалением групп')
        now = utc_now()
        for identity in ids:
            if not conn.execute('SELECT 1 FROM account_groups WHERE account_id=? AND group_id=?', (account_id, identity)).fetchone():
                continue
            removed += conn.execute('''INSERT OR IGNORE INTO account_hidden_groups(account_id,group_id,hidden_at)
                SELECT ?,id,? FROM groups_ WHERE id=? AND archived=0''', (account_id, now, identity)).rowcount
            conn.execute('UPDATE joined_groups SET allowed=0 WHERE account_id=? AND group_id=?', (account_id, identity))
            cancelled += conn.execute('''UPDATE queue SET status='cancelled',last_error='Группа удалена из списка этого аккаунта',updated_at=?
                WHERE group_id=? AND bike_id IN (SELECT id FROM bikes WHERE owner_account_id=?)
                AND status IN ('pending','waiting_approval')''', (now, identity, account_id)).rowcount
    return {'removed': removed, 'cancelled_jobs': cancelled}


def restore_account_groups(db: Database, account_id: str) -> int:
    """Undo removal within five minutes; never resume cancelled publications.

    Expired tombstones stay private to their account so sync cannot resurrect
    permanently removed groups or alter another account's catalog/history.
    """
    if not account_id:
        raise ValueError('Войди в Facebook перед восстановлением групп')
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Match the visible catalog: legacy global archives are not account deletions.
        restored = conn.execute('''DELETE FROM account_hidden_groups
            WHERE account_id=? AND julianday(hidden_at)>julianday(?) AND group_id IN (
                SELECT g.id FROM groups_ g JOIN account_groups a ON a.group_id=g.id
                WHERE a.account_id=? AND g.archived=0
            )''', (account_id, group_restore_cutoff(), account_id)).rowcount
    return restored
