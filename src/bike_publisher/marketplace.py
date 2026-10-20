"""Read-only Marketplace import with local preview and explicit commit."""
from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import parse_qs
from uuid import uuid4

from PIL import Image

from .catalog import price_cents
from .db import Database, utc_now


def listing_id(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}:
        raise ValueError('Нужна HTTPS-ссылка на объявление Facebook Marketplace')
    match = re.fullmatch(r'/marketplace/item/(\d+)/?', parsed.path)
    if not match:
        raise ValueError('Нужна ссылка вида https://www.facebook.com/marketplace/item/123/')
    return match.group(1)


def image_url_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return parsed.scheme == 'https' and not parsed.username and parsed.port in (None, 443) and any(
            host == suffix or host.endswith('.' + suffix) for suffix in ('fbcdn.net', 'fbsbx.com'))
    except ValueError:
        return False


def _field(page, names: str) -> str:
    controls = page.get_by_role('textbox', name=re.compile(names, re.I))
    for control in controls.all():
        if control.is_visible():
            return control.input_value().strip()
    raise ValueError('Не найдено поле объявления: ' + names)


def read_editor(page, item_id: str) -> dict:
    """Only seller editing forms are accepted; never save or submit this form."""
    title = _field(page, r'^(Title|Название|Заголовок|Tytuł)$')
    description = _field(page, r'^(Description|Описание|Opis)$')
    price = _field(page, r'^(Price|Цена|Cena)(?:\s*\(.*\))?$')
    if re.search(r'[$€£]|USD|EUR|GBP', price, re.I):
        raise ValueError('В этой версии поддерживается цена в PLN, без автоматической конвертации')
    amount = price_cents(price)
    if not title or not description:
        raise ValueError('Facebook не отдал полное название или описание')
    images = page.evaluate("""() => {
        const root = document.querySelector('[role="main"]') || document.body;
        return [...root.querySelectorAll('img')].filter(img => {
            if (!img.checkVisibility({checkVisibilityCSS:true})) return false;
            const r = img.getBoundingClientRect();
            return r.width >= 80 && r.height >= 80 && !/profile|профил|profil|avatar/i.test(img.alt);
        }).map(img => ({url: img.currentSrc || img.src, width:img.naturalWidth, height:img.naturalHeight}));
    }""")
    ordered = [image for image in images if image_url_allowed(image['url'])]
    if not ordered or len(ordered) > 40:
        raise ValueError('Не удалось достоверно прочитать фотографии объявления')
    return {'listing_id': item_id, 'name': title, 'description': description,
            'price': f'{Decimal(amount) / 100:.2f}', 'photos': ordered,
            'url': f'https://www.facebook.com/marketplace/item/{item_id}/'}


def scan_listings(publisher, account_id: str, *, limit: int = 50) -> list[dict]:
    playwright, context = publisher.launch()
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto('https://www.facebook.com/marketplace/you/selling/', wait_until='domcontentloaded', timeout=60000)
        publisher._pause(page, multiplier=2)
        if publisher._account_id(context) != account_id or publisher._is_login_url(page.url.lower()):
            raise ValueError('Аккаунт Facebook изменился. Обнови профиль перед импортом.')
        found = {}
        stagnant = 0
        for _ in range(20):
            before = len(found)
            # Seller cards are buttons; their promotion links expose the listing ID.
            # Read links only: never open advertising or mark-as-sold controls.
            cards = page.evaluate("""() => [...document.querySelectorAll('[role="main"] a[href*="/ad_center/create/listingad/"]')].map(a => {
                const url = new URL(a.href);
                const label = a.getAttribute('aria-label') || a.innerText;
                const match = label.match(/["“](.+?)["”]/);
                return {listing_id:url.searchParams.get('target_id'), name:match ? match[1] : label};
            })""")
            for item in cards:
                identity = item['listing_id'] or ''
                if identity.isdigit():
                    found.setdefault(identity, {**item, 'url': f'https://www.facebook.com/marketplace/item/{identity}/'})
            for item in page.locator('[role="main"] a[href*="/marketplace/item/"]').all():
                href = item.get_attribute('href') or ''
                if href.startswith('/'): href = 'https://www.facebook.com' + href
                try:
                    identity = listing_id(href)
                except ValueError:
                    continue
                name = item.inner_text().strip()[:250]
                found.setdefault(identity, {'listing_id': identity, 'name': name or f'Marketplace #{identity}',
                    'url': f'https://www.facebook.com/marketplace/item/{identity}/'})
            if len(found) >= limit: break
            stagnant = stagnant + 1 if before == len(found) else 0
            if stagnant >= 3: break
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(700)
        if not found:
            raise ValueError('Facebook не показал свои объявления. Можно вставить ссылку на конкретное объявление.')
        return list(found.values())[:limit]
    finally:
        context.close()
        playwright.stop()


def read_gallery(page, product: dict) -> list[dict]:
    """Match full gallery images to the seller form's exact ordered photo identities."""
    expected = [urlsplit(photo['url']).path for photo in product['photos']]
    page.goto(product['url'], wait_until='domcontentloaded', timeout=60000)
    heading = page.get_by_role('heading', name=product['name'], exact=True)
    heading.first.wait_for(timeout=20000)
    page.wait_for_function("""expected => {
        const images = [...document.querySelectorAll('[role="main"] img')];
        return expected.every(path => images.some(i => {
            try { return new URL(i.currentSrc || i.src).pathname === path && i.complete && i.naturalWidth >= 80; }
            catch (_) { return false; }
        }));
    }""", arg=expected, timeout=20000)
    images = page.locator('[role="main"] img').evaluate_all("""nodes => nodes.map(i => ({
        url:i.currentSrc || i.src, width:i.naturalWidth, height:i.naturalHeight}))""")
    matched = {}
    for photo in images:
        if not image_url_allowed(photo['url']): continue
        path = urlsplit(photo['url']).path
        if path in expected and photo['width'] * photo['height'] > matched.get(path, {}).get('area', 0):
            matched[path] = {**photo, 'area': photo['width'] * photo['height']}
    if set(matched) != set(expected):
        raise ValueError('Не все фотографии найдены в полной галерее. Товар не добавлен.')
    # Facebook can expose a different public alias than the canonical seller ID.
    for link in page.locator('[role="main"] a[href*="/marketplace/edit/"]').all():
        canonical = parse_qs(urlsplit(link.get_attribute('href') or '').query).get('listing_id', [''])[0]
        if canonical.isdigit():
            product['listing_id'] = canonical
            product['url'] = f'https://www.facebook.com/marketplace/item/{canonical}/'
            break
    return [{k: v for k, v in matched[path].items() if k != 'area'} for path in expected]


def prepare_preview(db: Database, publisher, account: str, url: str) -> str:
    identity = listing_id(url)
    token = uuid4().hex
    staged = publisher.settings.data_dir / 'marketplace' / 'previews' / token
    staged.mkdir(parents=True)
    files = []
    playwright, context = publisher.launch()
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f'https://www.facebook.com/marketplace/edit/?listing_id={identity}', wait_until='domcontentloaded', timeout=60000)
        publisher._pause(page, multiplier=2)
        if publisher._account_id(context) != account or publisher._is_login_url(page.url.lower()):
            raise ValueError('Аккаунт Facebook изменился. Обнови профиль перед импортом.')
        product = read_editor(page, identity)
        product['photos'] = read_gallery(page, product)
        total = 0
        for index, image in enumerate(product['photos']):
            publisher.report(f'Marketplace: фото {index + 1}/{len(product["photos"])}')
            response = context.request.get(image['url'], timeout=30000)
            if not response.ok or not image_url_allowed(response.url):
                raise ValueError('Facebook не отдал фотографию. Импорт остановлен без добавления товара.')
            if int(response.headers.get('content-length', '0')) > 30 * 1024 * 1024:
                raise ValueError('Фотография больше 30 МБ')
            content = response.body()
            response.dispose()
            total += len(content)
            if len(content) > 30 * 1024 * 1024 or total > 200 * 1024 * 1024:
                raise ValueError('Слишком большой объём фотографий объявления')
            with Image.open(BytesIO(content)) as photo:
                width, height = photo.size
                suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(photo.format)
                photo.verify()
            if not suffix or min(width, height) < 80:
                raise ValueError('Facebook вернул миниатюру или неподдерживаемое изображение')
            target = staged / f'{index:03}{suffix}'
            target.write_bytes(content)  # Exact downloaded bytes, no extra JPEG compression.
            files.append(target)
            image.clear()
            image.update(path=str(target.resolve()), width=width, height=height, sha256=hashlib.sha256(content).hexdigest())
        product['low_resolution'] = any(min(photo['width'], photo['height']) < 600 for photo in product['photos'])
        if db.get_setting('facebook_account_id', '') != account:
            raise ValueError('Аккаунт изменился до сохранения предпросмотра')
        db.execute('INSERT INTO marketplace_previews(id,account_id,data,created_at) VALUES(?,?,?,?)',
                   (token, account, json.dumps(product, ensure_ascii=False), utc_now()))
        return token
    except Exception:
        for target in files: target.unlink(missing_ok=True)
        raise
    finally:
        context.close()
        playwright.stop()


def preview_state(db: Database, account: str) -> dict | None:
    row = db.one('SELECT id,data FROM marketplace_previews WHERE account_id=? AND result IS NULL ORDER BY created_at DESC,rowid DESC LIMIT 1', (account,))
    if not row: return None
    product = json.loads(row['data'])
    existing = db.one("SELECT id,status FROM bikes WHERE owner_account_id=? AND source='facebook' AND source_listing_id=?", (account, product['listing_id']))
    return {**{key: value for key, value in product.items() if key != 'photos'}, 'id': row['id'],
            'existing': existing, 'photos': [{'url': f'/marketplace/photo/{row["id"]}/{index}', 'width': photo['width'], 'height': photo['height']}
                                           for index, photo in enumerate(product['photos'])]}


def commit_preview(db: Database, data_dir: Path, account: str, token: str, *, replace: bool = False) -> dict:
    new_files = []
    try:
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM marketplace_previews WHERE id=? AND account_id=?', (token, account)).fetchone()
            if not row: raise ValueError('Предпросмотр не найден для этого аккаунта')
            if row['result']: return json.loads(row['result'])
            item = json.loads(row['data'])
            existing = conn.execute("SELECT * FROM bikes WHERE owner_account_id=? AND source='facebook' AND source_listing_id=?", (account, item['listing_id'])).fetchone()
            if existing and (not replace or existing['status'] == 'deleted'):
                raise ValueError('Товар уже существует. Для замены данных нужно явное подтверждение; удалённые товары не восстанавливаются автоматически.')
            if existing and conn.execute("SELECT 1 FROM queue WHERE bike_id=? AND status='running'", (existing['id'],)).fetchone():
                raise ValueError('Дождись завершения публикации товара перед обновлением')
            now = utc_now()
            if existing:
                product_id = existing['id']
                conn.execute('UPDATE bikes SET name=?,price=?,description=?,imported_at=?,updated_at=? WHERE id=?',
                             (item['name'], item['price'], item['description'], now, now, product_id))
                conn.execute('UPDATE photos SET archived=1 WHERE bike_id=?', (product_id,))
            else:
                product_id = conn.execute("INSERT INTO bikes(external_id,name,price,description,owner_account_id,source,source_listing_id,source_url,imported_at,created_at,updated_at) VALUES(?,?,?,?,?,'facebook',?,?,?,?,?)",
                    (f'fb:{account}:{item["listing_id"]}', item['name'], item['price'], item['description'], account, item['listing_id'], item['url'], now, now, now)).lastrowid
            folder = data_dir / 'marketplace' / 'products' / hashlib.sha256(account.encode()).hexdigest()[:20] / item['listing_id']
            folder.mkdir(parents=True, exist_ok=True)
            for index, photo in enumerate(item['photos']):
                source = Path(photo['path']).resolve()
                if not source.is_relative_to((data_dir / 'marketplace' / 'previews' / token).resolve()):
                    raise ValueError('Недопустимый путь фотографии')
                content = source.read_bytes()
                if hashlib.sha256(content).hexdigest() != photo['sha256']:
                    raise ValueError('Фотография предпросмотра изменилась')
                target = folder / f'{uuid4().hex}{source.suffix}'
                target.write_bytes(content)
                new_files.append(target)
                conn.execute('INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)', (product_id, str(target.resolve()), index))
            result = {'product_id': product_id, 'photos': len(item['photos']), 'updated': bool(existing)}
            conn.execute('UPDATE marketplace_previews SET result=? WHERE id=?', (json.dumps(result), token))
            return result
    except Exception:
        for target in new_files: target.unlink(missing_ok=True)
        raise
