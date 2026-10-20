"""Account-specific Facebook group checks; legacy results have one owner."""
from __future__ import annotations

import json

from .db import Database


def migrate_legacy_checks(conn) -> None:
    """Called in the database migration transaction, before an account switch."""
    if conn.execute("SELECT 1 FROM settings WHERE key='group_checks_legacy_owner'").fetchone():
        return
    row = conn.execute("SELECT value FROM settings WHERE key='facebook_account_id'").fetchone()
    try:
        owner = json.loads(row['value']) if row else ''
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(owner, str) or not owner:
        conn.execute("INSERT INTO settings(key,value) VALUES('group_checks_legacy_owner','null')")
        return  # Never attribute anonymous checks to the next account to log in.
    for old in conn.execute("SELECT key,value FROM settings WHERE key GLOB 'group_check:[0-9]*'").fetchall():
        suffix = old['key'].removeprefix('group_check:')
        if not suffix.isdigit():
            continue
        if not conn.execute('SELECT 1 FROM groups_ WHERE id=?', (int(suffix),)).fetchone():
            continue
        try:
            result = json.loads(old['value'])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(result, dict):
            conn.execute('INSERT OR IGNORE INTO group_checks(account_id,group_id,result) VALUES(?,?,?)',
                         (owner, int(suffix), json.dumps(result, ensure_ascii=False)))
    conn.execute("INSERT INTO settings(key,value) VALUES('group_checks_legacy_owner',?)", (json.dumps(owner),))


def get_group_check(db: Database, group_id: int, account_id: str | None = None) -> dict:
    account = db.get_setting('facebook_account_id', '') if account_id is None else account_id
    if not account:
        return {'state': 'unchecked'}
    row = db.one('SELECT result FROM group_checks WHERE account_id=? AND group_id=?', (account, group_id))
    if row:
        try:
            result = json.loads(row['result'])
        except (json.JSONDecodeError, TypeError):
            result = None
        if isinstance(result, dict) and isinstance(result.get('state'), str):
            return result
    return {'state': 'unchecked'}
