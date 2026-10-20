from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import Database, utc_now
from .facebook import SessionStatus, FacebookPublisher


def save_account_status(db: Database, status: SessionStatus, profile_path: str, *, login: bool = False) -> None:
    """Cache identity separately from session health, clearing it on account switches."""
    from .workspaces import validate_identity
    if status.logged_in:
        validate_identity(db, status.account_id)
    previous = db.get_setting("facebook_user", {})
    same_account = (
        previous.get("account_id") == status.account_id
        and previous.get("browser_profile") == profile_path
    )
    user = previous.copy() if same_account else {}
    if not FacebookPublisher._valid_profile_name(user.get('name', '')):
        user.pop('name', None)
    if status.logged_in:
        user.update(account_id=status.account_id, browser_profile=profile_path)
        for key in ("name", "avatar_url", "profile_url"):
            value = getattr(status, key)
            if key == 'name' and not FacebookPublisher._valid_profile_name(value):
                continue
            if value:
                user[key] = value
        user["updated_at"] = utc_now()
        db.set_setting("facebook_user", user)
        # Repair only an automatically imported counter, never a user's alias.
        if getattr(db, 'strict_account_scope', False) and user.get('name'):
            row = db.one('SELECT label FROM browser_accounts WHERE slot_id=?', (db.slot,))
            if row and not FacebookPublisher._valid_profile_name(row['label']):
                db.execute('UPDATE browser_accounts SET label=? WHERE slot_id=?', (user['name'], db.slot))
        db.set_setting("facebook_account_id", status.account_id)
        if not getattr(db, 'strict_account_scope', False):
            db.execute("UPDATE bikes SET owner_account_id=? WHERE owner_account_id=''", (status.account_id,))
        if login:
            db.set_setting("facebook_login_at", utc_now())
    db.set_setting("facebook_session", {
        "state": ("connected" if status.logged_in else "disconnected") if status.checked else "unknown",
        "message": status.message,
        "checked_at": utc_now(),
        "browser_profile": profile_path,
    })


def activity_summary(db: Database, *, now: datetime | None = None) -> dict:
    """Use queue records so clearing the journal doesn't reset statistics."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Warsaw"))
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    next_day = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo)
    start, end = (value.astimezone(timezone.utc).isoformat(timespec="seconds") for value in (day_start, next_day))
    counts = {row["status"]: row["count"] for row in db.all("SELECT status,COUNT(*) AS count FROM queue GROUP BY status")}
    sent_today = db.one(
        "SELECT COUNT(*) AS count FROM queue WHERE status='sent' AND updated_at>=? AND updated_at<?",
        (start, end),
    )["count"]
    next_job = db.one(
        "SELECT q.id,q.scheduled_at,b.name AS bike_name,g.name AS group_name "
        "FROM queue q JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id "
        "WHERE q.status='pending' ORDER BY q.scheduled_at,q.id LIMIT 1"
    )
    return {
        "sent": counts.get("sent", 0), "sent_today": sent_today,
        "pending": counts.get("pending", 0), "running": counts.get("running", 0),
        "failed": counts.get("failed", 0), "approval": counts.get("waiting_approval", 0),
        "unknown": counts.get('unknown',0), "moderation":counts.get('moderation',0),
        "groups": db.one("SELECT COUNT(*) AS count FROM groups_ WHERE archived=0 AND enabled=1")["count"],
        "bikes": db.one("SELECT COUNT(*) AS count FROM bikes WHERE status='active'")["count"],
        "photos": db.one("SELECT COUNT(*) AS count FROM photos p JOIN bikes b ON b.id=p.bike_id WHERE p.archived=0 AND b.status='active'")["count"],
        "next_job": next_job,
    }
