import pytest
from playwright.sync_api import sync_playwright

from bike_publisher.facebook import FacebookPublisher, SessionStatus
from bike_publisher.account import save_account_status
from bike_publisher.db import Database


@pytest.mark.parametrize('counter',['40 — друзья','Friends · 40','40 znajomych'])
def test_profile_title_wins_over_friends_heading(counter):
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.set_content(f'<title>(9) Alex Example | Facebook</title><main><h1>{counter}</h1></main>')
        name,_,_=FacebookPublisher._read_profile_info(page,navigate=False)
        assert name=='Alex Example'
        browser.close()


def test_invalid_counter_does_not_survive_profile_refresh(tmp_path):
    db=Database(tmp_path/'app.db')
    db.set_setting('facebook_user',{'account_id':'A','browser_profile':'profile','name':'40 — друзья'})
    save_account_status(db,SessionStatus(True,'ok','A','40 — друзья'),'profile')
    assert not db.get_setting('facebook_user').get('name')


def test_profile_links_use_own_identity_not_friends_or_edit_button():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.route('https://www.facebook.com/**',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='''
            <title>(3) Facebook</title><main>
            <a href="/profile.php?id=123&sk=friends">40 — друзья</a>
            <a href="/profile.php?id=123">Редактировать профиль</a>
            <a href="/profile.php?id=999">Other Person</a>
            <h2><a href="/profile.php?id=123">Alex Example</a></h2></main>'''))
        page.goto('https://www.facebook.com/profile.php?id=123')
        assert FacebookPublisher._read_profile_info(page,navigate=False)[0]=='Alex Example'
        browser.close()
