from types import SimpleNamespace
import threading

import pytest

from bike_publisher.facebook import FacebookPublisher
from bike_publisher.webapp import Application


class LoginPage:
    url = 'https://www.facebook.com/'

    def __init__(self):
        self.waits = 0

    def goto(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, delay):
        self.waits += 1


def publisher_for(monkeypatch, page):
    publisher = FacebookPublisher(SimpleNamespace())
    closed = []
    context = SimpleNamespace(pages=[page], close=lambda: closed.append('context'))
    playwright = SimpleNamespace(stop=lambda: closed.append('playwright'))
    monkeypatch.setattr(publisher, 'launch', lambda **kwargs: (playwright, context))
    monkeypatch.setattr(publisher, '_account_id', lambda context: 'old' if page.waits < 2 else 'new')
    monkeypatch.setattr(publisher, '_read_profile_info', lambda *args, **kwargs: ('User', '', ''))
    return publisher, closed


def test_existing_session_does_not_close_switch_window(monkeypatch):
    page = LoginPage()
    publisher, closed = publisher_for(monkeypatch, page)

    def confirmed():
        assert not closed
        return page.waits >= 3

    assert publisher.login(confirmed=confirmed) == 'new'
    assert page.waits == 3
    assert publisher.last_session.account_id == 'new'
    assert closed == ['context', 'playwright']


def test_noninteractive_cli_login_keeps_previous_behaviour(monkeypatch):
    publisher, closed = publisher_for(monkeypatch, LoginPage())
    assert publisher.login() == 'old'
    assert closed == ['context', 'playwright']


def test_manual_close_shows_helpful_error(monkeypatch):
    page = LoginPage()
    publisher, closed = publisher_for(monkeypatch, page)

    def close_window(_):
        raise RuntimeError('Target page closed')

    page.wait_for_timeout = close_window
    with pytest.raises(RuntimeError, match='Окно входа закрыто'):
        publisher.login(confirmed=lambda: False)
    assert publisher.last_session is None
    assert closed == ['context', 'playwright']


def test_finish_only_confirms_active_login():
    app = Application.__new__(Application)
    app.db = None
    app.login_confirmation = threading.Event()
    app.operation = {'busy': True, 'kind': 'marketplace'}
    with pytest.raises(ValueError): app.action('/api/login/finish', {})
    assert not app.login_confirmation.is_set()
    app.operation = {'busy': True, 'kind': 'login'}
    assert app.action('/api/login/finish', {})['ok']
    assert app.login_confirmation.is_set()
