from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .control import PublicationPaused, check_running
from .db import Database, utc_now
from .group_checks import get_group_check
from .joined_groups import save_group_check
from .delivery import finish_delivery, mark_attempt
from .facebook import FacebookPublisher, SubmissionReceipt, UnsupportedGroupError
from .queue import get_job, get_next_job


@dataclass(frozen=True)
class PublishResult:
    job_id: int
    success: bool
    message: str


def _advance_plan(db, job):
    if not job.get("plan_id"):
        return
    remaining = db.one("SELECT id FROM queue WHERE plan_id=? AND bike_id=? AND status IN ('pending','running','waiting_approval')", (job["plan_id"], job["bike_id"]))
    if remaining:
        return
    delay = max(0, int(db.get_setting(f"plan:{job['plan_id']}", {}).get("delay_minutes", 90)))
    due = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat(timespec="seconds")
    db.execute("UPDATE queue SET scheduled_at=MAX(scheduled_at,?),updated_at=? WHERE plan_id=? AND status='pending' AND bike_id<>?", (due, utc_now(), job["plan_id"], job["bike_id"]))


def _permission(db, row):
    account = db.get_setting('facebook_account_id', '')
    if account and not db.one('SELECT 1 FROM account_groups WHERE account_id=? AND group_id=?', (account, row['group_id'])):
        return False
    if db.one('SELECT 1 FROM account_hidden_groups WHERE account_id=? AND group_id=?',
              (db.get_setting('facebook_account_id', ''), row['group_id'])):
        return False
    if row.get("mode") != "joined_batch":
        return True
    access = db.one("SELECT j.allowed,j.present,g.archived FROM joined_groups j JOIN groups_ g ON g.id=j.group_id WHERE j.account_id=? AND j.group_id=?", (db.get_setting("facebook_account_id", ""), row["group_id"]))
    check = get_group_check(db, row['group_id'])
    return bool(access and access["allowed"] and access["present"] and not access["archived"] and check['state'] == "ready")


def _space_remaining_packets(db, publisher, job):
    if db.get_setting('turbo_mode', False):
        return
    seconds = max(0, int(getattr(getattr(publisher, 'settings', None), 'between_posts_seconds', 0)))
    if job.get('mode') != 'joined_batch' or not seconds:
        return
    due = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')
    db.execute("UPDATE queue SET scheduled_at=MAX(scheduled_at,?),updated_at=? WHERE plan_id=? AND bike_id=? AND status='pending'",
               (due, utc_now(), job['plan_id'], job['bike_id']))


def run_job(db: Database, publisher: FacebookPublisher, job_id: int) -> PublishResult | None:
    job = get_job(db, job_id)
    if not job or job["status"] != "pending" or db.get_setting("publication_paused", False):
        return None
    owner = db.one('SELECT owner_account_id FROM bikes WHERE id=?', (job['bike_id'],))['owner_account_id']
    if getattr(db, 'strict_account_scope', False) and owner != db.get_setting('facebook_account_id', ''):
        return None
    if owner and owner != db.get_setting('facebook_account_id', ''):
        return None
    if isinstance(publisher, FacebookPublisher):
        publisher.control = lambda: check_running(db)
    batch = job.get("mode") == "joined_batch"
    # Offer the picker the remaining pool, not just its first ten rows. A group
    # missing from this picker must not prevent later available groups filling it.
    rows = db.all("SELECT q.*,b.name bike_name,b.price,b.description,b.post_heading,g.name group_name,COALESCE(NULLIF(g.identity,''),g.url) group_url FROM queue q JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id WHERE q.plan_id=? AND q.bike_id=? AND q.status='pending' ORDER BY q.id", (job["plan_id"], job["bike_id"])) if batch else [job]
    claimed = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bike = conn.execute("SELECT status FROM bikes WHERE id=?", (job['bike_id'],)).fetchone()
        if not bike or bike['status'] != 'active':
            conn.execute("UPDATE queue SET status='cancelled',last_error='Велосипед недоступен для публикации',updated_at=? WHERE bike_id=? AND status IN ('pending','waiting_approval')", (utc_now(), job['bike_id']))
            return PublishResult(job_id, False, "Велосипед недоступен для публикации")
        for row in rows:
            if not _permission(db, row):
                conn.execute("UPDATE queue SET status='cancelled',last_error='Группа больше не выбрана',updated_at=? WHERE id=?", (utc_now(), row["id"]))
            elif conn.execute("UPDATE queue SET status='running',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'", (utc_now(), row["id"])).rowcount:
                claimed.append(row)
    if not claimed:
        _advance_plan(db, job)
        return PublishResult(job_id, False, "Выбранные группы недоступны")
    photos = [row["path"] for row in db.all("SELECT path FROM photos WHERE bike_id=? AND archived=0 ORDER BY sort_order,id", (job["bike_id"],))]
    db.set_setting("worker_activity", {"bike": job["bike_name"], "groups": len(claimed), "started_at": utc_now()})
    if isinstance(publisher, FacebookPublisher):
        publisher.progress = lambda stage: db.set_setting("worker_activity", {
            "bike": job["bike_name"], "groups": len(claimed), "stage": stage, "updated_at": utc_now()
        })

    submitted_ids = set()

    def before_submit(selected):
        check_running(db)
        if not all(_permission(db, row) for row in selected):
            raise RuntimeError("Выбор групп изменился до отправки")
        mark_attempt(db, selected)
        submitted_ids.update(row['id'] for row in selected)

    try:
        if batch:
            while claimed:
                try:
                    receipt = publisher.publish_batch(claimed, photos, before_submit=before_submit)
                    break
                except UnsupportedGroupError as exc:
                    unsupported = claimed.pop(0)
                    finish_delivery(db, unsupported, "skipped", detail=str(exc))
                    save_group_check(db, db.get_setting('facebook_account_id', ''), unsupported['group_id'], {"state": "sell_only", "detail": str(exc)})
            else:
                receipt = {}
            for row in claimed:
                state, url, detail = receipt.get(row["id"], ("unknown", "", "Facebook не подтвердил результат группы"))
                if state == "deferred":
                    db.execute("UPDATE queue SET status='pending',attempts=MAX(0,attempts-1),updated_at=? WHERE id=?", (utc_now(), row["id"]))
                else:
                    finish_delivery(db, row, state, url, detail)
                    if state in ('sent', 'moderation', 'unknown'):
                        schedule_delivery_check(db, row['id'])
        else:
            result = publisher.publish(job, photos, before_submit=before_submit) if isinstance(publisher, FacebookPublisher) else publisher.publish(job, photos)
            if isinstance(result, SubmissionReceipt):
                finish_delivery(db, job, result.state, result.url, result.detail)
                if result.state in ('sent', 'moderation', 'unknown'):
                    schedule_delivery_check(db, job['id'])
            else:
                finish_delivery(db, job, "sent", str(result), "Facebook подтвердил отправку")
    except PublicationPaused:
        with db.connect() as conn:
            conn.executemany("UPDATE queue SET status='pending',attempts=MAX(0,attempts-1),last_error='',updated_at=? WHERE id=? AND status='running'", [(utc_now(), row["id"]) for row in claimed])
        return PublishResult(job_id, False, "Приостановлено до отправки")
    except Exception as exc:
        message = str(exc)[:1000]
        for row in claimed:
            if batch and row['id'] not in submitted_ids and (submitted_ids or row['id'] != claimed[0]['id']):
                db.execute("UPDATE queue SET status='pending',attempts=MAX(0,attempts-1),updated_at=? WHERE id=? AND status='running'", (utc_now(), row['id']))
                continue
            attempt = db.one("SELECT state FROM deliveries WHERE queue_id=?", (row["id"],))
            finish_delivery(db, row, "unknown" if attempt and attempt["state"] == "submitting" else "failed", detail=message)
            if attempt and attempt['state'] == 'submitting':
                schedule_delivery_check(db, row['id'])
        db.log("publish_failed", message)
        if any(word in message.lower() for word in ("temporarily blocked", "ограничиваем частоту", "checkpoint", "нет активного входа", "нет функции «добавить группы»")):
            db.set_setting("publication_paused", True)
        _space_remaining_packets(db, publisher, job)
        _advance_plan(db, job)
        return PublishResult(job_id, False, message)
    finally:
        db.set_setting("worker_activity", {})
        if isinstance(publisher, FacebookPublisher):
            publisher.progress = None
    _space_remaining_packets(db, publisher, job)
    _advance_plan(db, job)
    db.log("bike_packet_finished", f"{job['bike_name']}: пакет обработан")
    accepted = db.one("SELECT COUNT(*) count FROM queue WHERE plan_id=? AND bike_id=? AND status IN ('sent','published','moderation')", (job['plan_id'], job['bike_id']))['count']
    return PublishResult(job_id, bool(accepted), "Пакет обработан; результаты сохранены по группам")


def schedule_delivery_check(db: Database, job_id: int, *, now: datetime | None = None) -> None:
    due = ((now or datetime.now(timezone.utc)) + timedelta(minutes=2)).isoformat(timespec='seconds')
    db.execute('INSERT OR IGNORE INTO delivery_checks(queue_id,due_at) VALUES(?,?)', (job_id, due))


def run_due_checks(db: Database, publisher: FacebookPublisher, *, now: datetime | None = None) -> bool:
    """Reconcile once after two minutes; never republish an uncertain delivery."""
    if db.get_setting('publication_paused', False):
        return False
    due = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    owner_filter = 'b.owner_account_id=?' if getattr(db, 'strict_account_scope', False) else "b.owner_account_id IN ('',?)"
    jobs = db.all(f"""SELECT q.*,b.name bike_name,b.price,b.description,b.post_heading,g.name group_name,g.url group_url
        FROM delivery_checks c JOIN queue q ON q.id=c.queue_id
        JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id
        WHERE c.due_at<=? AND {owner_filter} ORDER BY c.due_at,q.id LIMIT 10""", (due, db.get_setting('facebook_account_id', '')))
    if not jobs:
        return False
    if isinstance(publisher, FacebookPublisher):
        publisher.control = lambda: check_running(db)
        publisher.progress = lambda stage: db.set_setting('worker_activity', {'stage': stage, 'updated_at': utc_now()})
    try:
        results = publisher.check_deliveries(jobs)
        for job in jobs:
            check_running(db)
            state, url, detail = results.get(job['id'], ('unknown', '', 'Результат пока не подтверждён'))
            if state == 'unknown' and job['status'] == 'moderation':
                state, url, detail = 'moderation', job['facebook_url'], 'Ранее Facebook подтвердил передачу на одобрение; решение пока не найдено.'
            finish_delivery(db, job, state, url, detail)
            db.execute('DELETE FROM delivery_checks WHERE queue_id=?', (job['id'],))
        return True
    except PublicationPaused:
        return False
    except Exception as exc:
        # Keep the persisted checks but back off instead of opening Chrome every second.
        retry_at = ((now or datetime.now(timezone.utc)) + timedelta(minutes=2)).isoformat(timespec='seconds')
        for job in jobs:
            db.execute('UPDATE delivery_checks SET due_at=? WHERE queue_id=?', (retry_at, job['id']))
        db.log('delivery_check_failed', str(exc)[:500])
        return False
    finally:
        db.set_setting('worker_activity', {})
        if isinstance(publisher, FacebookPublisher):
            publisher.progress = None


def run_once(db: Database, publisher: FacebookPublisher) -> PublishResult | None:
    run_due_checks(db, publisher)
    job = get_next_job(db)
    return run_job(db, publisher, job["id"]) if job else None
