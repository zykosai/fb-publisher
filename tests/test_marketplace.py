import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bike_publisher.db import Database, utc_now
from bike_publisher.marketplace import commit_preview, image_url_allowed, listing_id, preview_state, read_editor, read_gallery


@pytest.mark.parametrize('url', ['http://facebook.com/marketplace/item/12/', 'https://evil.com/marketplace/item/12/', 'https://facebook.com/groups/12/', 'https://facebook.com/marketplace/item/x/'])
def test_reject_invalid_listing(url):
    with pytest.raises(ValueError): listing_id(url)


def test_image_hosts():
    assert image_url_allowed('https://scontent.example.fbcdn.net/photo.jpg')
    for url in ['https://fbcdn.net.evil.com/p.jpg', 'http://fbcdn.net/p.jpg', 'https://user@fbcdn.net/p.jpg', 'https://fbcdn.net:abc/p.jpg', 'file:///p.jpg']:
        assert not image_url_allowed(url)


def draft(tmp_path, db, token='draft', account='A'):
    folder = tmp_path / 'marketplace' / 'previews' / token
    folder.mkdir(parents=True)
    photos = []
    for index, color in enumerate(['red', 'blue', 'green']):
        path = folder / f'{index}.jpg'
        Image.new('RGB', (960, 720), color).save(path)
        photos.append({'path': str(path), 'width': 960, 'height': 720, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    item = {'listing_id': '123', 'name': 'Test product', 'price': '1600.00', 'description': 'Original text', 'url': 'https://www.facebook.com/marketplace/item/123/', 'photos': photos}
    db.execute('INSERT INTO marketplace_previews(id,account_id,data,created_at) VALUES(?,?,?,?)', (token, account, json.dumps(item), utc_now()))
    return item


def test_commit_order_exact_bytes_idempotence_and_owner(tmp_path):
    db = Database(tmp_path / 'app.db')
    item = draft(tmp_path, db)
    assert preview_state(db, 'B') is None
    with pytest.raises(ValueError): commit_preview(db, tmp_path, 'B', 'draft')
    result = commit_preview(db, tmp_path, 'A', 'draft')
    assert result['photos'] == 3
    assert commit_preview(db, tmp_path, 'A', 'draft') == result
    product = db.one('SELECT * FROM bikes WHERE id=?', (result['product_id'],))
    assert product['owner_account_id'] == 'A' and product['source'] == 'facebook'
    photos = db.all('SELECT * FROM photos ORDER BY sort_order')
    assert [hashlib.sha256(Path(p['path']).read_bytes()).hexdigest() for p in photos] == [p['sha256'] for p in item['photos']]
    assert len(photos) == 3


def test_replace_requires_confirmation_preserves_sold_and_rolls_back(tmp_path):
    db = Database(tmp_path / 'app.db')
    draft(tmp_path, db)
    result = commit_preview(db, tmp_path, 'A', 'draft')
    identity = result['product_id']
    db.execute("UPDATE bikes SET status='sold',sold_price_cents=140000 WHERE id=?", (identity,))
    item = draft(tmp_path, db, 'next')
    with pytest.raises(ValueError): commit_preview(db, tmp_path, 'A', 'next')
    commit_preview(db, tmp_path, 'A', 'next', replace=True)
    assert db.one('SELECT status,sold_price_cents FROM bikes WHERE id=?', (identity,)) == {'status':'sold','sold_price_cents':140000}
    assert len(db.all('SELECT * FROM photos WHERE archived=0')) == 3
    bad = draft(tmp_path, db, 'bad')
    Path(bad['photos'][1]['path']).unlink()
    with pytest.raises(FileNotFoundError): commit_preview(db, tmp_path, 'A', 'bad', replace=True)
    assert len(db.all('SELECT * FROM photos WHERE archived=0')) == 3
    db.execute("UPDATE bikes SET status='deleted' WHERE id=?", (identity,))
    with pytest.raises(ValueError): commit_preview(db, tmp_path, 'A', 'bad', replace=True)


def test_account_catalog_isolation(tmp_path, monkeypatch):
    from bike_publisher import webapp
    db = Database(tmp_path / 'app.db')
    draft(tmp_path, db, 'one', 'A')
    draft(tmp_path, db, 'two', 'B')
    first = commit_preview(db, tmp_path, 'A', 'one')['product_id']
    second = commit_preview(db, tmp_path, 'B', 'two')['product_id']
    app = webapp.Application.__new__(webapp.Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(data_dir=tmp_path), {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    db.set_setting('facebook_account_id', 'A')
    assert [p['id'] for p in app.state()['bikes']] == [first]
    with pytest.raises(ValueError): app.action(f'/api/bikes/{second}/sold', {'sold':True})
    db.set_setting('facebook_account_id', 'B')
    assert [p['id'] for p in app.state()['bikes']] == [second]
    db.set_setting('facebook_account_id', 'A')
    assert [p['id'] for p in app.state()['bikes']] == [first]


def test_editor_order_matches_full_gallery_not_dom_order():
    from playwright.sync_api import sync_playwright
    image = BytesIO()
    Image.new('RGB', (960, 720), 'pink').save(image, format='PNG')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.route('https://test.fbcdn.net/**', lambda route: route.fulfill(body=image.getvalue(), content_type='image/png'))
        page.set_content('''<main role="main"><input aria-label="Название" value="Example">
            <input aria-label="Цена" value="1 600 PLN"><textarea aria-label="Описание">Original description</textarea>
            <img width="100" height="100" src="https://test.fbcdn.net/first.png?thumb=1">
            <img width="100" height="100" src="https://test.fbcdn.net/second.png?thumb=1">
            <img width="100" height="100" alt="Ваш профиль" src="https://test.fbcdn.net/avatar.png">
            <img style="display:none" src="https://test.fbcdn.net/hidden.png"></main>''')
        page.wait_for_load_state('load')
        product = read_editor(page, '123')
        assert product['price'] == '1600.00'
        assert product['description'] == 'Original description'
        assert len(product['photos']) == 2
        page.route('https://www.facebook.com/marketplace/item/123/', lambda route: route.fulfill(body='''<main role="main">
            <h1>Example</h1><a href="/marketplace/edit/?listing_id=456">Edit</a>
            <img src="https://test.fbcdn.net/second.png?full=1"><img src="https://test.fbcdn.net/first.png?full=1"></main>''', content_type='text/html'))
        photos = read_gallery(page, product)
        assert [p['url'] for p in photos] == ['https://test.fbcdn.net/first.png?full=1', 'https://test.fbcdn.net/second.png?full=1']
        assert all(p['width'] == 960 for p in photos)
        assert product['listing_id'] == '456'
        browser.close()
