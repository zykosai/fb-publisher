from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS bikes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    price TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL DEFAULT '',
    gears TEXT NOT NULL DEFAULT '',
    brakes TEXT NOT NULL DEFAULT '',
    fork TEXT NOT NULL DEFAULT '',
    frame TEXT NOT NULL DEFAULT '',
    wheels TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bike_id INTEGER NOT NULL REFERENCES bikes(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(bike_id, path)
);
CREATE TABLE IF NOT EXISTS groups_ (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL DEFAULT '',
    distance_km REAL,
    language TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bike_id INTEGER NOT NULL REFERENCES bikes(id),
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    facebook_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(bike_id, group_id, scheduled_at)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_checks (
    account_id TEXT NOT NULL,
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    result TEXT NOT NULL,
    PRIMARY KEY(account_id, group_id)
);
CREATE TABLE IF NOT EXISTS browser_accounts (
    slot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,
    user_json TEXT NOT NULL DEFAULT '{}',
    session_json TEXT NOT NULL DEFAULT '{}',
    preset TEXT NOT NULL DEFAULT 'goods',
    legacy INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS browser_account_identity ON browser_accounts(account_id) WHERE account_id<>'';
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            audit_columns = {row['name'] for row in conn.execute('PRAGMA table_info(audit_log)')}
            if 'account_id' not in audit_columns:
                conn.execute("ALTER TABLE audit_log ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
            bike_columns = {row['name'] for row in conn.execute('PRAGMA table_info(bikes)')}
            if 'sold_price_cents' not in bike_columns:
                conn.execute('ALTER TABLE bikes ADD COLUMN sold_price_cents INTEGER')
            for name, declaration in {
                'owner_account_id': "TEXT NOT NULL DEFAULT ''",
                'source': "TEXT NOT NULL DEFAULT 'manual'",
                'source_listing_id': "TEXT NOT NULL DEFAULT ''",
                'source_url': "TEXT NOT NULL DEFAULT ''",
                'imported_at': "TEXT NOT NULL DEFAULT ''",
                'post_heading': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in bike_columns:
                    conn.execute(f'ALTER TABLE bikes ADD COLUMN {name} {declaration}')
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS marketplace_product_identity ON bikes(owner_account_id,source_listing_id) WHERE source='facebook' AND source_listing_id<>''")
            conn.execute('''CREATE TABLE IF NOT EXISTS marketplace_previews (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, data TEXT NOT NULL,
                created_at TEXT NOT NULL, result TEXT
            )''')
            owner_row = conn.execute("SELECT value FROM settings WHERE key='facebook_account_id'").fetchone()
            try:
                catalog_owner = json.loads(owner_row['value']) if owner_row else ''
            except (ValueError, TypeError):
                catalog_owner = ''
            if isinstance(catalog_owner, str) and catalog_owner:
                conn.execute("UPDATE bikes SET owner_account_id=? WHERE owner_account_id=''", (catalog_owner,))
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(queue)").fetchall()}
            if 'history_hidden' not in columns:
                conn.execute('ALTER TABLE queue ADD COLUMN history_hidden INTEGER NOT NULL DEFAULT 0')
            if "approval_required" not in columns:
                conn.execute("ALTER TABLE queue ADD COLUMN approval_required INTEGER NOT NULL DEFAULT 0")
            group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups_)").fetchall()}
            if "archived" not in group_columns:
                conn.execute("ALTER TABLE groups_ ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            for name, declaration in {
                "catalog": "INTEGER NOT NULL DEFAULT 1",
                "identity": "TEXT NOT NULL DEFAULT ''",
                "reason": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in group_columns:
                    conn.execute(f"ALTER TABLE groups_ ADD COLUMN {name} {declaration}")
            for name, declaration in {
                "mode": "TEXT NOT NULL DEFAULT 'single'",
                "plan_id": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE queue ADD COLUMN {name} {declaration}")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS joined_groups (
                    account_id TEXT NOT NULL, group_id INTEGER NOT NULL REFERENCES groups_(id),
                    allowed INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1,
                    seen_at TEXT NOT NULL, PRIMARY KEY(account_id,group_id)
                );
                CREATE TABLE IF NOT EXISTS account_hidden_groups (
                    account_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups_(id) ON DELETE CASCADE,
                    hidden_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,group_id)
                );
                CREATE TABLE IF NOT EXISTS account_groups (
                    account_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups_(id) ON DELETE CASCADE,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,group_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups_(id), bike_id INTEGER NOT NULL,
                    state TEXT NOT NULL, attempted_at TEXT NOT NULL,
                    confirmed_at TEXT, url TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
                    origin_queue_id INTEGER,
                    UNIQUE(queue_id)
                );
                CREATE TABLE IF NOT EXISTS group_aliases (
                    alias TEXT PRIMARY KEY, identity TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS delivery_group_state ON deliveries(group_id,state);
                CREATE TABLE IF NOT EXISTS delivery_checks (
                    queue_id INTEGER PRIMARY KEY REFERENCES queue(id) ON DELETE CASCADE,
                    due_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS queue_due ON queue(status,scheduled_at);
            """)
            delivery_columns = {row['name'] for row in conn.execute('PRAGMA table_info(deliveries)')}
            if 'origin_queue_id' not in delivery_columns:
                conn.execute('ALTER TABLE deliveries ADD COLUMN origin_queue_id INTEGER')
            from .groups import clean_group_name, normalize_group_url
            for group in conn.execute('SELECT id,url,identity,name FROM groups_').fetchall():
                name = clean_group_name(group['name'])
                if name and name != group['name']:
                    conn.execute('UPDATE groups_ SET name=? WHERE id=?', (name, group['id']))
                identity=normalize_group_url(group['identity'] or group['url'])
                if identity and identity!=group['identity']:
                    conn.execute('UPDATE groups_ SET identity=? WHERE id=?',(identity,group['id']))
            conn.execute("""INSERT OR IGNORE INTO deliveries(queue_id,group_id,bike_id,state,attempted_at,confirmed_at,url,detail)
                SELECT id,group_id,bike_id,'sent',updated_at,updated_at,facebook_url,'Старая версия: отправка отмечена принятой'
                FROM queue WHERE status='sent'""")
            from .group_checks import migrate_legacy_checks
            migrate_legacy_checks(conn)
            from .account_groups import migrate_account_catalog
            migrate_account_catalog(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return int(cur.lastrowid or cur.rowcount)

    def log(self, event_type: str, message: str) -> None:
        self.execute("INSERT INTO audit_log(event_type,message,created_at,account_id) VALUES(?,?,?,?)", (event_type, message, utc_now(), self.get_setting('facebook_account_id', '')))

    def clear_audit_log(self) -> int:
        """Delete application audit events without creating a new event."""
        with self.connect() as conn:
            if getattr(self, 'strict_account_scope', False):
                cur = conn.execute("DELETE FROM audit_log WHERE account_id=?", (self.get_setting('facebook_account_id', ''),))
            else:
                cur = conn.execute("DELETE FROM audit_log")
            return int(cur.rowcount)

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]
