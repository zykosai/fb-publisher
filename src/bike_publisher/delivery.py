from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .db import Database, utc_now
from .groups import normalize_group_url

ACCEPTED = ('sent', 'published', 'moderation')
PACKET_ACCEPTED_DETAIL = 'Отправка исходной группы принята вместе с подтверждённым пакетом; её видимость и одобрение отдельно не проверялись.'


def identity_for(db: Database, group_id: int) -> str:
    group = db.one('SELECT identity,url FROM groups_ WHERE id=?', (group_id,))
    if not group:
        raise ValueError('Группа не найдена в базе данных.')
    value = normalize_group_url(group['identity'] or group['url'])
    if value and value!=group['identity']:
        db.execute('UPDATE groups_ SET identity=? WHERE id=?',(value,group_id))
    alias = db.one('SELECT identity FROM group_aliases WHERE alias=?', (value,))
    return alias['identity'] if alias else value


def bind_identity(db: Database, group_id: int, canonical_url: str) -> None:
    canonical = normalize_group_url(canonical_url)
    if not canonical:
        return
    old = identity_for(db, group_id)
    with db.connect() as conn:
        conn.execute('UPDATE group_aliases SET identity=? WHERE identity=?', (canonical, old))
        conn.execute('INSERT INTO group_aliases(alias,identity) VALUES(?,?) ON CONFLICT(alias) DO UPDATE SET identity=excluded.identity', (old, canonical))
        conn.execute('UPDATE groups_ SET identity=? WHERE id=? OR identity=?', (canonical, group_id, old))


def group_gate(db: Database, group_id: int, *, now: datetime | None = None) -> dict:
    # An uncertain OLD delivery belongs to that task, not to the entire group.
    # Never retry uncertain tasks automatically, but don't lock future bikes.
    return {'allowed': True, 'until': None, 'reason': '', 'seconds': 0}


def mark_attempt(db: Database, jobs: list[dict]) -> None:
    """Persist before clicking Submit. A crash afterwards must never cause a retry."""
    if not jobs:
        return
    attempted_at = utc_now()
    with db.connect() as conn:
        for job in jobs:
            conn.execute("""INSERT INTO deliveries(queue_id,group_id,bike_id,state,attempted_at,origin_queue_id)
                VALUES(?,?,?,'submitting',?,?) ON CONFLICT(queue_id) DO UPDATE SET
                state='submitting',attempted_at=excluded.attempted_at,origin_queue_id=excluded.origin_queue_id,confirmed_at=NULL,detail=''""",
                (job['id'], job['group_id'], job['bike_id'], attempted_at, jobs[0]['id']))


def finish_delivery(db: Database, job: dict, state: str, url: str = '', detail: str = '') -> None:
    now = utc_now()
    with db.connect() as conn:
        conn.execute("""INSERT INTO deliveries(queue_id,group_id,bike_id,state,attempted_at,confirmed_at,url,detail)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(queue_id) DO UPDATE SET state=excluded.state,
            confirmed_at=excluded.confirmed_at,url=excluded.url,detail=excluded.detail""",
            (job['id'],job['group_id'],job['bike_id'],state,now,now if state in ACCEPTED else None,url,detail))
        conn.execute('UPDATE queue SET status=?,facebook_url=?,last_error=?,updated_at=? WHERE id=?', (state,url,detail,now,job['id']))
        if state == 'published':
            origin = conn.execute('SELECT origin_queue_id,attempted_at FROM deliveries WHERE queue_id=?', (job['id'],)).fetchone()
            origin_id = origin['origin_queue_id'] if origin else None
            if origin_id and origin_id != job['id']:
                # Crossposting confirms acceptance of the originating submission,
                # not public visibility/approval of that originating group post.
                changed = conn.execute("UPDATE deliveries SET state='sent',confirmed_at=?,detail=? WHERE queue_id=? AND attempted_at=? AND state IN ('unknown','sent')", (now, PACKET_ACCEPTED_DETAIL, origin_id, origin['attempted_at'])).rowcount
                if changed:
                    conn.execute("UPDATE queue SET status='sent',last_error=?,updated_at=? WHERE id=? AND status IN ('unknown','sent')", (PACKET_ACCEPTED_DETAIL, now, origin_id))


def recover_interrupted(db: Database) -> None:
    strict = getattr(db, 'strict_account_scope', False)
    account = db.get_setting('facebook_account_id', '')
    delivery_scope = ' AND queue_id IN (SELECT q.id FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE b.owner_account_id=?)' if strict else ''
    queue_scope = ' AND bike_id IN (SELECT id FROM bikes WHERE owner_account_id=?)' if strict else ''
    args = (account,) if strict else ()
    with db.connect() as conn:
        interrupted = conn.execute("SELECT queue_id FROM deliveries WHERE state='submitting'" + delivery_scope, args).fetchall()
        conn.execute("UPDATE deliveries SET state='unknown',detail='Процесс прерван после начала отправки' WHERE state='submitting'" + delivery_scope, args)
        conn.execute("UPDATE queue SET status='unknown',last_error='Процесс прерван: проверь результат в группе',updated_at=? WHERE status='running' AND id IN (SELECT queue_id FROM deliveries WHERE state='unknown')" + queue_scope, (utc_now(), *args))
        conn.execute("UPDATE queue SET status='failed',last_error='Процесс прерван до отправки; можно повторить',updated_at=? WHERE status='running' AND id NOT IN (SELECT queue_id FROM deliveries)" + queue_scope, (utc_now(), *args))
        due = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec='seconds')
        conn.executemany('INSERT OR IGNORE INTO delivery_checks(queue_id,due_at) VALUES(?,?)',
                         [(row['queue_id'], due) for row in interrupted])


def retry_job(db: Database, job_id: int) -> bool:
    job = db.one("SELECT q.*,g.enabled,g.archived,g.catalog FROM queue q JOIN groups_ g ON g.id=q.group_id WHERE q.id=? AND q.status='failed'", (job_id,))
    if not job or not group_gate(db, job['group_id'])['allowed']:
        return False
    if job.get('mode')=='single' and (not job['enabled'] or job['archived'] or not job['catalog']):
        return False
    if job.get('mode') in ('joined_batch','joined_single'):
        account_id=db.get_setting('facebook_account_id','')
        access=db.one('SELECT allowed,present FROM joined_groups WHERE group_id=? AND account_id=?',(job['group_id'],account_id))
        if not access or not access['allowed'] or not access['present']:
            return False
    record = db.one('SELECT state FROM deliveries WHERE queue_id=?', (job_id,))
    if record and record['state'] not in ('failed', 'not_sent'):
        return False
    return bool(db.execute("UPDATE queue SET status='pending',last_error='',scheduled_at=?,updated_at=? WHERE id=? AND status='failed'", (utc_now(),utc_now(),job_id)))
