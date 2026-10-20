"""Small local web application. No cloud service or paid dependency is required."""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import secrets
import socket
import ssl
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as BaseHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

from .account import save_account_status
from .config import Settings, ensure_dirs
from .catalog import clear_history, sales_summary, set_bike_status
from .control import OperationCancelled
from .group_join import fatal_group_error, JoinRateLimited
from .bulk_descriptions import preview_descriptions, apply_descriptions, undo_descriptions
from .post_style import validate_post_heading
from .db import Database, utc_now
from .facebook import FacebookPublisher
from .groups import save_group
from .group_checks import get_group_check
from .importer import import_zip
from .joined_groups import save_snapshot, update_permissions, save_group_check, remove_account_groups, restore_account_groups, restorable_group_count
from .photos import add_photos, archive_photo, reorder_photos
from .pairing import pairing_key
from .marketplace import scan_listings, prepare_preview, preview_state, commit_preview
from .queue import add_plan
from .worker_process import ensure_worker_running, stop_worker, worker_state
from .workspaces import ensure_workspace, selected_database, workspace_settings, active_workspace, create_workspace, select_workspace, update_workspace

ASSETS = Path(__file__).resolve().parents[2] / "web"
MAX_BODY = 64 * 1024 * 1024


class ThreadingHTTPServer(BaseHTTPServer):
    # Windows SO_REUSEADDR lets two live servers bind the same address. Requests
    # can then reach the wrong instance and reject its otherwise valid key.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        ensure_dirs(settings)
        if settings.db_path.exists():
            backup = settings.data_dir / "backups" / f"before-web-{datetime.now():%Y%m%d-%H%M%S}.db"
            with sqlite3.connect(settings.db_path) as source, sqlite3.connect(backup) as target:
                source.backup(target)
        self.db = Database(settings.db_path)
        self.db.set_setting("publication_paused", True)
        # Replace an old in-memory worker so it cannot ignore the new pause flag.
        stop_worker(settings)
        ensure_workspace(self.db, settings)
        self.key = pairing_key(self.db)
        self.operation_lock = threading.Lock()
        self.operation = {"busy": False, "message": "", "error": ""}

    def state(self) -> dict:
        db = selected_database(self.db)
        active = active_workspace(self.db)
        account = db.get_setting("facebook_account_id", "")
        photos = db.all("SELECT id,bike_id FROM photos WHERE archived=0 ORDER BY sort_order,id")
        bikes = db.all("SELECT id,external_id,name,price,description,post_heading,status,source,source_listing_id,source_url,imported_at FROM bikes WHERE status IN ('active','sold') AND owner_account_id=? ORDER BY CAST(external_id AS INTEGER),id", (account,))
        for bike in bikes:
            bike["photos"] = [p["id"] for p in photos if p["bike_id"] == bike["id"]]
        account = db.get_setting("facebook_account_id", "")
        groups = db.all("""SELECT g.id,g.name,g.url,COALESCE(j.allowed,0) selected,COALESCE(j.present,0) joined
            FROM groups_ g LEFT JOIN joined_groups j ON j.group_id=g.id AND j.account_id=?
            WHERE g.archived=0 AND EXISTS (SELECT 1 FROM account_groups a WHERE a.account_id=? AND a.group_id=g.id)
            AND NOT EXISTS (SELECT 1 FROM account_hidden_groups h WHERE h.account_id=? AND h.group_id=g.id)
            ORDER BY g.name COLLATE NOCASE""", (account, account, account))
        for group in groups:
            group["check"] = get_group_check(db, group['id'], account)
        packets = db.all("SELECT q.plan_id,q.bike_id,b.name,MIN(q.id) first_id,MIN(q.scheduled_at) scheduled_at,MAX(q.updated_at) updated_at FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE b.owner_account_id=? GROUP BY q.plan_id,q.bike_id HAVING MIN(q.history_hidden)=0 ORDER BY MAX(q.id) DESC LIMIT 30", (account,))
        for packet in packets:
            packet["groups"] = db.all("SELECT q.id,q.status,q.last_error,g.name FROM queue q JOIN groups_ g ON g.id=q.group_id WHERE q.plan_id=? AND q.bike_id=? ORDER BY q.id", (packet["plan_id"], packet["bike_id"]))
        today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
        delivered = db.all("SELECT q.plan_id,q.bike_id,q.updated_at FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status IN ('sent','published','moderation') AND b.owner_account_id=?", (account,))
        sent_today = len({(r["plan_id"], r["bike_id"]) for r in delivered if datetime.fromisoformat(r["updated_at"]).astimezone(ZoneInfo("Europe/Warsaw")).date() == today})
        next_job = db.one("SELECT b.name,q.scheduled_at FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status='pending' AND b.owner_account_id=? ORDER BY q.scheduled_at,q.id LIMIT 1", (account,))
        deleted_groups = restorable_group_count(db, account)
        return {"bikes": bikes, "groups": groups, "turbo": db.get_setting('turbo_mode', False), "deleted_groups_count": deleted_groups, "history": packets,
                "user": db.get_setting("facebook_user", {}), "session": db.get_setting("facebook_session", {}),
                "paused": db.get_setting("publication_paused", True), "worker": worker_state(workspace_settings(self.settings, self.db)).running,
                "activity": db.get_setting("worker_activity", {}), "operation": self.operation.copy(),
                "sent_today": sent_today, "next_job": next_job, "sales": sales_summary(db),
                "workspace": {'id': active['slot_id'], 'label': active['label'], 'preset': active['preset']} if active else {'id': '', 'preset': 'goods'},
                "accounts": self.account_summaries(),
                "marketplace": {'listings': db.get_setting(f'marketplace_listings:{account}', []), 'preview': preview_state(db, account)}}

    def account_summaries(self):
        result = []
        for row in self.db.all('SELECT * FROM browser_accounts ORDER BY created_at,slot_id'):
            scoped = selected_database(self.db, row['slot_id'])
            result.append({'id': row['slot_id'], 'label': row['label'], 'account_id': row['account_id'], 'preset': row['preset'],
                           'user': scoped.get_setting('facebook_user', {}), 'session': scoped.get_setting('facebook_session', {}),
                           'paused': scoped.get_setting('publication_paused', True),
                           'running': worker_state(workspace_settings(self.settings, self.db, row['slot_id'])).running})
        return result

    def run_operation(self, title: str, action, *, kind: str = '') -> dict:
        if not self.operation_lock.acquire(blocking=False):
            raise ValueError("Дождись завершения текущей операции Facebook")
        db = selected_database(self.db)
        if db.one("SELECT q.id FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status='running' AND b.owner_account_id=?", (db.get_setting('facebook_account_id', ''),)):
            self.operation_lock.release()
            raise ValueError("Сначала поставь публикацию на паузу и дождись закрытия черновика")
        db.set_setting("publication_paused", True)
        browser_settings = workspace_settings(self.settings, self.db)
        self.operation = {"busy": True, "message": title, "error": "", "kind": kind}
        cancellation = threading.Event()
        self.operation_cancel = cancellation

        def check_cancelled():
            if cancellation.is_set():
                raise OperationCancelled()

        def work():
            try:
                publisher = FacebookPublisher(browser_settings)
                if kind == 'group_check':
                    publisher.control = check_cancelled
                message = action(publisher)
                self.operation = {"busy": False, "message": message or "Готово", "error": ""}
            except OperationCancelled:
                self.operation = {"busy": False, "message": f"Проверка отменена. Проверено групп: {self.operation.get('checked', 0)}", "error": ""}
            except Exception as exc:
                self.operation = {"busy": False, "message": "", "error": str(exc)}
            finally:
                self.operation_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def action(self, path: str, body: dict) -> dict:
        db = selected_database(self.db)
        if path in ('/api/accounts/add', '/api/accounts/select', '/api/accounts/update'):
            if not self.operation_lock.acquire(blocking=False):
                raise ValueError('Дождись завершения операции Facebook или отмени проверку перед переключением аккаунта')
            try:
                if path.endswith('/add'):
                    slot = create_workspace(self.db, body.get('label', ''), body.get('preset', 'goods'))
                    return {'ok': True, 'id': slot, 'message': 'Аккаунт добавлен. Теперь войди в Facebook в его отдельном окне.'}
                if path.endswith('/select'):
                    select_workspace(self.db, str(body.get('id', '')))
                else:
                    row = active_workspace(self.db)
                    if not row:
                        raise ValueError('Сначала добавь аккаунт')
                    update_workspace(self.db, row['slot_id'], body.get('label', ''), body.get('preset', 'goods'))
            finally:
                self.operation_lock.release()
        elif path in ('/api/accounts/start', '/api/accounts/pause'):
            slot = str(body.get('id', ''))
            if not self.db.one('SELECT 1 FROM browser_accounts WHERE slot_id=?', (slot,)):
                raise ValueError('Профиль аккаунта не найден')
            scoped = selected_database(self.db, slot)
            if not scoped.get_setting('facebook_account_id', ''):
                raise ValueError('Сначала войди в Facebook этого аккаунта')
            if path.endswith('/pause'):
                scoped.set_setting('publication_paused', True)
            else:
                if not self.operation_lock.acquire(blocking=False):
                    raise ValueError('Дождись завершения операции Facebook')
                try:
                    if scoped.one("SELECT q.id FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status='running' AND b.owner_account_id=?", (scoped.account_id,)):
                        return {'ok': True}
                    ensure_worker_running(workspace_settings(self.settings, self.db, slot))
                    scoped.set_setting('publication_paused', False)
                finally:
                    self.operation_lock.release()
        elif path == '/api/turbo':
            if not isinstance(body.get('enabled'), bool):
                raise ValueError('Укажи состояние режима Турбо')
            db.set_setting('turbo_mode', body['enabled'])
            return {'ok': True, 'message': 'Турбо включён. Расписание и проверки безопасности сохранены.' if body['enabled'] else 'Турбо выключен.'}
        elif path == '/api/groups/check/cancel':
            event = getattr(self, 'operation_cancel', None)
            if not self.operation.get('busy') or self.operation.get('kind') != 'group_check' or event is None:
                raise ValueError('Сейчас нет активной проверки групп')
            event.set()
            self.operation.update(cancelling=True, message='Останавливаю проверку групп…')
            return {'ok': True}
        elif path == '/api/descriptions/preview':
            return {'ok': True, **preview_descriptions(db, body)}
        elif path == '/api/descriptions/apply':
            count = apply_descriptions(db, str(body.get('token', '')))
            return {'ok': True, 'message': f'Описания обновлены: {count}', 'count': count}
        elif path == '/api/descriptions/undo':
            count = undo_descriptions(db)
            return {'ok': True, 'message': f'Описания восстановлены: {count}', 'count': count}
        elif path == "/api/pause":
            db.set_setting("publication_paused", True)
        elif path == "/api/start":
            if not db.get_setting('facebook_account_id', ''):
                raise ValueError('Сначала войди в Facebook в разделе «Аккаунты»')
            if not self.operation_lock.acquire(blocking=False):
                raise ValueError("Дождись завершения операции Facebook")
            try:
                if db.one("SELECT q.id FROM queue q JOIN bikes b ON b.id=q.bike_id WHERE q.status='running' AND b.owner_account_id=?", (db.get_setting('facebook_account_id', ''),)):
                    return {'ok': True}
                ensure_worker_running(workspace_settings(self.settings, self.db))
                db.set_setting("publication_paused", False)
            finally:
                self.operation_lock.release()
        elif path == "/api/plan":
            bike_ids = list(dict.fromkeys(int(i) for i in body.get("bikes", [])))
            group_ids = list(dict.fromkeys(int(i) for i in body.get("groups", [])))
            delay = int(body.get("delay", 90))
            if not bike_ids or not group_ids or not 0 <= delay <= 10080:
                raise ValueError("Выбери товары, группы и интервал от 0 до 10080 минут")
            for bike_id in bike_ids:
                bike = db.one("SELECT * FROM bikes WHERE id=? AND status='active' AND owner_account_id=?", (bike_id, db.get_setting('facebook_account_id', '')))
                if not bike:
                    raise ValueError("Проданный или удалённый товар нельзя публиковать")
                preset = (active_workspace(self.db) or {}).get('preset', 'goods')
                if not bike["description"].strip() or (preset == 'goods' and not db.one("SELECT id FROM photos WHERE bike_id=? AND archived=0", (bike_id,))):
                    raise ValueError("Нужно описание; в режиме товаров также требуется хотя бы одно фото")
            account = db.get_setting("facebook_account_id", "")
            if not account:
                raise ValueError("Войди в Facebook в разделе «Профиль»")
            for group_id in group_ids:
                if not db.one('SELECT 1 FROM account_groups WHERE account_id=? AND group_id=?', (account, group_id)):
                    raise ValueError('Группа не добавлена к текущему аккаунту')
                if db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=? AND group_id=?', (account, group_id)):
                    raise ValueError('Группа удалена из списка этого аккаунта')
                access = db.one("SELECT * FROM joined_groups WHERE account_id=? AND group_id=? AND present=1", (account, group_id))
                check = get_group_check(db, group_id, account)
                if not access or check.get("state") != "ready":
                    raise ValueError("Публиковать можно в свои проверенные группы с обычным полем публикации")
            update_permissions(db, account, group_ids)
            start = datetime.fromisoformat(body["start"]) if body.get("start") else datetime.now(timezone.utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
            count = add_plan(db, bike_ids, group_ids, start, delay, mode="joined_batch")
            return {"ok": True, "message": f"План добавлен: {len(bike_ids)} товаров, {len(group_ids)} групп. Нажми «Запустить»." if count else "Эти товары уже ожидают отправки в выбранные группы"}
        elif path == "/api/cancel":
            db.execute("UPDATE queue SET status='cancelled',updated_at=? WHERE plan_id=? AND bike_id IN (SELECT id FROM bikes WHERE owner_account_id=?) AND status IN ('pending','waiting_approval')", (utc_now(), body["plan_id"], db.get_setting('facebook_account_id', '')))
        elif path == '/api/items/create':
            account = db.get_setting('facebook_account_id', '')
            if not account:
                raise ValueError('Сначала войди в Facebook выбранного аккаунта')
            name = str(body.get('name', '')).strip()
            description = str(body.get('description', '')).strip()
            if not name or len(name) > 160 or not description:
                raise ValueError('Нужны название до 160 символов и описание')
            now = utc_now()
            identity = db.execute('''INSERT INTO bikes(external_id,name,price,description,post_heading,owner_account_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)''', ('item:'+secrets.token_hex(8), name, str(body.get('price', '')).strip(), description,
                validate_post_heading(body.get('post_heading', '')), account, now, now))
            return {'ok': True, 'id': identity}
        elif path == "/api/post/preview":
            heading = validate_post_heading(body.get('post_heading', ''))
            return {'ok': True, 'heading': heading, 'text': FacebookPublisher._build_text({
                'post_heading': heading,
                'price': str(body.get('price') or ''),
                'description': str(body.get('description') or '')})}
        elif path == "/api/groups/select":
            update_permissions(db, db.get_setting("facebook_account_id", ""), body["ids"])
        elif path == "/api/groups/delete":
            remove_account_groups(db, db.get_setting('facebook_account_id', ''), body['ids'])
            return {'ok': True, 'message': 'Группы удалены только у текущего аккаунта. Другие аккаунты не затронуты.'}
        elif path == "/api/groups/restore":
            restored = restore_account_groups(db, db.get_setting('facebook_account_id', ''))
            return {'ok': True, 'restored': restored,
                    'message': f'Восстановлено групп: {restored}. Отменённые публикации не возобновляются.'}
        elif path == "/api/groups/add":
            _, identity = save_group(db, body["name"], body["url"], "", None, "unknown")
            db.execute('DELETE FROM account_hidden_groups WHERE account_id=? AND group_id=?', (db.get_setting('facebook_account_id', ''), identity))
        elif path == "/api/groups/sync":
            def sync(publisher):
                status = publisher.verify_login()
                save_account_status(db, status, str(publisher.settings.browser_profile))
                if not status.logged_in:
                    raise ValueError(status.message)
                found = publisher.discover_my_groups()
                save_snapshot(db, status.account_id, found)
                return f"Загружено своих групп: {len(found)}. Теперь нажми «Проверить группы»."
            return self.run_operation("Загружаю твои группы Facebook…", sync)
        elif path == "/api/groups/check":
            ids = [int(i) for i in body.get("ids", [])]
            join = bool(body.get("join", False))
            account = db.get_setting('facebook_account_id', '')
            if not account:
                raise ValueError('Войди в Facebook перед проверкой групп')
            def inspect(publisher):
                publisher.checkpoint()
                actual = publisher.verify_login()
                publisher.checkpoint()
                if not actual.logged_in or actual.account_id != account:
                    raise ValueError('Facebook-аккаунт изменился. Обнови профиль перед проверкой групп.')
                checked = 0
                progress = {'ids': ids, 'join': join, 'started_at': utc_now(), 'results': []}
                db.set_setting(f'group_check_progress:{account}', progress)
                with publisher.group_check_session():
                    for group_id in ids:
                        publisher.checkpoint()
                        if not db.one('SELECT 1 FROM account_groups WHERE account_id=? AND group_id=?', (account, group_id)):
                            continue
                        group = db.one("SELECT * FROM groups_ WHERE id=? AND archived=0", (group_id,))
                        if db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=? AND group_id=?', (account, group_id)):
                            continue
                        if group:
                            self.operation['message'] = f"Проверяю группу {checked + 1} из {len(ids)}: {group['name']}"
                            try:
                                result = publisher.inspect_group(group["url"], join=join, expected_account_id=account)
                                publisher.checkpoint()
                            except OperationCancelled:
                                raise
                            except Exception as exc:
                                if fatal_group_error(exc):
                                    progress['stopped'] = {'group_id': group_id, 'name': group['name'], 'error': str(exc), 'at': utc_now()}
                                    db.set_setting(f'group_check_progress:{account}', progress)
                                    db.log('group_join_stopped', f"{group['name']}: {exc}")
                                    raise RuntimeError(
                                        f"Проверка прервана на группе «{group['name']}». Проверено: {checked} из {len(ids)}. "
                                        f"Для остальных сохранён прежний статус. Причина: {exc}"
                                    ) from exc
                                result = {'state': 'check_failed', 'joined': None,
                                          'detail': f'Группа пропущена, продолжаю следующие. Причина: {exc}'}
                            save_group_check(db, account, group_id, result)
                            if join:
                                db.log('group_join_result', f"{group['name']} · {result['state']} · {result.get('detail', '')} · {', '.join(result.get('join_actions', []))}")
                            progress['results'].append({'group_id': group_id, 'name': group['name'], **result})
                            db.set_setting(f'group_check_progress:{account}', progress)
                            checked += 1
                            self.operation['checked'] = checked
                            if result.get('join_rate_limit'):
                                # Save confirmed membership before stopping new account actions.
                                message = 'Facebook ограничивает действия аккаунта. Результат этой группы сохранён; новые вступления остановлены. ' + result['join_rate_limit']
                                progress['stopped'] = {'group_id': group_id, 'name': group['name'], 'error': message, 'at': utc_now()}
                                db.set_setting(f'group_check_progress:{account}', progress)
                                raise JoinRateLimited(message)
                publisher.checkpoint()
                if join:
                    joined = sum(row.get('joined') is True for row in progress['results'])
                    pending = sum(row['state'] == 'pending' for row in progress['results'])
                    questions = sum(row['state'] == 'questions' for row in progress['results'])
                    skipped = sum(row['state'] in ('check_failed', 'private', 'blocked', 'removed', 'join_unconfirmed') for row in progress['results'])
                    return f"Проверено групп: {checked}. Участник: {joined}; запрос: {pending}; нужны ответы: {questions}; пропущено: {skipped}."
                skipped = sum(row['state'] == 'check_failed' for row in progress['results'])
                return f"Проверено групп: {checked}; пропущено из-за ошибки: {skipped}"
            return self.run_operation("Проверяю обычное поле публикации в группах…", inspect, kind='group_check')
        elif path == "/api/groups/search":
            query = str(body.get("query", "")).strip()
            if not query:
                raise ValueError("Введи город и тему поиска")
            def search(publisher):
                found = publisher.discover_groups([query], limit=30)
                for group in found:
                    save_group(db, group["name"], group["url"], "", None, "unknown")
                return f"Найдено групп: {len(found)}. Проверь их перед публикацией."
            return self.run_operation("Ищу группы Facebook…", search)
        elif path == '/api/login/finish':
            confirmation = getattr(self, 'login_confirmation', None)
            if not self.operation.get('busy') or self.operation.get('kind') != 'login' or confirmation is None:
                raise ValueError('Окно входа не открыто. Сначала нажми «Войти / сменить аккаунт».')
            self.operation['message'] = 'Проверяю вход в выбранный аккаунт…'
            confirmation.set()
            return {'ok': True, 'message': 'Проверяю вход в выбранный аккаунт…'}
        elif path in ("/api/login", "/api/profile/refresh"):
            confirmation = threading.Event()
            def profile(publisher):
                login = path == "/api/login"
                if login:
                    self.login_confirmation = confirmation
                    publisher.progress = lambda message: self.operation.update(message=message)
                    publisher.login(confirmed=confirmation.is_set)
                    status = publisher.last_session
                else:
                    status = publisher.verify_login()
                if status is None:
                    raise ValueError("Не удалось прочитать профиль")
                save_account_status(db, status, str(publisher.settings.browser_profile), login=login)
                return status.message
            return self.run_operation("Войди в открывшемся окне Chrome на компьютере…" if path == "/api/login" else "Обновляю профиль…", profile, kind='login' if path == '/api/login' else '')
        elif path in ('/api/marketplace/scan', '/api/marketplace/preview'):
            account = db.get_setting('facebook_account_id', '')
            if not account:
                raise ValueError('Войди в Facebook перед импортом Marketplace')
            url = str(body.get('url', ''))
            def marketplace(publisher):
                publisher.progress = lambda message: self.operation.update(message=message)
                if path.endswith('/scan'):
                    listings = scan_listings(publisher, account)
                    db.set_setting(f'marketplace_listings:{account}', listings)
                    return f'Marketplace: найдено активных объявлений {len(listings)}'
                prepare_preview(db, publisher, account, url)
                return 'Предпросмотр готов. Проверь описание, цену и порядок фото перед импортом.'
            return self.run_operation('Читаю Marketplace. Объявления на Facebook не изменяются…', marketplace)
        elif path == '/api/marketplace/commit':
            replace = body.get('replace', False)
            if not isinstance(replace, bool):
                raise ValueError('Некорректное подтверждение замены')
            result = commit_preview(db, self.settings.data_dir, db.get_setting('facebook_account_id', ''), str(body.get('id', '')), replace=replace)
            return {'ok': True, 'result': result, 'message': 'Товар импортирован из Facebook Marketplace'}
        elif path.startswith("/api/bikes/"):
            bike_id = int(path.split("/")[3])
            if not db.one("SELECT id FROM bikes WHERE id=? AND status IN ('active','sold') AND owner_account_id=?", (bike_id, db.get_setting('facebook_account_id', ''))):
                raise ValueError("Товар не найден")
            if path.endswith("/sold"):
                if not isinstance(body.get('sold'), bool):
                    raise ValueError("Укажи состояние продажи")
                set_bike_status(db, bike_id, 'sold' if body['sold'] else 'active')
            elif path.endswith("/order"):
                reorder_photos(db, bike_id, body["ids"])
            elif path.endswith("/photo/delete"):
                archive_photo(db, bike_id, int(body["id"]))
            elif path.endswith("/photos"):
                uploads = []
                for item in body["files"]:
                    upload = BytesIO(base64.b64decode(item["data"], validate=True))
                    upload.name = item["name"]
                    uploads.append(upload)
                add_photos(db, bike_id, self.settings.data_dir, uploads)
            elif path.endswith("/delete"):
                set_bike_status(db, bike_id, 'deleted')
            else:
                heading = validate_post_heading(body['post_heading']) if 'post_heading' in body else db.one('SELECT post_heading FROM bikes WHERE id=?', (bike_id,))['post_heading']
                name = str(body.get('name', db.one('SELECT name FROM bikes WHERE id=?', (bike_id,))['name'])).strip()
                if not name or len(name) > 160:
                    raise ValueError('Название должно быть от 1 до 160 символов')
                if db.one("SELECT 1 FROM queue WHERE bike_id=? AND status='running'", (bike_id,)):
                    raise ValueError('Дождись завершения текущей публикации перед изменением товара')
                db.execute("UPDATE bikes SET name=?,price=?,description=?,post_heading=?,updated_at=? WHERE id=? AND owner_account_id=?", (name, str(body["price"]).strip(), str(body["description"]).strip(), heading, utc_now(), bike_id, db.get_setting('facebook_account_id', '')))
        elif path == "/api/import":
            if not db.get_setting('facebook_account_id', ''):
                raise ValueError('Сначала войди в Facebook выбранного аккаунта')
            target = self.settings.data_dir / "incoming" / f"upload-{secrets.token_hex(8)}.zip"
            target.write_bytes(base64.b64decode(body["data"], validate=True))
            return {"ok": True, "result": import_zip(target, self.settings.data_dir, db)}
        elif path == "/api/history/clear":
            clear_history(db)
        else:
            raise ValueError("Неизвестное действие")
        return {"ok": True}


def handler_for(app: Application):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Pairing tokens must never appear in access logs.

        def respond(self, data: bytes, kind: str, status=200, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", (headers or {}).get("Cache-Control", "no-store"))
            for key, value in (headers or {}).items():
                if key != "Cache-Control":
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def json(self, data, status=200):
            self.respond(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

        def authorized(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                value = cookie.get("bike_session")
                return bool(value and hmac.compare_digest(value.value, app.key))
            except Exception:
                return False

        def do_GET(self):
            parsed = urlsplit(self.path)
            token = parse_qs(parsed.query).get("key", [""])[0]
            if parsed.path == "/" and token and hmac.compare_digest(token, app.key):
                self.respond(b"", "text/plain", 303, {"Location": "/", "Set-Cookie": f"bike_session={app.key}; HttpOnly; SameSite=Strict; Path=/"})
                return
            if parsed.path == "/api/state":
                if not self.authorized():
                    self.json({"error": "Открой ссылку подключения из окна запуска бота"}, 401)
                else:
                    self.json(app.state())
                return
            if parsed.path.startswith("/photo/"):
                if not self.authorized():
                    self.json({"error": "Нужен вход"}, 401)
                    return
                try:
                    row = app.db.one("SELECT p.path FROM photos p JOIN bikes b ON b.id=p.bike_id WHERE p.id=? AND p.archived=0 AND b.status IN ('active','sold') AND b.owner_account_id=?", (int(parsed.path.rsplit("/", 1)[1]), selected_database(app.db).get_setting('facebook_account_id', '')))
                    if not row:
                        raise ValueError()
                    with Image.open(row["path"]) as source:
                        image = ImageOps.exif_transpose(source).convert("RGB")
                        image.thumbnail((900, 700))
                        output = BytesIO()
                        image.save(output, "JPEG", quality=82)
                    self.respond(output.getvalue(), "image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
                except (OSError, ValueError):
                    self.json({"error": "Фото недоступно"}, 404)
                return
            if parsed.path.startswith('/marketplace/photo/'):
                if not self.authorized():
                    self.json({'error': 'Нужен вход'}, 401)
                    return
                try:
                    _, _, _, token, index = parsed.path.split('/')
                    row = app.db.one('SELECT data FROM marketplace_previews WHERE id=? AND account_id=?', (token, selected_database(app.db).get_setting('facebook_account_id', '')))
                    if not row or not index.isdigit(): raise ValueError()
                    photo = json.loads(row['data'])['photos'][int(index)]
                    target = Path(photo['path']).resolve()
                    if not target.is_relative_to((app.settings.data_dir / 'marketplace' / 'previews' / token).resolve()): raise ValueError()
                    kind = {'.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}[target.suffix]
                    self.respond(target.read_bytes(), kind)
                except (ValueError, KeyError, IndexError, OSError):
                    self.json({'error': 'Фото недоступно'}, 404)
                return
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css"), "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                     "/sw.js": ("sw.js", "text/javascript"), "/icon.svg": ("icon.svg", "image/svg+xml"), '/i18n.js': ('i18n.js', 'text/javascript'), '/locales.js': ('locales.js', 'text/javascript')}
            if parsed.path in files:
                name, mime = files[parsed.path]
                self.respond((ASSETS / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.json({"error": "Не найдено"}, 404)

        def do_POST(self):
            if not self.authorized():
                self.json({"error": "Открой ссылку подключения из окна запуска"}, 401)
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.json({"error": "Недопустимый источник запроса"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("Слишком большой запрос: максимум 64 МБ. Добавляй большие фото отдельными порциями.")
                body = json.loads(self.rfile.read(length))
                self.json(app.action(urlsplit(self.path).path, body))
            except (ValueError, KeyError, TypeError) as exc:
                self.json({"error": str(exc)}, 400)
            except Exception as exc:
                self.json({"error": str(exc)}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan", action="store_true", help="Разрешить подключение телефона в домашней сети")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Открыть панель в браузере автоматически")
    parser.add_argument("--cert", type=Path, help="PEM-сертификат для HTTPS")
    parser.add_argument("--key-file", type=Path, help="PEM-ключ для HTTPS")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key_file):
        parser.error("Для HTTPS нужны и --cert, и --key-file")
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    # Bind before opening the database or stopping a worker. A duplicate panel
    # on the same port must not interrupt an existing session.
    try:
        server = ThreadingHTTPServer((host, args.port), BaseHTTPRequestHandler)
    except OSError as exc:
        if exc.errno == 10048 or getattr(exc, "winerror", None) == 10048 or exc.errno == 98:
            parser.exit(1, f"Port {args.port} is already in use. Keep only one bot window open.\n")
        raise
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    app = Application(Settings.from_root())
    server.RequestHandlerClass = handler_for(app)
    scheme = "https" if args.cert else "http"
    panel_url = f"{scheme}://localhost:{args.port}/?key={app.key}"
    print(f"Открой на компьютере: {panel_url}", flush=True)
    print("Keep this window open. Ctrl+C stops the bot server.", flush=True)
    if args.lan:
        print(f"На телефоне: {scheme}://IP-компьютера:{args.port}/?key={app.key} (та же Wi-Fi сеть)", flush=True)
    try:
        if args.open_browser:
            try:
                if not webbrowser.open(panel_url):
                    print("Browser could not be opened. Copy the URL above into your browser.", flush=True)
            except OSError:
                print("Browser could not be opened. Copy the URL above into your browser.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBot server stopped.", flush=True)
    finally:
        app.db.set_setting("publication_paused", True)
        for row in app.db.all('SELECT slot_id FROM browser_accounts'):
            selected_database(app.db, row['slot_id']).set_setting('publication_paused', True)
        server.server_close()


if __name__ == "__main__":
    main()
