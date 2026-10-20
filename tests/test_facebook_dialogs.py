import base64
import re
from io import BytesIO

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from bike_publisher.config import Settings
from bike_publisher.facebook import FacebookPublisher


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # A secure loopback origin enables crypto.subtle. All requests are
        # intercepted: these tests never contact Facebook or the real panel.
        page.route("http://127.0.0.1:12345/**", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
        page.goto("http://127.0.0.1:12345/")
        yield page
        browser.close()


def test_group_access_ignores_other_peoples_posts_and_hidden_content(page, tmp_path):
    page.set_content('''<div role="main">
        <button>В группе</button>
        <div>Ожидает подтверждения администратора</div><div>21 публикация</div>
        <div>Достигнуто ограничение по контенту на рассмотрении в этой группе.</div>
        <div role="feed"><div role="article">Вас заблокировали в этой группе</div></div>
        <div hidden>Отклонено 99</div>
        <div role="navigation">Declined posts (82)</div>
    </div>''')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    result = publisher._inspect_group_access(page, composer=True)
    assert result['state'] == 'moderation_limit'
    assert result['pending_count'] == 21
    assert result['rejected_count'] is None
    assert result['joined'] is True


def test_group_join_button_is_not_proof_of_expulsion(page, tmp_path):
    page.set_content('<div role="main"><button>Вступить в группу</button></div>')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    result = publisher._inspect_group_access(page)
    assert result['state'] == 'not_member'
    assert result['joined'] is False


def test_native_bold_heading_preserves_complete_description(page):
    page.set_content('<div role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>')
    textbox = page.get_by_role('textbox')
    text = 'Rower miejski · 400 zł\n\nWidoczne rysy.\n📍 Środa Wielkopolska'
    assert FacebookPublisher._fill_post_text(textbox, text, bold_heading=True) is True
    assert textbox.inner_text().replace('\n\n\n', '\n\n') == text
    assert textbox.locator('b,strong').first.inner_text() == text.splitlines()[0]
    assert 'Widoczne rysy' not in textbox.locator('b,strong').first.inner_text()


def test_plain_editor_and_unsupported_bold_do_not_lose_text(page):
    page.set_content('<textarea></textarea><div role="textbox" contenteditable="true" style="white-space:pre-wrap" '
        'onkeydown="if(event.ctrlKey && event.key.toLowerCase()===\'b\') event.preventDefault()"></div>')
    text = 'Cena: 100 zł\n\nOpis bez zmian.'
    assert FacebookPublisher._fill_post_text(page.locator('textarea'), text, bold_heading=True) is False
    assert page.locator('textarea').input_value() == text
    assert FacebookPublisher._fill_post_text(page.locator('[contenteditable]'), text, bold_heading=True) is False
    assert page.locator('[contenteditable]').inner_text().replace('\n\n\n', '\n\n') == text


def test_missing_heading_never_formats_first_description_line(page):
    page.set_content('<div role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>')
    textbox=page.get_by_role('textbox')
    text='Na sprzedaż rower miejski.\n\nCena: 400 zł'
    assert FacebookPublisher._fill_post_text(textbox,text) is False
    assert textbox.locator('b,strong').count()==0
    assert textbox.inner_text().replace('\n\n\n','\n\n')==text


@pytest.mark.parametrize('member', ['В группе', 'Joined', 'Należysz do grupy'])
def test_membership_ignores_recommended_groups(page, tmp_path, member):
    page.set_content(f'''<div role="main"><h1>Target group</h1>
        <button>{member}</button><div role="tablist">Discussion</div>
        <aside><button>Присоединиться к группе</button></aside>
        <div role="feed"><button>Join group</button></div></div>''')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    result = publisher._inspect_group_access(page, composer=True)
    assert result['joined'] is True
    assert result['state'] == 'ready'
    assert publisher._group_join_button(page) is None


def test_public_composer_and_other_groups_membership_are_not_membership(page, tmp_path):
    page.set_content('''<div role="main"><h1>Target group</h1>
        <button>Присоединиться к группе</button><div role="tablist">Discussion</div>
        <button>В группе</button><button>Напишите что-нибудь...</button></div>''')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    result = publisher._inspect_group_access(page, composer=True)
    assert result['joined'] is False
    assert result['state'] == 'not_member'


def test_eight_photos_ignore_duplicate_layers_and_hidden_composer(page, tmp_path):
    paths, tiles = [], []
    for index in range(8):
        data = BytesIO()
        Image.new("RGB", (120, 120), (index * 30, 80, 100)).save(data, "PNG")
        path = tmp_path / f"{index}.png"
        path.write_bytes(data.getvalue())
        paths.append(str(path))
        src = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
        tiles.append(f'<div draggable="true"><img src="{src}"><img src="{src}"></div>')
    page.set_content('<form><div role="dialog" aria-label="Создание публикации">'
                     '<button type="button" onclick="document.getElementById(\'editor\').hidden=false">Редактировать всё</button>'
                     + '<img width="100" height="100">' * 13
                     + '<div id="editor" hidden role="dialog" aria-label="Фото/видео">'
                     + "".join(tiles)
                     + '<button type="button" onclick="document.getElementById(\'editor\').hidden=true">Готово</button></div></div></form>')
    page.get_by_role('button', name='Редактировать всё').click()
    assert page.locator('#editor').is_visible(), page.content()[:1000]
    assert page.get_by_role('dialog', name='Фото/видео', exact=True).count() == 1
    assert page.locator('#editor [draggable="true"]').count() == 8
    named = page.get_by_role('dialog', name=re.compile(r'^(?:Фото\/видео|Photo\/video|Zdjęcia i filmy)$', re.I), exact=True)
    assert named.count() == 1
    assert named.locator('[draggable="true"]').count() == 8
    assert FacebookPublisher._find_photo_editor(page, expected_images=8, timeout_ms=1000) is not None
    FacebookPublisher._verify_photo_order(page, paths)
    assert page.locator("#editor").is_hidden()
    # Order verification remains mandatory, including the first attachment.
    with pytest.raises(RuntimeError, match="Порядок фотографий"):
        FacebookPublisher._verify_photo_order(page, paths[::-1])


@pytest.mark.parametrize('header_only', [False, True])
def test_group_picker_does_not_touch_composer_checkbox(page, tmp_path, header_only):
    html = '''<form><div role="dialog" aria-label="Создание публикации">
        <input id="anonymous" type="checkbox" checked>
        <button type="button" onclick="document.getElementById('picker').hidden=false">Добавить группы</button>
        <div id="picker" hidden role="dialog" aria-label="Добавить группы">
          <p>Выберите для публикации не более 9 групп, в которых участвуете.</p>
          <div id="chips"></div>
          <label><span>Group A</span><input id="a" type="checkbox" onchange="document.getElementById('chips').innerHTML += '<span>Group A</span>'"></label>
          <label><span>Group B</span><input id="b" type="checkbox" onchange="document.getElementById('chips').innerHTML += '<span>Group B</span>'"></label>
          <button type="button" onclick="document.getElementById('picker').hidden=true">Готово</button>
        </div></div></form>'''
    if header_only:
        html = html.replace('<div id="picker" hidden role="dialog" aria-label="Добавить группы">', '<div id="picker" hidden><div role="dialog" aria-label="Добавить группы">Добавить группы</div>')
    page.set_content(html)
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    recipients = [{"id": 1, "group_name": "Primary"}, {"id": 2, "group_name": "Group A"}, {"id": 3, "group_name": "Group B"}]
    selected = publisher._add_group_recipients(page, page.get_by_role("dialog", name="Создание публикации"), recipients)
    assert selected == recipients
    assert page.locator("#anonymous").is_checked()
    assert page.locator("#a").is_checked() and page.locator("#b").is_checked()
    assert page.locator("#picker").is_hidden()


def test_photo_editor_header_and_grid_are_siblings(page):
    page.set_content('''<form><div contenteditable="true">Description</div>
        <div id="photo-panel"><div role="dialog" aria-label="Фото/видео">Фото/видео</div>
          <div draggable="true"><img><img></div><div draggable="true"><img><img></div>
          <button type="button">Готово</button></div></form>''')
    root = FacebookPublisher._find_photo_editor(page, expected_images=2, timeout_ms=1000)
    assert root is not None
    assert root.get_attribute('id') == 'photo-panel'
    assert root.locator('[draggable="true"]').count() == 2
    assert root.locator('[contenteditable="true"]').count() == 0


def test_avatar_labels_do_not_hide_groups_from_picker(page, tmp_path):
    page.set_content('''<div role="dialog" aria-label="Создание публикации">
        <button onclick="document.getElementById('picker').hidden=false">Добавить группы</button>
        <div id="picker" hidden role="dialog" aria-label="Добавить группы">
          <p>Выберите не более 9 групп</p>
          <label>Ogłoszenia Śrem i okolice<input type="checkbox"></label>
          <button onclick="document.getElementById('picker').hidden=true">Готово</button>
        </div></div>''')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    recipients = [{'id': 1, 'group_name': 'Primary'}, {'id': 2, 'group_name': 'Фото профиля Ogłoszenia Śrem i okolice'}]
    assert publisher._add_group_recipients(page, page.get_by_role('dialog', name='Создание публикации'), recipients) == recipients


def test_virtualized_picker_scrolls_beyond_first_eight_steps(page, tmp_path):
    page.set_content('''<div role="dialog" aria-label="Создание публикации">
        <button onclick="document.getElementById('picker').hidden=false; render()">Добавить группы</button>
        <div id="picker" hidden role="dialog" aria-label="Добавить группы">
          <p>Выберите не более 9 групп</p>
          <div id="scroller" style="height:200px;overflow-y:auto"><div style="height:3500px;position:relative" id="rows"></div></div>
          <button onclick="document.getElementById('picker').hidden=true">Готово</button>
        </div></div><script>
          let chosen=false;
          function render(){
            const start=Math.floor(document.getElementById('scroller').scrollTop/70);
            document.getElementById('rows').innerHTML=Array.from({length:4},(_,i)=>start+i).filter(i=>i<50).map(i=>
              `<label style="position:absolute;top:${i*70}px;height:60px">Group ${i}<input type="checkbox" ${i===45&&chosen?'checked':''} onchange="chosen=this.checked"></label>`).join('');
          }
          document.getElementById('scroller').addEventListener('scroll',render);
        </script>''')
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    recipients = [{'id': 1, 'group_name': 'Primary'}, {'id': 2, 'group_name': 'Group 45'}]
    assert publisher._add_group_recipients(page, page.get_by_role('dialog', name='Создание публикации'), recipients) == recipients
    assert page.evaluate('chosen')


def test_missing_recipient_remains_deferred_for_next_packet(tmp_path):
    class Publisher(FacebookPublisher):
        def publish(self, job, photos, **kwargs):
            self._missing_recipients = [kwargs['recipients'][1]]
            return {1: ('sent', '', ''), 2: ('deferred', '', '')}

    publisher = Publisher(Settings.from_root(tmp_path))
    receipt = publisher.publish_batch([{'id': 1, 'group_url': 'primary'}, {'id': 2}], [])
    assert receipt[2][0] == 'deferred'


def test_notifications_scroll_once_as_a_list_not_by_opening_groups(page, tmp_path):
    html = '''<button onclick="document.getElementById('panel').hidden=false; render()">Уведомления</button>
        <div id="panel" hidden style="width:330px"><div id="scroller" style="height:200px;overflow:auto">
          <div id="rows" style="height:1750px;position:relative"></div></div></div>
        <script>
        function render(){
          const start=Math.floor(document.getElementById('scroller').scrollTop/70);
          document.getElementById('rows').innerHTML=Array.from({length:5},(_,i)=>start+i).filter(i=>i<25).map(i=>
            `<a style="position:absolute;top:${i*70}px;height:60px" href="https://www.facebook.com/groups/${i+1}/posts/123/?notif_t=group_crossposting_published">Group ${i+1}</a>`).join('');
        }
        document.getElementById('scroller').addEventListener('scroll',render);
        </script>'''
    visited = []

    def serve(route):
        visited.append(route.request.url)
        route.fulfill(body=html, content_type='text/html; charset=utf-8')

    page.route('https://www.facebook.com/**', serve)
    publisher = FacebookPublisher(Settings.from_root(tmp_path))
    notifications = publisher._read_delivery_notifications(page)
    assert len(notifications) == 25
    assert visited == ['https://www.facebook.com/']
