import json
from types import SimpleNamespace

import pytest

from bike_publisher.account import save_account_status
from bike_publisher.db import Database, utc_now
from bike_publisher.facebook import SessionStatus
from bike_publisher.group_checks import get_group_check
from bike_publisher.joined_groups import save_group_check, save_snapshot, update_permissions, remove_account_groups
from bike_publisher.runner import _permission
from bike_publisher.webapp import Application


@pytest.fixture
def shared_group(tmp_path):
    db = Database(tmp_path / 'app.db')
    item = {'name': 'Shared group', 'url': 'https://facebook.com/groups/shared'}
    for account in ('A', 'B'):
        save_snapshot(db, account, [item])
    return db, db.one('SELECT id FROM groups_')['id']


def switch(db, account):
    save_account_status(db, SessionStatus(True, 'ok', account, f'User {account}'), 'profile')


def test_switching_a_b_a_restores_checks_and_selection(shared_group):
    db, group = shared_group
    switch(db, 'A')
    save_group_check(db, 'A', group, {'state': 'ready', 'joined': True, 'pending_count': 4})
    update_permissions(db, 'A', [group])
    switch(db, 'B')
    assert get_group_check(db, group) == {'state': 'unchecked'}
    save_group_check(db, 'B', group, {'state': 'blocked', 'joined': None})
    assert get_group_check(db, group)['state'] == 'blocked'
    switch(db, 'A')
    assert get_group_check(db, group)['pending_count'] == 4
    assert get_group_check(db, group)['state'] == 'ready'
    assert db.one("SELECT allowed FROM joined_groups WHERE account_id='A' AND group_id=?", (group,))['allowed'] == 1
    assert get_group_check(db, group, 'B')['state'] == 'blocked'
    assert _permission(db, {'mode': 'joined_batch', 'group_id': group})
    switch(db, 'B')
    assert not _permission(db, {'mode': 'joined_batch', 'group_id': group})


def test_another_account_never_uses_ready_check(shared_group):
    db, group = shared_group
    save_group_check(db, 'A', group, {'state': 'ready', 'joined': True})
    update_permissions(db, 'B', [group])
    switch(db, 'B')
    assert not _permission(db, {'mode': 'joined_batch', 'group_id': group})


def test_legacy_checks_migrate_once_to_original_owner(shared_group):
    db, group = shared_group
    # Simulate a pre-upgrade database with old global settings and no marker.
    db.execute("DELETE FROM settings WHERE key='group_checks_legacy_owner'")
    db.set_setting('facebook_account_id', 'A')
    original = {'state': 'moderation_limit', 'pending_count': 21, 'checked_at': '2026-10-11T12:00:00+00:00'}
    db.set_setting(f'group_check:{group}', original)
    db = Database(db.path)
    assert get_group_check(db, group, 'A') == original
    assert db.get_setting('group_checks_legacy_owner') == 'A'
    switch(db, 'B')
    db = Database(db.path)
    assert get_group_check(db, group) == {'state': 'unchecked'}
    save_group_check(db, 'A', group, {'state': 'ready'})
    db = Database(db.path)
    assert get_group_check(db, group, 'A')['state'] == 'ready'
    assert get_group_check(db, group, 'B') == {'state': 'unchecked'}


def test_anonymous_old_checks_are_not_given_to_next_login(shared_group):
    db, group = shared_group
    db.execute("DELETE FROM settings WHERE key='group_checks_legacy_owner'")
    db.set_setting(f'group_check:{group}', {'state': 'ready'})
    db = Database(db.path)
    switch(db, 'B')
    db = Database(db.path)
    assert get_group_check(db, group) == {'state': 'unchecked'}


def test_web_state_reads_only_active_accounts_checks(shared_group, monkeypatch):
    from bike_publisher import webapp
    db, group = shared_group
    save_group_check(db, 'A', group, {'state': 'ready', 'joined': True, 'pending_count': 4})
    save_group_check(db, 'B', group, {'state': 'removed', 'joined': False})
    app = Application.__new__(Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    switch(db, 'A')
    assert app.state()['groups'][0]['check']['pending_count'] == 4
    assert app.state()['groups'][0]['joined'] == 1
    switch(db, 'B')
    assert app.state()['groups'][0]['check']['state'] == 'removed'
    assert app.state()['groups'][0]['joined'] == 0


def test_save_requires_account_and_does_not_write_global_check(shared_group):
    db, group = shared_group
    with pytest.raises(ValueError):
        save_group_check(db, '', group, {'state': 'ready'})
    save_group_check(db, 'A', group, {'state': 'ready'})
    assert db.get_setting(f'group_check:{group}') is None
    assert json.loads(db.one('SELECT result FROM group_checks')['result'])['state'] == 'ready'


def test_delete_only_affects_current_account_and_its_waiting_jobs(shared_group, monkeypatch):
    from bike_publisher import webapp
    db, group = shared_group
    jobs = {}
    for account in ('A', 'B'):
        save_group_check(db, account, group, {'state':'ready', 'joined':True, 'pending_count':4})
        update_permissions(db, account, [group])
        product = db.execute('''INSERT INTO bikes(external_id,name,owner_account_id,created_at,updated_at)
            VALUES(?,?,?,?,?)''', (account, account, account, utc_now(), utc_now()))
        jobs[account] = db.execute('''INSERT INTO queue(bike_id,group_id,scheduled_at,status,mode,created_at,updated_at)
            VALUES(?,?,?,'pending','joined_batch',?,?)''', (product, group, utc_now(), utc_now(), utc_now()))
    app = Application.__new__(Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    switch(db, 'B')
    app.action('/api/groups/delete', {'ids':[group]})
    assert app.state()['groups'] == []
    assert not _permission(db, {'mode':'joined_batch','group_id':group})
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs['B'],))['status'] == 'cancelled'
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs['A'],))['status'] == 'pending'
    assert db.one('SELECT archived FROM groups_ WHERE id=?', (group,))['archived'] == 0
    switch(db, 'A')
    visible = app.state()['groups'][0]
    assert visible['id'] == group and visible['selected'] == 1
    assert visible['check']['pending_count'] == 4
    assert _permission(db, {'mode':'joined_batch','group_id':group})
    switch(db, 'B')
    save_snapshot(db, 'B', [{'name':'Shared group','url':'https://facebook.com/groups/shared'}])
    update_permissions(db, 'B', [group])
    assert app.state()['groups'] == []
    assert not _permission(db, {'mode':'joined_batch','group_id':group})
    app.action('/api/groups/add', {'name':'Shared group','url':'https://facebook.com/groups/shared'})
    assert app.state()['groups'][0]['id'] == group
    assert app.state()['groups'][0]['selected'] == 0


def test_delete_refuses_anonymous_account(shared_group):
    db, group = shared_group
    with pytest.raises(ValueError): remove_account_groups(db, '', [group])
    assert db.all('SELECT * FROM account_hidden_groups') == []


def test_restore_is_account_scoped_and_keeps_checks_and_cancelled_jobs(shared_group, monkeypatch):
    from bike_publisher import webapp
    db, group = shared_group
    for account in ('A', 'B'):
        save_group_check(db, account, group, {'state': 'ready', 'joined': True, 'pending_count': 4})
        update_permissions(db, account, [group])
        remove_account_groups(db, account, [group])
    product = db.execute('''INSERT INTO bikes(external_id,name,owner_account_id,created_at,updated_at)
        VALUES('A','A','A',?,?)''', (utc_now(), utc_now()))
    job = db.execute('''INSERT INTO queue(bike_id,group_id,scheduled_at,status,created_at,updated_at)
        VALUES(?,?,?,'cancelled',?,?)''', (product, group, utc_now(), utc_now(), utc_now()))
    app = Application.__new__(Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    switch(db, 'A')
    assert app.state()['deleted_groups_count'] == 1
    # A supplied foreign account must not override the authenticated active account.
    assert app.action('/api/groups/restore', {'account_id': 'B'})['restored'] == 1
    restored = app.state()['groups'][0]
    assert restored['check']['pending_count'] == 4
    assert restored['joined'] == 1 and restored['selected'] == 0
    assert app.state()['deleted_groups_count'] == 0
    assert db.one('SELECT status FROM queue WHERE id=?', (job,))['status'] == 'cancelled'
    assert app.action('/api/groups/restore', {})['restored'] == 0
    switch(db, 'B')
    assert app.state()['groups'] == []
    assert app.state()['deleted_groups_count'] == 1


def test_restore_refuses_anonymous_account_and_keeps_legacy_archives(shared_group):
    from bike_publisher.joined_groups import restore_account_groups
    db, group = shared_group
    remove_account_groups(db, 'A', [group])
    with pytest.raises(ValueError):
        restore_account_groups(db, '')
    db.execute('UPDATE groups_ SET archived=1 WHERE id=?', (group,))
    assert restore_account_groups(db, 'A') == 0
    assert db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=?', ('A',))


def test_restore_expires_after_five_minutes_without_resurrecting_groups(shared_group, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from bike_publisher import joined_groups
    db, group = shared_group
    remove_account_groups(db, 'A', [group])
    remove_account_groups(db, 'B', [group])
    deleted_at = datetime.now(timezone.utc).replace(microsecond=0)
    db.execute('UPDATE account_hidden_groups SET hidden_at=? WHERE account_id=?', (deleted_at.isoformat(), 'A'))
    # Simulate time without sleeping or depending on the SQLite host clock.
    monkeypatch.setattr(joined_groups, 'group_restore_cutoff', lambda: (deleted_at - timedelta(seconds=1)).isoformat())
    assert joined_groups.restorable_group_count(db, 'A') == 1
    monkeypatch.setattr(joined_groups, 'group_restore_cutoff', lambda: deleted_at.isoformat())
    assert joined_groups.restorable_group_count(db, 'A') == 0
    assert joined_groups.restore_account_groups(db, 'A') == 0
    save_snapshot(db, 'A', [{'name': 'Shared group', 'url': 'https://facebook.com/groups/shared'}])
    assert db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=?', ('A',))
    assert db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=?', ('B',))


def test_delete_running_job_is_atomic(shared_group):
    db, group = shared_group
    product = db.execute('''INSERT INTO bikes(external_id,name,owner_account_id,created_at,updated_at)
        VALUES('B','B','B',?,?)''', (utc_now(), utc_now()))
    db.execute('''INSERT INTO queue(bike_id,group_id,scheduled_at,status,created_at,updated_at)
        VALUES(?,?,?,'running',?,?)''', (product, group, utc_now(), utc_now(), utc_now()))
    with pytest.raises(ValueError): remove_account_groups(db, 'B', [group])
    assert db.all('SELECT * FROM account_hidden_groups') == []


def test_catalog_lists_are_isolated_when_switching_accounts(tmp_path, monkeypatch):
    from bike_publisher import webapp
    from bike_publisher.groups import save_group
    db = Database(tmp_path / 'app.db')
    shared = {'name':'Shared', 'url':'https://facebook.com/groups/shared'}
    save_snapshot(db, 'A', [shared, {'name':'A only','url':'https://facebook.com/groups/a-only'}])
    save_snapshot(db, 'B', [shared, {'name':'B only','url':'https://facebook.com/groups/b-only'}])
    app = Application.__new__(Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    for account, expected in [('A',{'Shared','A only'}), ('B',{'Shared','B only'}), ('A',{'Shared','A only'}), ('C',set())]:
        switch(db, account)
        assert {g['name'] for g in app.state()['groups']} == expected
    switch(db, 'B')
    save_group(db, 'Search B', 'https://facebook.com/groups/search-b', '', 0, 'pl')
    assert 'Search B' in {g['name'] for g in app.state()['groups']}
    switch(db, 'A')
    assert {g['name'] for g in app.state()['groups']} == {'Shared','A only'}


def test_legacy_catalog_migration_never_assigns_foreign_or_unattributed_groups(tmp_path):
    from bike_publisher.groups import save_group
    db = Database(tmp_path / 'app.db')
    save_snapshot(db, 'A', [{'name':'A','url':'https://facebook.com/groups/a'}])
    save_snapshot(db, 'B', [{'name':'B','url':'https://facebook.com/groups/b'}])
    _, orphan = save_group(db, 'Unknown owner', 'https://facebook.com/groups/orphan', '', 0, 'pl')
    db.execute('DELETE FROM account_groups')
    db.execute("DELETE FROM settings WHERE key='account_group_catalog_migrated'")
    switch(db, 'A')
    db = Database(db.path)
    pairs = db.all('SELECT a.account_id,g.name FROM account_groups a JOIN groups_ g ON g.id=a.group_id ORDER BY a.account_id')
    assert pairs == [{'account_id':'A','name':'A'}, {'account_id':'B','name':'B'}]
    assert not db.one('SELECT 1 FROM account_groups WHERE group_id=?', (orphan,))
    switch(db, 'C')
    db = Database(db.path)
    assert not db.one("SELECT 1 FROM account_groups WHERE account_id='C'")
