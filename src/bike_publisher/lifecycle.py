"""Explicit whole-application shutdown; ordinary UI pause remains per account."""
from .db import Database
from .worker_process import stop_worker
from .workspaces import selected_database, workspace_settings


def stop_all_workers(settings):
    db = Database(settings.db_path)
    db.set_setting('publication_paused', True)
    slots = db.all('SELECT slot_id FROM browser_accounts')
    for row in slots:
        selected_database(db, row['slot_id']).set_setting('publication_paused', True)
    # Pause every account before terminating any process.
    for row in slots:
        stop_worker(workspace_settings(settings, db, row['slot_id']))
    stop_worker(settings)  # Old installations may still have a legacy worker.
