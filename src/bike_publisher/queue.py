from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .db import Database, utc_now


def add_plan(db: Database, bike_ids: list[int], group_ids: list[int], start_at: datetime, delay_minutes: int, manual_approval: bool = False, *, mode: str = 'single') -> int:
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    start_at = start_at.astimezone(timezone.utc)
    created = 0
    plan_id = uuid4().hex
    db.set_setting(f"plan:{plan_id}", {"delay_minutes": delay_minutes})
    for bike_index, bike_id in enumerate(bike_ids):
        scheduled = (start_at + timedelta(minutes=bike_index * delay_minutes)).replace(
            microsecond=0
        )
        for group_id in group_ids:
            exists = db.one(
                "SELECT id FROM queue WHERE bike_id=? AND group_id=? "
                "AND status IN ('pending','waiting_approval','running')",
                (bike_id, group_id),
            )
            if exists:
                continue
            status = "waiting_approval" if manual_approval else "pending"
            db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,approval_required,created_at,updated_at,mode,plan_id) VALUES(?,?,?,?,?,?,?,?,?)", (bike_id, group_id, scheduled.isoformat(), status, int(manual_approval), utc_now(), utc_now(), mode, plan_id))
            created += 1
    db.log("queue_created", f"Добавлено заданий в очередь: {created}")
    return created


def approve_jobs(db: Database, job_ids: list[int]) -> int:
    if not job_ids:
        return 0
    placeholders = ",".join("?" for _ in job_ids)
    with db.connect() as conn:
        cur = conn.execute(f"UPDATE queue SET status='pending',updated_at=? WHERE id IN ({placeholders}) AND status='waiting_approval'", (utc_now(), *job_ids))
        return cur.rowcount


def cancel_job(db: Database, job_id: int) -> bool:
    """Cancel a job only while it has not been claimed by the worker."""
    with db.connect() as conn:
        changed = conn.execute(
            "UPDATE queue SET status='cancelled',last_error='Отменено пользователем',"
            "updated_at=? WHERE id=? AND status IN ('pending','waiting_approval')",
            (utc_now(), job_id),
        ).rowcount
    if changed:
        db.log("queue_job_cancelled", f"Отменено задание {job_id}")
    return changed == 1


def get_next_job(db: Database) -> dict | None:
    if db.get_setting("publication_paused", False):
        return None
    from .delivery import group_gate
    account = db.get_setting('facebook_account_id', '')
    owner_filter = 'b.owner_account_id=?' if getattr(db, 'strict_account_scope', False) else "b.owner_account_id IN ('',?)"
    candidates = db.all(
        "SELECT q.*,b.name bike_name,b.price,b.description,b.post_heading,g.name group_name,g.url group_url "
        "FROM queue q JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id "
        f"WHERE q.status='pending' AND q.scheduled_at<=? AND {owner_filter} AND "
        "(?='' OR EXISTS (SELECT 1 FROM account_groups a WHERE a.account_id=? AND a.group_id=q.group_id)) AND "
        "NOT EXISTS (SELECT 1 FROM account_hidden_groups h WHERE h.account_id=? AND h.group_id=q.group_id) AND "
        "((q.mode='single' AND g.enabled=1 AND g.archived=0 AND g.catalog=1) OR q.mode IN ('joined_batch','joined_single')) "
        "AND NOT EXISTS (SELECT 1 FROM queue earlier WHERE earlier.plan_id=q.plan_id "
        "AND q.plan_id<>'' AND earlier.bike_id<>q.bike_id AND earlier.id<q.id "
        "AND earlier.status IN ('pending','running','waiting_approval')) "
        "ORDER BY q.scheduled_at,q.id LIMIT 200", (utc_now(), account, account, account, account))
    return next((row for row in candidates if group_gate(db, row['group_id'])['allowed']), None)


def get_job(db: Database, job_id: int) -> dict | None:
    return db.one(
        "SELECT q.*,b.name bike_name,b.price,b.description,b.post_heading,"
        "g.name group_name,COALESCE(NULLIF(g.identity,''),g.url) group_url "
        "FROM queue q JOIN bikes b ON b.id=q.bike_id "
        "JOIN groups_ g ON g.id=q.group_id WHERE q.id=?",
        (job_id,),
    )
