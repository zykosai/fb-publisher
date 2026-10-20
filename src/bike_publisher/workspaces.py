"""Saved Facebook sessions and a fixed account context for concurrent workers."""
import json
import re
from dataclasses import replace
from uuid import uuid4

from .db import Database, utc_now

PRESETS = ('goods', 'services', 'creator')
SCOPED = {'publication_paused', 'worker_activity', 'turbo_mode', 'facebook_login_at'}


def ensure_workspace(db, settings):
    if db.one('SELECT 1 FROM browser_accounts WHERE slot_id=?', (db.get_setting('active_account_slot', ''),)):
        return
    account = db.get_setting('facebook_account_id', '')
    user = db.get_setting('facebook_user', {})
    slot = uuid4().hex
    now = utc_now()
    db.execute('''INSERT INTO browser_accounts(slot_id,account_id,label,user_json,session_json,legacy,created_at,updated_at)
        VALUES(?,?,?,?,?,1,?,?)''', (slot, account, user.get('name') or 'Основной аккаунт', json.dumps(user, ensure_ascii=False),
        json.dumps(db.get_setting('facebook_session', {}), ensure_ascii=False), now, now))
    db.set_setting('active_account_slot', slot)
    for key, default in [('publication_paused', True), ('turbo_mode', False), ('worker_activity', {})]:
        db.set_setting(f'workspace:{slot}:{key}', db.get_setting(key, default))


def active_workspace(db):
    return db.one('SELECT * FROM browser_accounts WHERE slot_id=?', (Database.get_setting(db, 'active_account_slot', ''),))


def workspace_settings(settings, db, slot=None):
    slot = slot if slot is not None else Database.get_setting(db, 'active_account_slot', '')
    if not slot:
        return settings
    row = db.one('SELECT * FROM browser_accounts WHERE slot_id=?', (slot,))
    if not row or not re.fullmatch(r'[a-f0-9]{32}', slot):
        raise ValueError('Профиль аккаунта не найден')
    profile = settings.browser_profile if row['legacy'] else settings.data_dir / 'accounts' / slot / 'browser_profile'
    return replace(settings, browser_profile=profile, account_slot=slot, expected_account_id=row['account_id'])


def validate_identity(db, account):
    if not isinstance(db, WorkspaceDatabase):
        return
    row = db.one('SELECT account_id FROM browser_accounts WHERE slot_id=?', (db.slot,))
    if row['account_id'] and row['account_id'] != account:
        db.set_setting('facebook_session', {'state': 'wrong_account', 'message': 'В браузере другой Facebook. Войди в нужный аккаунт этого профиля; публикация заблокирована.'})
        raise ValueError('В этом профиле другой Facebook. Для другого аккаунта нажми «Добавить аккаунт».')
    if db.one('SELECT 1 FROM browser_accounts WHERE account_id=? AND slot_id<>?', (account, db.slot)):
        raise ValueError('Этот Facebook уже сохранён. Переключись на его профиль, не создавай второй worker для того же аккаунта.')


class WorkspaceDatabase(Database):
    strict_account_scope = True

    def __init__(self, db, slot):
        self.path, self.slot = db.path, slot
        row = self.one('SELECT account_id FROM browser_accounts WHERE slot_id=?', (slot,))
        if not row:
            raise ValueError('Профиль аккаунта не найден')
        self.account_id = row['account_id']

    def get_setting(self, key, default=None):
        if key == 'facebook_account_id':
            return self.account_id
        if key in ('facebook_user', 'facebook_session'):
            column = 'user_json' if key == 'facebook_user' else 'session_json'
            row = self.one(f'SELECT {column} value FROM browser_accounts WHERE slot_id=?', (self.slot,))
            value = json.loads(row['value']) if row else {}
            if key == 'facebook_user':
                value.setdefault('account_id', self.account_id)
            return value
        return super().get_setting(f'workspace:{self.slot}:{key}' if key in SCOPED else key, default)

    def set_setting(self, key, value):
        if key == 'facebook_account_id':
            validate_identity(self, value)
            self.execute('UPDATE browser_accounts SET account_id=?,updated_at=? WHERE slot_id=?', (value, utc_now(), self.slot))
            self.account_id = value
            return
        if key in ('facebook_user', 'facebook_session'):
            column = 'user_json' if key == 'facebook_user' else 'session_json'
            self.execute(f'UPDATE browser_accounts SET {column}=?,updated_at=? WHERE slot_id=?', (json.dumps(value, ensure_ascii=False), utc_now(), self.slot))
            return
        super().set_setting(f'workspace:{self.slot}:{key}' if key in SCOPED else key, value)


def selected_database(db, slot=None):
    if db is None:
        return None
    slot = slot if slot is not None else Database.get_setting(db, 'active_account_slot', '')
    return WorkspaceDatabase(db, slot) if slot else db


def create_workspace(db, label, preset='goods'):
    label = str(label).strip()
    if not label or len(label) > 80 or preset not in PRESETS:
        raise ValueError('Укажи название аккаунта до 80 символов и режим интерфейса')
    slot, now = uuid4().hex, utc_now()
    db.execute('INSERT INTO browser_accounts(slot_id,label,preset,created_at,updated_at) VALUES(?,?,?,?,?)', (slot, label, preset, now, now))
    db.set_setting('active_account_slot', slot)
    return slot


def select_workspace(db, slot):
    if not db.one('SELECT 1 FROM browser_accounts WHERE slot_id=?', (slot,)):
        raise ValueError('Профиль аккаунта не найден')
    db.set_setting('active_account_slot', slot)


def update_workspace(db, slot, label, preset):
    if not str(label).strip() or len(str(label).strip()) > 80 or preset not in PRESETS:
        raise ValueError('Некорректное название или режим интерфейса')
    db.execute('UPDATE browser_accounts SET label=?,preset=?,updated_at=? WHERE slot_id=?', (str(label).strip(), preset, utc_now(), slot))
