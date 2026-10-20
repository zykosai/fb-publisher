from types import SimpleNamespace

import pytest

from bike_publisher.config import Settings
from bike_publisher.control import OperationCancelled
from bike_publisher.db import Database
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.runner import _space_remaining_packets
from bike_publisher.speed import turbo_enabled
from bike_publisher.webapp import Application


def test_live_toggle_skips_only_artificial_pause_and_preserves_cancel(tmp_path):
    settings = Settings.from_root(tmp_path)
    db = Database(settings.db_path)
    app = Application.__new__(Application)
    app.db = db
    publisher = FacebookPublisher(settings)
    waits = []
    page = SimpleNamespace(wait_for_timeout=waits.append)
    assert not turbo_enabled(settings)
    publisher._pause(page)
    assert sum(waits) >= 500
    app.action('/api/turbo', {'enabled': True})
    assert turbo_enabled(settings)
    waits.clear()
    publisher._pause(page, multiplier=2)
    assert waits == []
    assert turbo_enabled(Settings.from_root(tmp_path))
    publisher.control = lambda: (_ for _ in ()).throw(OperationCancelled())
    with pytest.raises(OperationCancelled): publisher._pause(page)
    publisher.control = None
    with pytest.raises(ValueError): app.action('/api/turbo', {'enabled': 'true'})
    app.action('/api/turbo', {'enabled': False})
    assert not turbo_enabled(settings)
    publisher._pause(page)
    assert waits


def test_turbo_packet_spacing_does_not_touch_explicit_schedules(tmp_path):
    db = Database(tmp_path / 'app.db')
    db.set_setting('turbo_mode', True)
    # Turbo spacing returns before issuing any scheduling query.
    db.execute = lambda *args: (_ for _ in ()).throw(AssertionError('Schedule changed'))
    _space_remaining_packets(db, SimpleNamespace(settings=SimpleNamespace(between_posts_seconds=60)), {'mode': 'joined_batch'})


def test_check_batch_reuses_browser_and_closes_on_cancel(tmp_path, monkeypatch):
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    launched, closed = [], []
    page = SimpleNamespace(url='https://facebook.com/groups/test', goto=lambda *args, **kwargs: None)
    context = SimpleNamespace(pages=[page], close=lambda: closed.append('context'))
    runtime = SimpleNamespace(stop=lambda: closed.append('runtime'))
    monkeypatch.setattr(publisher, 'launch', lambda: (launched.append(1) or runtime, context))
    monkeypatch.setattr(publisher, '_pause', lambda *args, **kwargs: None)
    monkeypatch.setattr(publisher, '_account_id', lambda _: 'A')
    monkeypatch.setattr(publisher, '_inspect_group_access', lambda _: {'state': 'blocked'})
    with pytest.raises(OperationCancelled):
        with publisher.group_check_session():
            publisher.inspect_group(page.url, expected_account_id='A')
            publisher.inspect_group(page.url, expected_account_id='A')
            assert len(launched) == 1
            assert closed == []
            raise OperationCancelled()
    assert closed == ['context', 'runtime']
    assert publisher._group_check_context is None
