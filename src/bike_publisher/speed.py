"""Shared live speed preference; does not modify schedules or Facebook limits."""
import sqlite3
from contextlib import closing
from pathlib import Path


def turbo_enabled(settings) -> bool:
    path = getattr(settings, 'db_path', None)
    if path is None or not Path(path).is_file():
        return False
    try:
        with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as conn:
            slot = getattr(settings, 'account_slot', '')
            key = f'workspace:{slot}:turbo_mode' if slot else 'turbo_mode'
            row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return bool(row and row[0] == 'true')
    except sqlite3.Error:
        return False
