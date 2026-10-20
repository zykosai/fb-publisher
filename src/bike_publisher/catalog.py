"""Reversible sales and non-destructive catalog/history operations."""
from __future__ import annotations

import re
from decimal import Decimal

from .db import Database, utc_now


def price_cents(value: str) -> int:
    text = re.sub(r"\s+", "", str(value)).lower()
    text = re.sub(r"(?:zł|pln)$", "", text).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        raise ValueError("Укажи корректную цену в zł перед отметкой продажи")
    amount = int(Decimal(text) * 100)
    if amount > 999_999_999_99:
        raise ValueError("Цена слишком большая")
    return amount


def set_bike_status(db: Database, bike_id: int, status: str) -> None:
    if status not in {"active", "sold", "deleted"}:
        raise ValueError("Неизвестный статус велосипеда")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        bike = conn.execute("SELECT * FROM bikes WHERE id=? AND status IN ('active','sold')", (bike_id,)).fetchone()
        if not bike:
            raise ValueError("Велосипед не найден")
        if bike['status'] == status:
            return
        if conn.execute("SELECT id FROM queue WHERE bike_id=? AND status='running' LIMIT 1", (bike_id,)).fetchone():
            raise ValueError("Поставь бота на паузу и дождись остановки публикации этого велосипеда")
        amount = price_cents(bike['price']) if status == 'sold' else None
        now = utc_now()
        conn.execute("UPDATE bikes SET status=?,sold_price_cents=?,updated_at=? WHERE id=?", (status, amount, now, bike_id))
        if status != 'active':
            conn.execute("UPDATE queue SET status='cancelled',last_error=?,updated_at=? WHERE bike_id=? AND status IN ('pending','waiting_approval')",
                         ('Велосипед продан' if status == 'sold' else 'Велосипед удалён', now, bike_id))


def sales_summary(db: Database) -> dict:
    row = db.one("SELECT COUNT(*) count,COALESCE(SUM(sold_price_cents),0) total_cents FROM bikes WHERE status='sold' AND owner_account_id=?", (db.get_setting('facebook_account_id', ''),))
    return {"count": row['count'], "total": f"{Decimal(row['total_cents']) / 100:.2f}"}


def clear_history(db: Database) -> None:
    # Keep delivery receipts for duplicate protection and daily statistics.
    # Hide only entire finished packets; an active plan stays accessible.
    with db.connect() as conn:
        conn.execute("""UPDATE queue SET history_hidden=1 WHERE bike_id IN
            (SELECT id FROM bikes WHERE owner_account_id=?) AND NOT EXISTS (
            SELECT 1 FROM queue active WHERE active.plan_id=queue.plan_id
            AND active.bike_id=queue.bike_id
            AND active.status IN ('pending','running','waiting_approval'))""", (db.get_setting('facebook_account_id', ''),))
        if getattr(db, 'strict_account_scope', False):
            conn.execute("DELETE FROM audit_log WHERE account_id=?", (db.get_setting('facebook_account_id', ''),))
        else:
            conn.execute("DELETE FROM audit_log")
