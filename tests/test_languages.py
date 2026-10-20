"""All interface languages, RTL, persistence and user-content isolation."""
import ast
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from bike_publisher import webapp
from bike_publisher.db import Database, utc_now
from bike_publisher.joined_groups import save_snapshot, save_group_check


NEW_LANGUAGES = ('zh-CN','hi','es','ar','fr','bn','pt','ja')


def test_static_locale_catalogs_are_complete():
    root=Path(__file__).resolve().parents[1]/'web'
    rows=ast.literal_eval(re.search(r'const rows = (\[.*?\n  \]);',(root/'i18n.js').read_text(encoding='utf-8'),re.S)[1])
    catalogs=json.loads((root/'locales.js').read_text(encoding='utf-8').split('window.MARKET_LOCALES = ',1)[1].strip().removesuffix(';'))
    assert set(catalogs)==set(NEW_LANGUAGES)
    for language, catalog in catalogs.items():
        assert set(row[0] for row in rows) <= set(catalog),language
        assert all(isinstance(value,str) and value.strip() and '@@' not in value for value in catalog.values()),language
        for template in ('Фото {n}','{n} фото','{n} из {total}'):
            assert set(re.findall(r'\{\w+\}',template))==set(re.findall(r'\{\w+\}',catalog[template]))


def test_all_languages_on_mobile_keep_user_content_and_restore_selection(tmp_path,monkeypatch):
    db=Database(tmp_path/'app.db')
    db.set_setting('facebook_account_id','A')
    db.set_setting('facebook_user',{'account_id':'A','name':'Пользователь · User'})
    db.set_setting('facebook_session',{'message':'Вход подтверждён. Сессия готова для публикаций.'})
    db.execute('INSERT INTO bikes(external_id,name,description,owner_account_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',
        ('1','Мой товар · Product','Моё описание · Description','A',utc_now(),utc_now()))
    save_snapshot(db,'A',[{'name':'Моя группа · Group','url':'https://facebook.com/groups/test'}])
    gid=db.one('SELECT id FROM groups_')['id']
    save_group_check(db,'A',gid,{'state':'ready','joined':True})
    app=webapp.Application.__new__(webapp.Application)
    app.db,app.settings,app.key,app.operation=db,SimpleNamespace(),'languages-test',{}
    monkeypatch.setattr(webapp,'worker_state',lambda _:SimpleNamespace(running=False))
    server=webapp.ThreadingHTTPServer(('127.0.0.1',0),webapp.handler_for(app))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':390,'height':844})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/?key={app.key}')
            page.locator('.card').wait_for()
            assert page.locator('#language option').count()==11
            for language in ('en','pl',*NEW_LANGUAGES,'ru'):
                page.locator('#language').select_option(language)
                assert page.locator('html').get_attribute('lang')==language
                assert page.locator('html').get_attribute('dir')==('rtl' if language=='ar' else 'ltr')
                for section in ('bikes','groups','publish','history','profile'):
                    page.evaluate('(section)=>location.hash=section',section)
                    page.wait_for_timeout(40)
                    page.locator('#help-toggle').click()
                    assert page.locator('#help-popup').is_visible()
                    if language!='ru':
                        untranslated=page.evaluate('''() => {
                            const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT), found=[];
                            while(walker.nextNode()) {
                                const node=walker.currentNode, parent=node.parentElement;
                                if(parent?.checkVisibility() && !parent.closest('[data-user-content],script,style,textarea,option') && /[А-Яа-яЁё]/.test(node.nodeValue)) found.push(node.nodeValue.trim());
                            }
                            return found;
                        }''')
                        assert not untranslated,(language,section,untranslated)
                    page.keyboard.press('Escape')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(language,section)
                page.evaluate("location.hash='bikes'")
                page.locator('.card h2').wait_for()
                assert page.locator('.card h2').inner_text()=='Мой товар · Product'
                assert page.locator('.card .body p').inner_text()=='Моё описание · Description'
                assert page.evaluate("I18n.t('7 из 11')").find('из')<0 or language=='ru'
            page.locator('#language').select_option('ar')
            page.reload()
            page.locator('.card').wait_for()
            assert page.locator('#language').input_value()=='ar'
            assert page.locator('html').get_attribute('dir')=='rtl'
            page.locator('.card [data-action="edit"]').click()
            assert page.locator('#description').input_value()=='Моё описание · Description'
            page.locator('#add-heading').click()
            assert page.locator('#heading-fields').is_visible()
            page.locator('#remove-heading').click()
            assert page.locator('#heading-fields').is_hidden()
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
