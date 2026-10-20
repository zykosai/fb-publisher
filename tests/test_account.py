from datetime import datetime, timezone
from pathlib import Path

from bike_publisher.account import activity_summary, save_account_status
from bike_publisher.db import Database
from bike_publisher.facebook import SessionStatus


def test_account_switch_does_not_keep_previous_name_or_avatar(tmp_path):
    db = Database(tmp_path / "app.db")
    save_account_status(db, SessionStatus(True, "ok", "1", "First", "https://example.test/avatar.jpg"), "profile")
    save_account_status(db, SessionStatus(True, "ok", "2"), "profile")
    user = db.get_setting("facebook_user")
    assert user["account_id"] == "2"
    assert not user.get("name")
    assert not user.get("avatar_url")


def test_failed_check_preserves_identity_but_does_not_claim_connected(tmp_path):
    db = Database(tmp_path / "app.db")
    save_account_status(db, SessionStatus(True, "ok", "1", "First"), "profile")
    save_account_status(db, SessionStatus(False, "network unavailable", checked=False), "profile")
    assert db.get_setting("facebook_user")["name"] == "First"
    assert db.get_setting("facebook_session")["state"] == "unknown"
    save_account_status(db, SessionStatus(False, "login expired"), "profile")
    assert db.get_setting("facebook_session")["state"] == "disconnected"


def test_statistics_survive_log_clearing_and_use_polish_dst_day(tmp_path):
    db = Database(tmp_path / "app.db")
    created = "2026-10-01T00:00:00+00:00"
    bike = db.execute("INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)", ("1", "Bike", created, created))
    group = db.execute("INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)", ("Group", "https://facebook.com/groups/1", created, created))
    for when in ("2026-10-24T21:59:00+00:00", "2026-10-24T22:01:00+00:00", "2026-10-25T22:59:00+00:00", "2026-10-25T23:01:00+00:00"):
        db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,created_at,updated_at) VALUES(?,?,?,'sent',?,?)", (bike, group, when, created, when))
    scheduled = "2026-10-26T10:00:00+00:00"
    db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,created_at,updated_at) VALUES(?,?,?,'pending',?,?)", (bike, group, scheduled, created, created))
    db.log("event", "message")
    db.clear_audit_log()
    summary = activity_summary(db, now=datetime(2026, 10, 25, 12, tzinfo=timezone.utc))
    assert summary["sent"] == 4
    assert summary["sent_today"] == 2
    assert summary["pending"] == 1
    assert summary["next_job"]["scheduled_at"] == scheduled


def test_settings_page_renders_cached_identity_and_statistics(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    data_dir = tmp_path / "data"
    profile_dir = data_dir / "browser_profile"
    monkeypatch.setenv("BIKEBOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("BIKEBOT_BROWSER_PROFILE", str(profile_dir))
    db = Database(data_dir / "app.db")
    save_account_status(db, SessionStatus(True, "ok", "123", "Test User", "https://example.test/avatar.jpg"), str(profile_dir))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=15)
    assert not app.exception
    assert "Test User" in [element.value for element in app.subheader]
    assert "Опубликовано сегодня" in [element.label for element in app.metric]
    assert "Обновить имя и аватарку" in [element.label for element in app.button]
