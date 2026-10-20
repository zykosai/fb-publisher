from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bike_publisher.config import Settings, ensure_dirs
from bike_publisher.db import Database
from bike_publisher.importer import import_zip
from bike_publisher.workspaces import ensure_workspace, selected_database


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python scripts/import_zip.py путь_к_zip")
    settings = Settings.from_root(ROOT)
    ensure_dirs(settings)
    base = Database(settings.db_path)
    ensure_workspace(base, settings)
    db = selected_database(base)
    if not db.get_setting('facebook_account_id', ''):
        raise SystemExit('Sign in to the selected account in the dashboard before importing.')
    result = import_zip(Path(sys.argv[1]).resolve(), settings.data_dir, db)
    print(result)
