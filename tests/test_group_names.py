from types import SimpleNamespace

import pytest

from bike_publisher.db import Database
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.groups import clean_group_name, save_group


@pytest.mark.parametrize('raw,expected', [
    ('Фото профиля POZNAŃ OGŁOSZENIA', 'POZNAŃ OGŁOSZENIA'),
    ('Фото\u00a0профиля\nPoznań — Ogłoszenia', 'Poznań — Ogłoszenia'),
    ('Фото профиля: Poznań', 'Poznań'),
    ('Фото профиля Фото профиля Poznań', 'Poznań'),
    ('Profile picture of Poznań', 'Poznań'),
    ('Zdjęcie profilowe Poznań', 'Poznań'),
    ('Ogłoszenia · 4 ч.', 'Ogłoszenia'),
    ('Клуб: фото профиля и фотография', 'Клуб: фото профиля и фотография'),
])
def test_clean_names(raw, expected):
    assert clean_group_name(raw) == expected
    assert clean_group_name(clean_group_name(raw)) == expected


def test_save_cleans_new_and_existing_names_without_recreating_group(tmp_path):
    db = Database(tmp_path / 'app.db')
    _, identity = save_group(db, 'Фото профиля Poznań', 'https://facebook.com/groups/test', '', 0, 'pl')
    assert db.one('SELECT name FROM groups_ WHERE id=?', (identity,))['name'] == 'Poznań'
    db.execute('UPDATE groups_ SET name=? WHERE id=?', ('Фото профиля Poznań', identity))
    status, same_identity = save_group(db, 'Фото профиля Poznań', 'https://facebook.com/groups/test', '', 0, 'pl')
    assert status == 'exists' and same_identity == identity
    assert db.one('SELECT name FROM groups_ WHERE id=?', (identity,))['name'] == 'Poznań'


def test_search_prefers_visible_name_and_cleans_accessibility_fallback(monkeypatch):
    class Anchor:
        def __init__(self, identity, text, label):
            self.identity, self.text, self.label = identity, text, label

        def get_attribute(self, name):
            return f'/groups/{self.identity}' if name == 'href' else self.label

        def inner_text(self, **kwargs):
            return self.text

    anchors = [Anchor('one', 'Poznań ogłoszenia', 'Фото профиля Wrong name'),
               Anchor('two', '', 'Фото профиля Rowery giełda')]
    links = SimpleNamespace(all=lambda: anchors, count=lambda: len(anchors),
                            first=SimpleNamespace(wait_for=lambda **kwargs: None))
    page = SimpleNamespace(goto=lambda *args, **kwargs: None, locator=lambda _: links,
                           mouse=SimpleNamespace(wheel=lambda *args: None),
                           wait_for_function=lambda *args, **kwargs: None)
    context = SimpleNamespace(pages=[page], close=lambda: None)
    publisher = FacebookPublisher(SimpleNamespace(action_delay_seconds=0))
    monkeypatch.setattr(publisher, 'launch', lambda: (SimpleNamespace(stop=lambda: None), context))
    found = publisher.discover_groups(['Poznań'], limit=20)
    assert {item['name'] for item in found} == {'Poznań ogłoszenia', 'Rowery giełda'}
