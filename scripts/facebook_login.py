from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bike_publisher.config import Settings, ensure_dirs
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.account import save_account_status
from bike_publisher.db import Database
from bike_publisher.workspaces import ensure_workspace, selected_database, workspace_settings


if __name__ == "__main__":
    print("Запускаю окно входа Facebook...", flush=True)
    try:
        settings = Settings.from_root(ROOT)
        base = Database(settings.db_path)
        ensure_workspace(base, settings)
        settings = workspace_settings(settings, base)
        db = selected_database(base)
        if db.one("SELECT q.id FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status='running' AND b.owner_account_id=?", (db.get_setting('facebook_account_id', ''),)):
            raise RuntimeError('Pause this account and wait for its current publication before opening login.')
        db.set_setting('publication_paused', True)
        ensure_dirs(settings)
        print(f"Профиль браузера: {settings.browser_profile}", flush=True)
        publisher = FacebookPublisher(settings)
        publisher.login()
        if publisher.last_session:
            save_account_status(db, publisher.last_session, str(settings.browser_profile), login=True)
        print("Вход подтверждён, профиль Facebook сохранён.", flush=True)
    except Exception as exc:
        print(f"Ошибка входа: {exc}", flush=True)
        traceback.print_exc()
