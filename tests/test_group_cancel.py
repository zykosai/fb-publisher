import threading
import time
from types import SimpleNamespace

import pytest

from bike_publisher import webapp
from bike_publisher.db import Database
from bike_publisher.webapp import Application


def test_group_check_cancellation_releases_lock_and_is_not_error(tmp_path, monkeypatch):
    entered = threading.Event()

    class Publisher:
        control = None

    monkeypatch.setattr(webapp, 'FacebookPublisher', lambda _: Publisher())
    app = Application.__new__(Application)
    app.db = Database(tmp_path / 'app.db')
    app.settings = SimpleNamespace()
    app.operation = {}
    app.operation_lock = threading.Lock()

    def checking(publisher):
        app.operation['checked'] = 3
        entered.set()
        while True:
            publisher.control()
            time.sleep(.01)

    app.run_operation('Checking', checking, kind='group_check')
    assert entered.wait(3)
    app.action('/api/groups/check/cancel', {})
    assert app.operation_lock.acquire(timeout=3)
    app.operation_lock.release()
    assert not app.operation['busy']
    assert not app.operation['error']
    assert app.operation['message'].endswith('3')
    with pytest.raises(ValueError): app.action('/api/groups/check/cancel', {})


def test_cancel_does_not_cancel_login_or_publication(tmp_path):
    app = Application.__new__(Application)
    app.db = Database(tmp_path / 'app.db')
    app.operation = {'busy': True, 'kind': 'login'}
    app.operation_cancel = threading.Event()
    with pytest.raises(ValueError): app.action('/api/groups/check/cancel', {})
    assert not app.operation_cancel.is_set()
