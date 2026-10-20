from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from bike_publisher.db import Database
from bike_publisher.group_join import GroupAccountError, JoinRateLimited
from bike_publisher.joined_groups import save_snapshot
from bike_publisher.webapp import Application
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.config import Settings


def setup_batch(tmp_path, outcomes):
    db = Database(tmp_path / 'app.db')
    db.set_setting('facebook_account_id', 'A')
    save_snapshot(db, 'A', [{'name': f'Group {i}', 'url': f'https://facebook.com/groups/test-{i}'} for i in range(len(outcomes))])
    calls = []

    def inspect(url, **kwargs):
        result = outcomes[len(calls)]
        calls.append(url)
        if isinstance(result, Exception): raise result
        return result

    publisher = SimpleNamespace(checkpoint=lambda: None,
        verify_login=lambda: SimpleNamespace(logged_in=True, account_id='A'),
        group_check_session=nullcontext, inspect_group=inspect)
    app = Application.__new__(Application)
    app.db, app.operation = db, {}
    app.run_operation = lambda title, action, **kwargs: {'message': action(publisher)}
    ids = [g['id'] for g in db.all('SELECT id FROM groups_ ORDER BY id')]
    return app, db, ids, calls


def test_group_error_is_saved_and_next_group_is_processed(tmp_path):
    app, db, ids, calls = setup_batch(tmp_path, [RuntimeError('Group page failed'), {'state': 'ready', 'joined': True}])
    app.action('/api/groups/check', {'ids': ids, 'join': True})
    assert len(calls) == 2
    results = db.get_setting('group_check_progress:A')['results']
    assert results[0]['state'] == 'check_failed'
    assert results[1]['joined'] is True


def test_account_error_is_not_treated_as_one_bad_group(tmp_path):
    app, db, ids, calls = setup_batch(tmp_path, [GroupAccountError('Account changed'), {'state': 'ready', 'joined': True}])
    with pytest.raises(RuntimeError, match='Account changed'):
        app.action('/api/groups/check', {'ids': ids, 'join': True})
    assert len(calls) == 1
    assert db.get_setting('group_check_progress:A')['stopped']


def test_confirmed_membership_saved_before_account_limit_stops_new_joins(tmp_path):
    app, db, ids, calls = setup_batch(tmp_path, [
        {'state': 'ready', 'joined': True, 'join_rate_limit': 'Limit actions'},
        {'state': 'ready', 'joined': True}])
    with pytest.raises(JoinRateLimited):
        app.action('/api/groups/check', {'ids': ids, 'join': True})
    assert len(calls) == 1
    assert db.one('SELECT present FROM joined_groups WHERE group_id=?', (ids[0],))['present'] == 1
    assert db.get_setting('group_check_progress:A')['results'][0]['joined'] is True


def test_already_joined_group_never_clicks_recommended_group_join(tmp_path, monkeypatch):
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    page = SimpleNamespace(url='https://facebook.com/groups/test', goto=lambda *args, **kwargs: None)
    publisher._group_check_context = SimpleNamespace(pages=[page])
    recommendation = SimpleNamespace(click=lambda: (_ for _ in ()).throw(AssertionError('Unrelated join clicked')))
    monkeypatch.setattr(publisher, '_pause', lambda *args, **kwargs: None)
    monkeypatch.setattr(publisher, '_account_id', lambda _: 'A')
    monkeypatch.setattr(publisher, '_inspect_group_access', lambda *args, **kwargs: {'state': 'ready', 'joined': True})
    monkeypatch.setattr(publisher, '_group_join_button', lambda _: recommendation)
    monkeypatch.setattr(publisher, '_find_post_composer', lambda *args, **kwargs: object())
    assert publisher.inspect_group(page.url, join=True, expected_account_id='A')['joined'] is True
