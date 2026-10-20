"""Persistent, random access key for the private control panel."""
from __future__ import annotations

import json
import re
import secrets

from .db import Database


def pairing_key(db: Database) -> str:
    # Serialise first-time creation so concurrent starts cannot get different keys.
    # The setting stays in the private app database, never in /api/state or assets.
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT value FROM settings WHERE key='web_pairing_key'").fetchone()
        try:
            key = json.loads(row['value']) if row else None
        except (json.JSONDecodeError, TypeError):
            key = None
        if isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
            return key
        key = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO settings(key,value) VALUES('web_pairing_key',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(key),))
        return key
