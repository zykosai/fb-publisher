import pytest
from playwright.sync_api import sync_playwright

from bike_publisher.config import Settings
from bike_publisher.control import OperationCancelled
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.group_join import finish_join


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser.new_page()
        browser.close()


@pytest.fixture
def publisher(tmp_path):
    return FacebookPublisher(Settings.from_root(tmp_path))


def test_join_waits_for_real_state_even_in_turbo(page, publisher):
    page.set_content('''<div role="main"><h1>Test group</h1><button id="join">Join group</button>
        <div role="tablist">Discussion</div></div>
        <script>setTimeout(()=>document.querySelector('#join').textContent='Joined',300)</script>''')
    result = finish_join(publisher, page, timeout_seconds=3)
    assert result['joined'] is True
    assert result['join_attempted'] is True


def test_optional_questions_can_be_skipped_without_answers(page, publisher):
    page.set_content('''<div role="main"><h1>Test group</h1><button id="join">Join group</button><div role="tablist">Discussion</div></div>
      <div role="dialog" id="questions"><h2>Membership questions</h2><textarea></textarea>
      <button onclick="document.querySelector('#questions').remove();document.querySelector('#join').textContent='Joined'">Not now</button></div>''')
    result = finish_join(publisher, page, timeout_seconds=2)
    assert result['joined'] is True
    assert result['join_actions'] == ['Not now']
    assert page.get_by_role('dialog').count() == 0


def test_mandatory_questions_are_reported_not_fabricated(page, publisher):
    page.set_content('''<div role="main"><button>Join group</button><div role="tablist">Discussion</div></div>
        <div role="dialog"><h2>Membership questions</h2><label>Where do you live?<textarea></textarea></label>
        <button onclick="window.sent=true">Submit</button></div>''')
    result = finish_join(publisher, page, timeout_seconds=2)
    assert result['state'] == 'questions'
    assert result['joined'] is None
    assert page.locator('textarea').input_value() == ''
    assert page.evaluate('window.sent === undefined')


def test_request_sent_is_pending_not_membership(page, publisher):
    page.set_content('<div role="main"><button>Запрос отправлен</button><div role="tablist">Обсуждение</div></div>')
    result = finish_join(publisher, page, timeout_seconds=2)
    assert result['state'] == 'pending'
    assert result['joined'] is False


def test_unknown_dialog_is_not_automatically_confirmed(page, publisher):
    page.set_content('<div role="main"><button>Join group</button></div><div role="dialog">An unrelated popup</div>')
    result = finish_join(publisher, page, timeout_seconds=.01)
    assert result['state'] == 'join_unconfirmed'
    assert result['joined'] is None


def test_join_confirmation_without_questions_and_cancellation(page, publisher):
    page.set_content('''<div role="main"><button id="join">Join group</button></div>
        <div role="dialog" id="confirm"><p>Join this group?</p><button onclick="document.querySelector('#confirm').remove();document.querySelector('#join').textContent='Joined'">Join group</button></div>''')
    assert finish_join(publisher, page, timeout_seconds=2)['joined'] is True
    publisher.control = lambda: (_ for _ in ()).throw(OperationCancelled())
    with pytest.raises(OperationCancelled): finish_join(publisher, page)


def test_restriction_in_feed_is_not_an_account_limit(page, publisher):
    page.set_content('''<div role="main"><button>Joined</button><div role="tablist">Discussion</div>
        <div role="feed"><div role="article">I was temporarily blocked yesterday.</div></div></div>''')
    assert finish_join(publisher, page, timeout_seconds=2)['joined'] is True


def test_real_limit_stops_join_and_includes_facebook_message(page, publisher):
    page.set_content('''<div role="main"><button>Join group</button></div>
        <div role="dialog">We limit how often you can do certain things on Facebook.</div>''')
    with pytest.raises(RuntimeError, match='limit how often'):
        finish_join(publisher, page, timeout_seconds=2)


def test_membership_is_preserved_when_warning_appears_after_success(page, publisher):
    page.set_content('''<div role="main"><button>В группе</button><div role="tablist">Обсуждение</div></div>
        <div role="dialog">We limit how often you can do certain things on Facebook.</div>''')
    result = finish_join(publisher, page, timeout_seconds=2)
    assert result['joined'] is True
    assert 'limit how often' in result['join_rate_limit']
