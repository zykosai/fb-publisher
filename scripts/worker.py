from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bike_publisher.config import Settings, ensure_dirs
from bike_publisher.db import Database
from bike_publisher.delivery import recover_interrupted
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.process_lock import worker_lock
from bike_publisher.runner import run_once
from bike_publisher.worker_process import worker_signature
from bike_publisher.workspaces import selected_database, workspace_settings


def main():
    settings = Settings.from_root(ROOT)
    base = Database(settings.db_path)
    db = selected_database(base, settings.account_slot or None)
    settings = workspace_settings(settings, base, getattr(db, 'slot', ''))
    if getattr(db, 'strict_account_scope', False) and not db.get_setting('facebook_account_id', ''):
        raise RuntimeError('Сначала войди в Facebook этого аккаунта')
    ensure_dirs(settings)
    settings.worker_dir.mkdir(parents=True, exist_ok=True)
    with worker_lock(settings.worker_dir / "worker.lock") as acquired:
        if not acquired:
            print("Worker уже запущен. Второй процесс остановлен.", flush=True)
            return
        recover_interrupted(db)
        publisher = FacebookPublisher(settings)
        pid_file = settings.worker_dir / "worker.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        (settings.worker_dir / "worker.signature").write_text(worker_signature(settings), encoding="utf-8")
        print("Worker готов. Управляй запуском и паузой из веб-панели.", flush=True)
        try:
            while True:
                result = run_once(db, publisher)
                if result is not None:
                    print(f"Задание {result.job_id}: {result.message}", flush=True)
                time.sleep(0.2 if db.get_setting('turbo_mode', False) and not db.get_setting('publication_paused', True) else 1)
        finally:
            try:
                if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
