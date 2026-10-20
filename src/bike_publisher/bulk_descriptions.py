"""Account-scoped, previewed description edits with conflict-safe undo."""
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

from .db import utc_now


def _account(db):
    account = db.get_setting('facebook_account_id', '')
    if not account:
        raise ValueError('Войди в Facebook перед изменением описаний')
    return account


def _remove_promo(text):
    lines = []
    removed = False
    for line in text.splitlines():
        normalized = unicodedata.normalize('NFKD', line.lower().replace('ł', 'l'))
        normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
        normalized = ' '.join(re.sub(r'[^a-z0-9\s]', ' ', normalized).split())
        if re.fullmatch(r'wiecej rowerow (?:znajdziesz )?(?:na (?:moim )?profilu|w (?:moim )?profilu)', normalized):
            removed = True
            continue
        lines.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() if removed else text


def preview_descriptions(db, body):
    account = _account(db)
    mode = body.get('mode', 'remove_promo')
    find, replacement = str(body.get('find', '')), str(body.get('replacement', ''))
    if mode not in ('remove_promo', 'replace'):
        raise ValueError('Неизвестный способ изменения описаний')
    if mode == 'replace' and not find.strip():
        raise ValueError('Укажи текст для поиска')
    if len(find) > 10000 or len(replacement) > 10000:
        raise ValueError('Текст слишком длинный')
    edits = []
    for item in db.all("SELECT id,name,description FROM bikes WHERE owner_account_id=? AND status IN ('active','sold') ORDER BY id", (account,)):
        before = item['description']
        after = _remove_promo(before) if mode == 'remove_promo' else before.replace(find, replacement)
        if before == after:
            continue
        if not after.strip():
            raise ValueError(f"После изменения описание «{item['name']}» станет пустым. Ничего не сохранено.")
        edits.append({'id': item['id'], 'name': item['name'], 'before': before, 'after': after})
    preview = {'token': secrets.token_urlsafe(24), 'expires': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(), 'edits': edits}
    db.set_setting(f'description_preview:{account}', preview)
    return preview


def _commit(db, account, edits):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for edit in edits:
            row = conn.execute("SELECT description FROM bikes WHERE id=? AND owner_account_id=? AND status IN ('active','sold')", (edit['id'], account)).fetchone()
            if not row or row['description'] != edit['before']:
                raise ValueError('Описание уже изменилось. Обнови предпросмотр; ничего не сохранено.')
            if conn.execute("SELECT 1 FROM queue WHERE bike_id=? AND status='running'", (edit['id'],)).fetchone():
                raise ValueError('Дождись завершения публикации товара перед массовым изменением')
        now = utc_now()
        for edit in edits:
            conn.execute('UPDATE bikes SET description=?,updated_at=? WHERE id=? AND owner_account_id=?', (edit['after'], now, edit['id'], account))
        undo = [{'id': e['id'], 'before': e['after'], 'after': e['before']} for e in edits]
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (f'description_undo:{account}', json.dumps(undo, ensure_ascii=False)))
        conn.execute('DELETE FROM settings WHERE key=?', (f'description_preview:{account}',))
    return len(edits)


def apply_descriptions(db, token):
    account = _account(db)
    preview = db.get_setting(f'description_preview:{account}', {})
    if not token or not secrets.compare_digest(str(preview.get('token', '')), token):
        raise ValueError('Сначала создай предпросмотр изменений')
    if datetime.fromisoformat(preview['expires']) <= datetime.now(timezone.utc):
        raise ValueError('Предпросмотр устарел. Создай его заново.')
    if not preview['edits']:
        return 0
    return _commit(db, account, preview['edits'])


def undo_descriptions(db):
    account = _account(db)
    edits = db.get_setting(f'description_undo:{account}', [])
    if not edits:
        raise ValueError('Нет массового изменения для отмены')
    # Applying an inverse edit also records its inverse, allowing a safe redo.
    return _commit(db, account, edits)
