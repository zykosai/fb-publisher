from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from bike_publisher import webapp
from bike_publisher.account import save_account_status
from bike_publisher.config import Settings
from bike_publisher.db import Database, utc_now
from bike_publisher.delivery import recover_interrupted
from bike_publisher.facebook import SessionStatus
from bike_publisher.joined_groups import save_snapshot, save_group_check
from bike_publisher.queue import get_next_job
from bike_publisher.workspaces import ensure_workspace, create_workspace, selected_database, workspace_settings, select_workspace, validate_identity


@pytest.fixture
def profiles(tmp_path):
    settings = Settings.from_root(tmp_path)
    db = Database(settings.db_path)
    save_account_status(db, SessionStatus(True, 'ok', 'A', 'User A'), str(settings.browser_profile))
    ensure_workspace(db, settings)
    a = db.get_setting('active_account_slot')
    b = create_workspace(db, 'Services B', 'services')
    bdb = selected_database(db, b)
    save_account_status(bdb, SessionStatus(True, 'ok', 'B', 'User B'), str(workspace_settings(settings, db, b).browser_profile))
    return db, settings, a, b


def test_profiles_have_distinct_paths_and_fixed_account_context(profiles):
    db, settings, a, b = profiles
    adb, bdb = selected_database(db, a), selected_database(db, b)
    sa, sb = workspace_settings(settings, db, a), workspace_settings(settings, db, b)
    assert sa.browser_profile == settings.browser_profile
    assert sb.browser_profile != sa.browser_profile
    assert sa.worker_dir != sb.worker_dir
    assert sa.expected_account_id == 'A' and sb.expected_account_id == 'B'
    adb.set_setting('publication_paused', False)
    bdb.set_setting('publication_paused', True)
    adb.set_setting('turbo_mode', True)
    select_workspace(db, b)
    assert adb.get_setting('facebook_account_id') == 'A'
    assert bdb.get_setting('facebook_account_id') == 'B'
    assert not adb.get_setting('publication_paused')
    assert bdb.get_setting('publication_paused')
    assert not bdb.get_setting('turbo_mode', False)
    assert adb.get_setting('facebook_user')['name'] == 'User A'
    assert bdb.get_setting('facebook_user')['name'] == 'User B'
    assert db.get_setting('facebook_account_id') == 'A'


def test_duplicate_or_wrong_facebook_cannot_overwrite_profile(profiles):
    db, settings, a, b = profiles
    bdb = selected_database(db, b)
    with pytest.raises(ValueError): validate_identity(bdb, 'A')
    assert bdb.get_setting('facebook_account_id') == 'B'
    assert bdb.get_setting('facebook_session')['state'] == 'wrong_account'
    slot = create_workspace(db, 'Duplicate A')
    with pytest.raises(ValueError): validate_identity(selected_database(db, slot), 'A')
    assert selected_database(db, slot).get_setting('facebook_account_id') == ''


def test_recovery_and_queue_cannot_touch_running_other_account(profiles):
    db, settings, a, b = profiles
    group = {'name':'Shared','url':'https://facebook.com/groups/shared'}
    for account in ('A', 'B'):
        save_snapshot(db, account, [group])
    gid = db.one('SELECT id FROM groups_')['id']
    jobs = {}
    for account in ('A', 'B'):
        product = db.execute('INSERT INTO bikes(external_id,name,owner_account_id,created_at,updated_at) VALUES(?,?,?,?,?)', (account, account, account, utc_now(), utc_now()))
        jobs[account] = db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,mode,created_at,updated_at) VALUES(?,?,?,'running','joined_batch',?,?)", (product,gid,utc_now(),utc_now(),utc_now()))
    recover_interrupted(selected_database(db, b))
    assert db.one('SELECT status FROM queue WHERE id=?',(jobs['A'],))['status'] == 'running'
    assert db.one('SELECT status FROM queue WHERE id=?',(jobs['B'],))['status'] == 'failed'
    adb = selected_database(db, a)
    adb.set_setting('publication_paused', False)
    db.execute("UPDATE queue SET status='pending'")
    assert get_next_job(adb)['id'] == jobs['A']


def test_services_creation_plan_deletion_and_cross_account_access(profiles, monkeypatch):
    db, settings, a, b = profiles
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.settings, app.operation, app.operation_lock = db, settings, {}, threading.Lock()
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    identity = app.action('/api/items/create', {'name':'Repair service','description':'Honest description','price':'','post_heading':'Repairs'})['id']
    assert app.state()['bikes'][0]['name'] == 'Repair service'
    save_snapshot(selected_database(db,b), 'B', [{'name':'Group B','url':'https://facebook.com/groups/b'}])
    group = db.one('SELECT id FROM groups_')['id']
    save_group_check(db,'B',group,{'state':'ready','joined':True})
    app.action('/api/plan', {'bikes':[identity],'groups':[group],'delay':0})
    assert db.one('SELECT status FROM queue')['status'] == 'pending'
    app.action('/api/accounts/select', {'id':a})
    assert app.state()['bikes'] == []
    with pytest.raises(ValueError): app.action(f'/api/bikes/{identity}/delete',{})
    app.action('/api/accounts/select', {'id':b})
    app.action(f'/api/bikes/{identity}/delete',{})
    assert app.state()['bikes'] == []
    assert db.one('SELECT status FROM queue')['status'] == 'cancelled'
    assert db.one('SELECT status FROM bikes')['status'] == 'deleted'


def test_start_and_pause_two_accounts_are_independent(profiles, monkeypatch):
    db, settings, a, b = profiles
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.settings, app.operation, app.operation_lock = db, settings, {}, threading.Lock()
    launched = []
    monkeypatch.setattr(webapp, 'ensure_worker_running', lambda s: launched.append(s.account_slot))
    app.action('/api/accounts/start', {'id':a})
    app.action('/api/accounts/start', {'id':b})
    assert launched == [a,b]
    app.action('/api/accounts/pause', {'id':a})
    assert selected_database(db,a).get_setting('publication_paused') is True
    assert selected_database(db,b).get_setting('publication_paused') is False
    with pytest.raises(ValueError): app.action('/api/accounts/start', {'id':''})


def test_clear_history_preserves_other_account_audit(profiles):
    from bike_publisher.catalog import clear_history
    db, settings, a, b = profiles
    selected_database(db,a).log('test','A event')
    selected_database(db,b).log('test','B event')
    clear_history(selected_database(db,b))
    assert [row['message'] for row in db.all('SELECT message FROM audit_log')] == ['A event']


def test_explicit_stop_pauses_every_account_before_stopping_workers(profiles, monkeypatch):
    from bike_publisher import lifecycle
    db, settings, a, b = profiles
    for slot in (a,b): selected_database(db,slot).set_setting('publication_paused',False)
    stopped=[]
    def stop(config):
        assert all(selected_database(db,slot).get_setting('publication_paused') for slot in (a,b))
        stopped.append(config.account_slot)
    monkeypatch.setattr(lifecycle,'stop_worker',stop)
    lifecycle.stop_all_workers(settings)
    assert set(stopped[:-1]) == {a,b}
    assert stopped[-1] == '' and len(stopped) == 3


def test_two_real_workers_run_together_without_facebook_actions(profiles):
    import time
    from bike_publisher.worker_process import ensure_worker_running, stop_worker, worker_state
    db, settings, a, b = profiles
    # Real code, isolated temporary database, fake identities, empty queues and paused workers.
    settings = replace(settings, root=Path(__file__).resolve().parents[1])
    sa, sb = workspace_settings(settings, db, a), workspace_settings(settings, db, b)
    for slot in (a,b): selected_database(db,slot).set_setting('publication_paused',True)
    try:
        wa, _ = ensure_worker_running(sa)
        wb, _ = ensure_worker_running(sb)
        assert wa.pid != wb.pid
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            logs = [settings.data_dir/'logs'/f'worker-{slot}.log' for slot in (a,b)]
            if all(p.exists() and 'Worker готов' in p.read_text(encoding='utf-8') for p in logs): break
            time.sleep(.1)
        assert all(p.exists() and 'Worker готов' in p.read_text(encoding='utf-8') for p in logs)
        assert worker_state(sa).running and worker_state(sb).running
        stop_worker(sa)
        assert worker_state(sb).running
    finally:
        stop_worker(sa)
        stop_worker(sb)


def test_account_switch_and_service_editor_in_browser(profiles, monkeypatch):
    from playwright.sync_api import sync_playwright
    db, settings, a, b = profiles
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.settings, app.key = db, settings, 'workspace-browser-test'
    app.operation, app.operation_lock = {}, threading.Lock()
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    server = webapp.ThreadingHTTPServer(('127.0.0.1',0),webapp.handler_for(app))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome',headless=True)
            page = browser.new_page(viewport={'width':390,'height':844})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('dialog',lambda dialog:dialog.accept())
            page.goto(f'http://127.0.0.1:{server.server_port}/?key={app.key}')
            page.get_by_role('heading',name='Твои услуги',exact=True).wait_for()
            page.evaluate("location.hash='profile'")
            page.get_by_role('heading',name='Сохранённые аккаунты',exact=True).wait_for()
            assert page.locator('.sales-panel').count() == 0
            page.evaluate("location.hash='bikes'")
            page.get_by_role('button',name='Добавить объявление',exact=True).click()
            page.locator('#product-name').fill('Repair service')
            page.locator('#description').fill('Description for account B')
            page.get_by_role('button',name='Сохранить',exact=True).click()
            page.wait_for_function("() => document.querySelector('#editor-title')?.textContent === 'Repair service'")
            page.locator('#close-editor').click()
            page.wait_for_function("() => document.querySelector('.card h2')?.textContent === 'Repair service'")
            page.evaluate("location.hash='profile'")
            page.locator(f'[data-action="account-select"][data-id="{a}"]').click()
            page.wait_for_function("() => document.querySelector('#active-account')?.textContent === 'User A'")
            page.evaluate("location.hash='bikes'")
            page.get_by_role('heading',name='Твои товары',exact=True).wait_for()
            assert page.locator('.card').count()==0
            page.evaluate("location.hash='profile'")
            page.locator(f'[data-action="account-select"][data-id="{b}"]').click()
            page.wait_for_function("() => document.querySelector('#active-account')?.textContent === 'Services B'")
            page.evaluate("location.hash='bikes'")
            page.get_by_role('button',name='Удалить из каталога',exact=True).click()
            page.wait_for_function("() => !document.querySelector('.card')")
            page.evaluate("location.hash='publish'")
            page.get_by_role('heading',name='1. Услуги',exact=True).wait_for()
            assert 'товар' not in page.locator('#content').inner_text().lower()
            page.evaluate("location.hash='profile'")
            page.locator('#account-preset').select_option('creator')
            page.get_by_role('button',name='Сохранить профиль',exact=True).click()
            page.wait_for_function("() => document.querySelector('#catalog-nav')?.textContent === 'Контент'")
            page.evaluate("location.hash='publish'")
            for language, title in [('ru','1. Контент'),('en','1. Content'),('pl','1. Treści')]:
                page.locator('#language').select_option(language)
                page.get_by_role('heading',name=title,exact=True).wait_for()
                assert all(word not in page.locator('#content').inner_text().lower() for word in ['товар','products','produkty'])
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_drag_selection_help_and_marketplace_fold(profiles, monkeypatch):
    from playwright.sync_api import sync_playwright
    db, settings, a, b = profiles
    select_workspace(db,a)
    db.set_setting('marketplace_listings:A',[{'name':'Listing','url':'https://facebook.com/marketplace/item/123'}])
    save_snapshot(db,'A',[{'name':f'Group {n}','url':f'https://facebook.com/groups/test{n}'} for n in range(7)])
    for group in db.all('SELECT id FROM groups_'):
        save_group_check(db,'A',group['id'],{'state':'ready','joined':True})
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.settings, app.key, app.operation = db, settings, 'ui-polish-key', {}
    monkeypatch.setattr(webapp,'worker_state',lambda _:SimpleNamespace(running=False))
    server=webapp.ThreadingHTTPServer(('127.0.0.1',0),webapp.handler_for(app))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1280,'height':1000})
            page.goto(f'http://127.0.0.1:{server.server_port}/?key={app.key}')
            page.get_by_role('heading',name='Твои товары',exact=True).wait_for()
            assert not page.locator('.marketplace').evaluate('(el)=>el.open')
            page.locator('.marketplace > summary').click()
            page.locator('#language').select_option('en')
            assert page.locator('.marketplace').evaluate('(el)=>el.open')
            page.locator('.marketplace > summary').click()
            page.locator('#language').select_option('ru')
            assert not page.locator('.marketplace').evaluate('(el)=>el.open')
            assert page.locator('#content .help-panel').count()==0
            page.locator('#help-toggle').click()
            assert page.locator('#help-popup').is_visible()
            page.keyboard.press('Escape')
            assert page.locator('#help-popup').is_hidden()
            page.evaluate("location.hash='profile'")
            page.locator('.accounts-panel > summary').click()
            assert not page.locator('.accounts-panel').evaluate('(el)=>el.open')
            page.locator('#language').select_option('en')
            assert not page.locator('.accounts-panel').evaluate('(el)=>el.open')
            page.reload()
            page.locator('.accounts-panel').wait_for()
            assert not page.locator('.accounts-panel').evaluate('(el)=>el.open')
            page.locator('.accounts-panel > summary').click()
            page.locator('.account-create > summary').click()
            page.locator('#language').select_option('ru')
            assert page.locator('.account-create').evaluate('(el)=>el.open')
            save=page.locator('[data-action="account-save"]').bounding_box()
            add=page.locator('.account-create > summary').bounding_box()
            name=page.locator('.account-create label').first.bounding_box()
            assert add['y'] >= save['y']+save['height']+16
            assert name['y'] >= add['y']+add['height']+12
            page.evaluate("location.hash='groups'")
            rows=page.locator('input[data-pick="marked"]')
            rows.first.wait_for()
            def drag(start,end):
                first,last=rows.nth(start).bounding_box(),rows.nth(end).bounding_box()
                page.mouse.move(first['x']+8,first['y']+8)
                page.mouse.down()
                page.mouse.move(last['x']+8,last['y']+8,steps=12)
                page.wait_for_timeout(60)
                page.mouse.up()
            drag(0,3)
            assert page.locator('input[data-pick="marked"]:checked').count()==4
            drag(1,2)
            assert page.locator('input[data-pick="marked"]:checked').count()==2
            # Ordinary mouse clicks and keyboard toggling still work.
            rows.nth(4).click()
            assert page.locator('input[data-pick="marked"]:checked').count()==3
            rows.nth(5).focus()
            page.keyboard.press('Space')
            assert page.locator('input[data-pick="marked"]:checked').count()==4
            page.evaluate("location.hash='publish'")
            choices=page.locator('input[data-pick="group"]')
            choices.first.wait_for()
            # Clear preselected groups using the keyboard, then paint and persist.
            for choice in choices.all():
                if choice.is_checked():
                    choice.focus()
                    page.keyboard.press('Space')
            rows=choices
            drag(0,2)
            assert page.locator('input[data-pick="group"]:checked').count()==3
            page.wait_for_function("() => !document.querySelector('#notice')?.textContent.includes('Ошибка')")
            import time
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if db.one("SELECT COUNT(*) n FROM joined_groups WHERE account_id='A' AND allowed=1")['n']==3: break
                time.sleep(.05)
            assert db.one("SELECT COUNT(*) n FROM joined_groups WHERE account_id='A' AND allowed=1")['n']==3
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#help-toggle').click()
            assert page.locator('#help-popup').is_visible()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
