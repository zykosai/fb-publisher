"""Account catalog membership is separate from Facebook membership and deletion."""
from __future__ import annotations

from .db import Database, utc_now


def attach_group(db: Database, group_id: int, account_id: str | None = None) -> None:
    account = db.get_setting('facebook_account_id', '') if account_id is None else account_id
    if account:
        db.execute('INSERT OR IGNORE INTO account_groups(account_id,group_id,added_at) VALUES(?,?,?)',
                   (account, group_id, utc_now()))


def migrate_account_catalog(conn) -> None:
    """Assign legacy groups only from recorded account evidence, exactly once."""
    if conn.execute("SELECT 1 FROM settings WHERE key='account_group_catalog_migrated'").fetchone():
        return
    now = utc_now()
    conn.execute('''INSERT OR IGNORE INTO account_groups(account_id,group_id,added_at)
        SELECT account_id,group_id,? FROM joined_groups WHERE account_id<>''
        UNION SELECT account_id,group_id,? FROM group_checks WHERE account_id<>''
        UNION SELECT account_id,group_id,? FROM account_hidden_groups WHERE account_id<>''
        UNION SELECT b.owner_account_id,q.group_id,? FROM queue q JOIN bikes b ON b.id=q.bike_id
            WHERE b.owner_account_id<>'' ''', (now, now, now, now))
    conn.execute("INSERT INTO settings(key,value) VALUES('account_group_catalog_migrated','true')")
