"""Browser regression: sales, publication choices and history on mobile/desktop."""
import threading
from types import SimpleNamespace

from PIL import Image
from playwright.sync_api import sync_playwright

from bike_publisher import webapp
from bike_publisher.db import Database, utc_now
from bike_publisher.joined_groups import save_group_check


def test_sales_and_history_in_browser(tmp_path, monkeypatch):
    db = Database(tmp_path / 'app.db')
    photo = tmp_path / 'bike.jpg'
    Image.new('RGB', (500, 350), '#63545d').save(photo)
    for number in (1, 2):
        bike = db.execute("INSERT INTO bikes(external_id,name,price,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (str(number), f'Велосипед №{number}', '1600', 'Описание велосипеда', utc_now(), utc_now()))
        db.execute('INSERT INTO photos(bike_id,path) VALUES(?,?)', (bike, str(photo)))
    group = db.execute("INSERT INTO groups_(name,url,created_at,updated_at) VALUES('Test group','https://facebook.com/groups/test',?,?)", (utc_now(), utc_now()))
    db.set_setting('facebook_account_id', 'ui-test-account')
    db.execute("UPDATE bikes SET owner_account_id='ui-test-account'")
    db.execute("UPDATE bikes SET source='facebook',source_listing_id='123' WHERE id=2")
    save_group_check(db, 'ui-test-account', group, {'state': 'moderation_limit', 'pending_count': 21, 'rejected_count': 4, 'detail': 'Лимит публикаций на проверке'})
    db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,created_at,updated_at) VALUES(1,?,?,'published',?,?)", (group, utc_now(), utc_now(), utc_now()))
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.key, app.settings, app.operation = db, 'ui-test-key', SimpleNamespace(), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    server = webapp.ThreadingHTTPServer(('127.0.0.1', 0), webapp.handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('dialog', lambda dialog: dialog.accept())
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/?key={app.key}')
            page.get_by_role('button', name='Турбо: выкл.', exact=True).click()
            page.get_by_role('button', name='Турбо: вкл.', exact=True).wait_for()
            assert db.get_setting('turbo_mode') is True
            page.get_by_role('button', name='Турбо: вкл.', exact=True).click()
            page.get_by_role('button', name='Турбо: выкл.', exact=True).wait_for()
            assert db.get_setting('turbo_mode') is False
            page.get_by_role('button', name='Отметить SOLD').first.click()
            assert page.locator('.import-label').inner_text() == 'Import FB'
            page.locator('.sold-label').wait_for()
            assert page.get_by_role('button', name='Отменить SOLD').count() == 1
            page.screenshot(path=str(tmp_path / 'catalog-desktop.png'), full_page=True)
            page.evaluate("location.hash='publish'")
            page.locator('[data-pick="bike"]').wait_for()
            assert page.locator('[data-pick="bike"]').count() == 1
            assert page.locator('[data-pick="bike"]').get_attribute('value') == '2'
            page.evaluate("location.hash='profile'")
            page.locator('.sales-panel').wait_for()
            assert '1' == page.locator('.sales-panel .stat').first.inner_text().split('\n')[0]
            assert '1\u00a0600' in page.locator('.sales-panel').inner_text()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(tmp_path / 'profile-phone.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("location.hash='groups'")
            page.get_by_text('На проверке · 21', exact=True).wait_for()
            assert page.get_by_text('Отклонено · 4', exact=True).count() == 1
            # Polls must update badges even while searching, without losing input.
            page.locator('#group-filter').fill('Test')
            page.locator('#group-filter').evaluate('el => el.setSelectionRange(2, 2)')
            save_group_check(db, 'ui-test-account', group, {
                'state': 'ready', 'joined': True, 'detail': 'Проверено заново'})
            page.get_by_text('Можно · моя группа', exact=True).wait_for(timeout=10000)
            assert page.locator('#group-filter').input_value() == 'Test'
            assert page.locator('#group-filter').evaluate('el => document.activeElement === el && el.selectionStart === 2')
            page.locator('[data-pick="marked"]').check()
            page.get_by_role('button', name='Удалить отмеченные', exact=True).click()
            page.get_by_text('Групп пока нет.', exact=True).wait_for()
            page.get_by_role('button', name='Вернуть удалённые · 1', exact=True).click()
            page.get_by_text('Можно · моя группа', exact=True).wait_for()
            assert page.get_by_role('button', name='Вернуть удалённые · 0', exact=True).is_disabled()
            page.locator('.group-badges').scroll_into_view_if_needed()
            page.evaluate('window.scrollBy(0, 400)')
            page.screenshot(path=str(tmp_path / 'groups-phone.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("location.hash='history'")
            page.get_by_role('button', name='Очистить историю').click()
            page.get_by_text('Публикаций пока нет.').wait_for()
            page.evaluate("location.hash='bikes'")
            page.get_by_role('button', name='Отменить SOLD').click()
            page.locator('.sold-label').wait_for(state='detached')
            assert page.locator('.sold-label').count() == 0
            for language, title in [('en', 'Your products'), ('pl', 'Twoje produkty'), ('ru', 'Твои товары')]:
                page.locator('#language').select_option(language)
                page.get_by_role('heading', name=title, exact=True).wait_for()
                assert page.locator('.card h2').first.inner_text() == 'Велосипед №1'
                assert page.locator('.card .body p').first.inner_text() == 'Описание велосипеда'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if language != 'ru':
                    for section in ['groups', 'publish', 'history', 'profile', 'bikes']:
                        page.evaluate('(section)=>location.hash=section', section)
                        page.wait_for_timeout(70)
                        untranslated = page.evaluate('''() => {
                            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                            const result = [];
                            while (walker.nextNode()) {
                                const node = walker.currentNode, parent = node.parentElement;
                                if (parent && parent.checkVisibility() && !parent.closest('[data-user-content],script,style,textarea,option') && /[А-Яа-яЁё]/.test(node.nodeValue)) result.push(node.nodeValue.trim());
                            }
                            return result;
                        }''')
                        assert not untranslated, (language, section, untranslated)
            page.locator('#language').select_option('en')
            page.reload()
            page.get_by_role('heading', name='Your products', exact=True).wait_for()
            page.get_by_role('button', name='Edit', exact=True).first.click()
            assert page.locator('#editor-title').inner_text() == 'Велосипед №1'
            assert page.locator('#description').input_value() == 'Описание велосипеда'
            page.get_by_role('button', name='Post preview', exact=True).click()
            page.locator('#post-preview').wait_for(state='visible')
            assert page.locator('#post-preview strong').count() == 0
            assert 'Описание велосипеда' in page.locator('#post-preview').inner_text()
            page.get_by_role('button', name='Add heading', exact=True).click()
            page.locator('#post-heading').fill('Custom Winora heading')
            page.get_by_role('button', name='Post preview', exact=True).click()
            page.wait_for_function("() => document.querySelector('#post-preview strong')?.textContent === 'Custom Winora heading'")
            page.get_by_role('button', name='Save', exact=True).click()
            page.wait_for_function("() => document.querySelector('#notice')?.textContent.includes('saved')")
            assert db.one('SELECT post_heading FROM bikes WHERE id=1')['post_heading'] == 'Custom Winora heading'
            page.get_by_role('button', name='Close', exact=True).click()
            page.get_by_role('button', name='Edit', exact=True).first.click()
            assert page.locator('#post-heading').input_value() == 'Custom Winora heading'
            page.get_by_role('button', name='Remove heading', exact=True).click()
            assert page.locator('#post-heading').is_hidden()
            with page.expect_response(lambda response: response.url.endswith('/api/bikes/1') and response.request.method == 'POST'):
                page.get_by_role('button', name='Save', exact=True).click()
            page.wait_for_function("() => document.querySelector('#notice')?.textContent.includes('saved')")
            assert db.one('SELECT post_heading FROM bikes WHERE id=1')['post_heading'] == ''
            page.get_by_role('button', name='Close', exact=True).click()
            page.get_by_role('button', name='Edit', exact=True).first.click()
            assert page.locator('#post-heading').is_hidden()
            page.get_by_role('button', name='Post preview', exact=True).click()
            page.locator('#post-preview').wait_for(state='visible')
            assert page.locator('#post-preview strong').count() == 0
            page.screenshot(path=str(tmp_path / 'products-phone-en.png'), full_page=True)
            page.get_by_role('button', name='Close', exact=True).click()
            page.get_by_text('Bulk description editing', exact=True).click()
            page.locator('#bulk-find').fill('Описание')
            page.locator('#bulk-replacement').fill('Товар')
            page.get_by_role('button', name='Preview replacements', exact=True).click()
            page.get_by_role('button', name='Apply changes', exact=True).wait_for()
            assert db.one('SELECT description FROM bikes WHERE id=1')['description'] == 'Описание велосипеда'
            page.get_by_role('button', name='Apply changes', exact=True).click()
            page.wait_for_function("() => document.querySelector('.card .body p')?.textContent === 'Товар велосипеда'")
            page.get_by_text('Bulk description editing', exact=True).click()
            page.get_by_role('button', name='Undo last bulk edit', exact=True).click()
            page.wait_for_function("() => document.querySelector('.card .body p')?.textContent === 'Описание велосипеда'")
            app.operation = {'busy': True, 'kind': 'group_check', 'message': '', 'error': ''}
            app.operation_cancel = threading.Event()
            page.evaluate("location.hash='groups'")
            page.get_by_role('button', name='Cancel check', exact=True).click()
            assert app.operation_cancel.wait(3)
            app.operation = {'busy': True, 'kind':'login', 'message':'', 'error':''}
            app.login_confirmation = threading.Event()
            page.evaluate("location.hash='profile'")
            page.get_by_role('button', name='Done, verify login', exact=True).click()
            assert app.login_confirmation.wait(3)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
