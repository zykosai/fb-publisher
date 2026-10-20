from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bike_publisher.config import Settings
from bike_publisher.db import Database
from bike_publisher.delivery import finish_delivery, group_gate
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.groups import (
    group_relevance,
    make_search_plan,
    normalize_group_url,
    remove_groups,
    restore_groups,
    save_group,
)
from bike_publisher.importer import parse_info
from bike_publisher.photos import add_photos, archive_photo, reorder_photos, restore_photo
from bike_publisher.queue import add_plan, cancel_job
from bike_publisher.joined_groups import save_snapshot, update_permissions
from bike_publisher.worker import run_once


def test_parse_info_supports_russian_and_polish_keys():
    parsed = parse_info("1- Рама M\nКолеса 29\nЦена 700\n2- cena 1100\nkola 26\nrama L\n3- Rama m kola 26 1150\n4- 1300 xl 29")
    assert parsed["1"] == {"frame": "M", "wheels": "29", "price": "700"}
    assert parsed["2"] == {"price": "1100", "wheels": "26", "frame": "L"}
    assert parsed["3"] == {"frame": "M", "wheels": "26", "price": "1150"}
    assert parsed["4"] == {"price": "1300", "frame": "XL", "wheels": "29"}


def test_search_plan_is_bilingual_and_bounded():
    plan = make_search_plan()
    assert plan.radius_min_km == 15 and plan.radius_max_km == 200
    assert any("sprzedam rower" in query for query in plan.queries)
    assert any("велосипед" in query for query in plan.queries)
    assert len(plan.queries) >= 10


def test_group_search_ranks_relevant_names_and_normalizes_links():
    plan = make_search_plan("  Łódź  ", 200, 10)
    assert plan.city == "Łódź"
    assert plan.radius_min_km == plan.radius_max_km == 200
    assert group_relevance("Rowery używane Poznań sprzedaż", "Poznań") > group_relevance(
        "Ogłoszenia lokalne", "Poznań"
    )
    assert normalize_group_url("facebook.com/groups/12345/?ref=share") == (
        "https://www.facebook.com/groups/12345"
    )
    assert normalize_group_url("https://example.com/groups/12345") == ""
    city_variants=make_search_plan('Poznań',nearby_cities=['Śrem'],topics=['oddam sprzedam']).queries
    assert any('Poznan' in query for query in city_variants)
    assert any('Srem' in query for query in city_variants)


def test_facebook_picker_requires_an_exact_group_title():
    match=FacebookPublisher._is_exact_group_label
    assert match("Ogłoszenia Poznań\nВаше последнее посещение: 2 ч. назад", "Ogłoszenia Poznan")
    assert not match("Ogłoszenia Poznań i okolice", "Ogłoszenia Poznań")
    assert not match("Ogłoszenia Poznań", "Ogłoszenia Poznań i okolice")


def test_group_has_no_cooldown_even_for_aliases(tmp_path: Path):
    db=Database(tmp_path/'app.db')
    now=datetime(2026,10,5,12,tzinfo=timezone.utc)
    bike=db.execute('INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)',('1','Bike',now.isoformat(),now.isoformat()))
    slug=db.execute('INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)',('Slug','https://www.facebook.com/groups/slug',now.isoformat(),now.isoformat()))
    numeric=db.execute('INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)',('Numeric','https://www.facebook.com/groups/123456',now.isoformat(),now.isoformat()))
    add_plan(db,[bike],[slug],now,30)
    job=db.one('SELECT * FROM queue WHERE group_id=?',(slug,))
    finish_delivery(db,job,'published','https://www.facebook.com/groups/slug/posts/9/','confirmed')
    confirmed_at=now.isoformat()
    db.execute('UPDATE deliveries SET attempted_at=?,confirmed_at=? WHERE queue_id=?',(confirmed_at,confirmed_at,job['id']))
    from bike_publisher.delivery import bind_identity
    bind_identity(db,slug,'https://www.facebook.com/groups/123456/')
    gate=group_gate(db,numeric,now=now+timedelta(minutes=10))
    assert gate['allowed']
    assert gate['seconds']==0
    assert group_gate(db,numeric,now=now+timedelta(minutes=31))['allowed']


def test_removing_catalog_group_preserves_my_groups_and_batch_job(tmp_path: Path):
    db=Database(tmp_path/'app.db')
    now=datetime.now(timezone.utc)
    bike=db.execute('INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)',('1','Bike',now.isoformat(),now.isoformat()))
    group=db.execute('INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)',('Group','https://www.facebook.com/groups/123',now.isoformat(),now.isoformat()))
    save_snapshot(db,'account',[{'name':'Group','url':'https://facebook.com/groups/123/'}])
    update_permissions(db,'account',[group])
    add_plan(db,[bike],[group],now+timedelta(hours=1),30,mode='joined_batch')
    result=remove_groups(db,[group])
    assert result['archived']==1 and result['cancelled_jobs']==0
    row=db.one('SELECT catalog,archived FROM groups_ WHERE id=?',(group,))
    access=db.one('SELECT allowed,present FROM joined_groups WHERE account_id=? AND group_id=?',('account',group))
    queued=db.one('SELECT status,mode FROM queue WHERE group_id=?',(group,))
    assert row=={'catalog':0,'archived':1}
    assert access=={'allowed':1,'present':1}
    assert queued=={'status':'pending','mode':'joined_batch'}


def test_remove_groups_preserves_queue_history_and_can_restore(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now.isoformat(), now.isoformat()),
    )
    group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Group", "https://www.facebook.com/groups/123", now.isoformat(), now.isoformat()),
    )
    add_plan(db, [bike], [group], now + timedelta(hours=1), 90)

    result = remove_groups(db, [group])
    assert result["archived"] == 1
    assert result["cancelled_jobs"] == 1
    assert db.one("SELECT archived FROM groups_ WHERE id=?", (group,))["archived"] == 1
    assert db.one("SELECT status FROM queue WHERE group_id=?", (group,))["status"] == "cancelled"
    assert restore_groups(db, [group]) == 1
    restored = db.one("SELECT archived,enabled FROM groups_ WHERE id=?", (group,))
    assert restored == {"archived": 0, "enabled": 0}

    unused_group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Unused", "https://www.facebook.com/groups/unused", now.isoformat(), now.isoformat()),
    )
    result = remove_groups(db, [unused_group])
    assert result["deleted"] == 1
    assert db.one("SELECT id FROM groups_ WHERE id=?", (unused_group,)) is None


def test_adding_hidden_group_reactivates_it_disabled_and_preserves_history(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now.isoformat(), now.isoformat()),
    )
    group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Old name", "https://www.facebook.com/groups/123", now.isoformat(), now.isoformat()),
    )
    add_plan(db, [bike], [group], now + timedelta(hours=1), 90)
    assert remove_groups(db, [group])["archived"] == 1

    status, reactivated_id = save_group(
        db,
        "Current name",
        "https://www.facebook.com/groups/123/",
        "Poznań",
        25,
        "pl",
    )
    assert (status, reactivated_id) == ("reactivated", group)
    restored = db.one(
        "SELECT name,city,distance_km,language,enabled,archived FROM groups_ WHERE id=?",
        (group,),
    )
    assert restored == {
        "name": "Current name",
        "city": "Poznań",
        "distance_km": 25.0,
        "language": "pl",
        "enabled": 0,
        "archived": 0,
    }
    assert db.one("SELECT COUNT(*) AS count FROM queue WHERE group_id=?", (group,))["count"] == 1
    assert save_group(db, "Current name", "facebook.com/groups/123", "", 0, "unknown")[0] == "exists"


def test_queue_deduplicates(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute("INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)", ("1", "Bike", now.isoformat(), now.isoformat()))
    group = db.execute("INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)", ("Group", "https://facebook.com/g", now.isoformat(), now.isoformat()))
    assert add_plan(db, [bike], [group], now, 90) == 1
    assert add_plan(db, [bike], [group], now, 90) == 0
    assert add_plan(db, [bike], [group], now + timedelta(days=1), 90) == 0


def test_photo_reorder_archive_and_restore(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc).isoformat()
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now, now),
    )
    other_bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("2", "Other bike", now, now),
    )
    ids = [
        db.execute(
            "INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)",
            (bike, f"photo-{index}.jpg", index - 1),
        )
        for index in range(1, 4)
    ]
    foreign_id = db.execute(
        "INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)",
        (other_bike, "other.jpg", 0),
    )

    assert reorder_photos(db, bike, [ids[2], ids[0], ids[1]])
    assert [row["id"] for row in db.all(
        "SELECT id FROM photos WHERE bike_id=? AND archived=0 ORDER BY sort_order,id", (bike,)
    )] == [ids[2], ids[0], ids[1]]
    assert not reorder_photos(db, bike, [ids[2], ids[0], ids[1]])

    with pytest.raises(ValueError):
        reorder_photos(db, bike, [ids[2], ids[0], foreign_id])
    assert archive_photo(db, bike, ids[2])
    assert db.one("SELECT archived FROM photos WHERE id=?", (ids[2],))["archived"] == 1
    assert [row["id"] for row in db.all(
        "SELECT id FROM photos WHERE bike_id=? AND archived=0 ORDER BY sort_order,id", (bike,)
    )] == [ids[0], ids[1]]
    assert restore_photo(db, bike, ids[2])
    assert [row["id"] for row in db.all(
        "SELECT id FROM photos WHERE bike_id=? AND archived=0 ORDER BY sort_order,id", (bike,)
    )] == [ids[0], ids[1], ids[2]]


def test_add_photos_appends_validated_uploads_for_selected_bike(tmp_path: Path):
    class Upload:
        def __init__(self, name: str, content: bytes):
            self.name = name
            self.content = content

        def getvalue(self):
            return self.content

    def image_bytes(image_format: str) -> bytes:
        output = BytesIO()
        Image.new("RGB", (8, 8), color="blue").save(output, format=image_format)
        return output.getvalue()

    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc).isoformat()
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now, now),
    )
    original_id = db.execute(
        "INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)",
        (bike, str(tmp_path / "existing.jpg"), 0),
    )

    uploads = [
        Upload("first.jpeg", image_bytes("JPEG")),
        Upload("second.png", image_bytes("PNG")),
    ]
    assert add_photos(db, bike, tmp_path / "data", uploads) == 2
    rows = db.all(
        "SELECT path,sort_order FROM photos WHERE bike_id=? AND archived=0 ORDER BY sort_order,id",
        (bike,),
    )
    assert [row["sort_order"] for row in rows] == [0, 1, 2]
    assert rows[0]["path"] == str(tmp_path / "existing.jpg")
    assert [Path(row["path"]).suffix for row in rows[1:]] == [".jpg", ".png"]
    assert all(Path(row["path"]).is_file() for row in rows[1:])
    assert db.one("SELECT id FROM photos WHERE id=?", (original_id,))

    with pytest.raises(ValueError):
        add_photos(db, bike, tmp_path / "data", [Upload("broken.jpg", b"not an image")])
    assert db.one("SELECT COUNT(*) AS count FROM photos WHERE bike_id=?", (bike,))["count"] == 3


def test_cancel_job_only_before_worker_starts(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now.isoformat(), now.isoformat()),
    )
    group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Group", "https://facebook.com/g", now.isoformat(), now.isoformat()),
    )
    add_plan(db, [bike], [group], now + timedelta(hours=1), 90)
    pending_id = db.one("SELECT id FROM queue")["id"]
    assert cancel_job(db, pending_id)
    assert db.one("SELECT status FROM queue WHERE id=?", (pending_id,))["status"] == "cancelled"
    assert not cancel_job(db, pending_id)

    add_plan(db, [bike], [group], now + timedelta(hours=3), 90, manual_approval=True)
    waiting_id = db.one("SELECT id FROM queue WHERE status='waiting_approval'")["id"]
    assert cancel_job(db, waiting_id)

    add_plan(db, [bike], [group], now + timedelta(hours=2), 90)
    running_id = db.one("SELECT id FROM queue WHERE status='pending'")["id"]
    db.execute("UPDATE queue SET status='running' WHERE id=?", (running_id,))
    assert not cancel_job(db, running_id)


def test_clear_audit_log_removes_only_log_entries(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("1", "Bike", now.isoformat(), now.isoformat()),
    )
    group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Group", "https://facebook.com/g", now.isoformat(), now.isoformat()),
    )
    add_plan(db, [bike], [group], now + timedelta(hours=1), 90)
    db.log("test", "entry")
    assert db.clear_audit_log() == 2
    assert db.all("SELECT * FROM audit_log") == []
    assert db.one("SELECT COUNT(*) AS count FROM queue")["count"] == 1


def test_empty_browser_profile_setting_uses_default(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BIKEBOT_BROWSER_PROFILE", "")
    settings = Settings.from_root(tmp_path)
    assert settings.browser_profile != tmp_path
    assert settings.browser_profile.name == "browser_profile"


def test_facebook_selectors_include_polish_ui():
    assert FacebookPublisher._composer_names().search("Napisz coś...")
    assert FacebookPublisher._submit_names().search("Opublikuj")


def test_facebook_uploads_all_photos_in_one_ordered_batch():
    preview = {"count": 0}

    class Locator:
        def __init__(self, *, visible=False, click_callback=None):
            self.visible = visible
            self.click_callback = click_callback
            self.click_count = 0

        def count(self):
            return int(self.visible)

        def nth(self, index):
            return self

        def is_visible(self, timeout=100):
            return self.visible

        def evaluate(self, _script):
            return {
                "src": "blob:preview",
                "width": 100,
                "height": 100,
                "naturalWidth": 100,
                "naturalHeight": 100,
            }

        def click(self):
            self.click_count += 1
            if self.click_callback:
                self.click_callback()

    class FileChooser:
        def __init__(self):
            self.files = []

        def is_multiple(self):
            return True

        def set_files(self, files):
            if isinstance(files, list):
                self.files.append(files)
                preview["count"] += len(files)
            else:
                self.files.append([files])
                preview["count"] += 1

    class ChooserContext:
        def __init__(self, chooser):
            self.value = chooser

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    chooser = FileChooser()
    button = Locator(visible=True)

    class Scope:
        def locator(self, *_args, **_kwargs):
            return self

        def inner_text(self, **_kwargs):
            return ""

        def get_by_role(self, *_args, **_kwargs):
            return button

        def get_by_text(self, *_args, **_kwargs):
            return Locator()

        def count(self):
            return preview["count"]

        def nth(self, _index):
            return Locator(visible=True)

        def is_visible(self, timeout=100):
            return True

    class Page(Scope):
        def __init__(self):
            self.waits = []

        def expect_file_chooser(self, **_kwargs):
            return ChooserContext(chooser)

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

        def locator(self, *_args, **_kwargs):
            raise AssertionError("Batch chooser should prevent fallback input lookup")

    paths = ["bike/front.jpg", "bike/side.jpg", "bike/frame.jpg"]
    page = Page()
    publisher = FacebookPublisher(SimpleNamespace(action_delay_seconds=0.5))
    publisher._upload_photos(page, paths, Scope(), verify_order=False)

    assert chooser.files == [paths]
    assert button.click_count == 1
    assert page.waits == []


def test_facebook_fallback_input_receives_one_photo_at_a_time():
    preview = {"count": 0}

    class Locator:
        def __init__(self, visible=False):
            self.visible = visible

        def count(self):
            return int(self.visible)

        def nth(self, _index):
            return self

        def is_visible(self, timeout=100):
            return self.visible

        def evaluate(self, _script):
            return {
                "src": "blob:preview",
                "width": 100,
                "height": 100,
                "naturalWidth": 100,
                "naturalHeight": 100,
            }

    class FileInput(Locator):
        def __init__(self):
            super().__init__(visible=True)
            self.files = []

        @property
        def first(self):
            return self

        def set_input_files(self, files):
            assert isinstance(files, list)
            self.files.append(files)
            preview["count"] += len(files)

        def get_attribute(self, name):
            return "image/*" if name == "accept" else None

    class Scope:
        def locator(self, *_args, **_kwargs):
            return self

        def inner_text(self, **_kwargs):
            return ""

        def count(self):
            return preview["count"]

        def nth(self, _index):
            return Locator(visible=True)

        def get_by_role(self, *_args, **_kwargs):
            return Locator()

        def get_by_text(self, *_args, **_kwargs):
            return Locator()

    class Page(Scope):
        def __init__(self):
            self.file_input = FileInput()
            self.waits = []

        def locator(self, *_args, **_kwargs):
            return self.file_input

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    paths = ["bike/front.jpg", "bike/side.jpg", "bike/frame.jpg"]
    page = Page()
    publisher = FacebookPublisher(SimpleNamespace(action_delay_seconds=0.5))
    publisher._upload_photos(page, paths, Scope(), verify_order=False)

    assert page.file_input.files == [paths]
    assert page.waits == []


def test_facebook_multiple_file_input_receives_one_ordered_batch():
    preview = {"count": 0}

    class PreviewScope:
        def locator(self, *_args, **_kwargs):
            return self

        def count(self):
            return preview["count"]

        def nth(self, _index):
            return self

        def is_visible(self, timeout=100):
            return True

        def evaluate(self, _script):
            return {
                "src": "blob:preview",
                "width": 100,
                "height": 100,
                "naturalWidth": 100,
                "naturalHeight": 100,
            }

        def inner_text(self, **_kwargs):
            return ""

    class FileInput:
        def __init__(self):
            self.files = []

        def count(self):
            return 1

        def nth(self, _index):
            return self

        @property
        def first(self):
            return self

        def get_attribute(self, name):
            return "image/*" if name == "accept" else None

        def set_input_files(self, files):
            assert isinstance(files, list)
            self.files.append(files)
            preview["count"] += len(files)

    class Page:
        def __init__(self):
            self.file_input = FileInput()
            self.waits = []

        def locator(self, *_args, **_kwargs):
            return self.file_input

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    paths = [f"bike/photo-{index}.jpg" for index in range(8)]
    page = Page()
    publisher = FacebookPublisher(SimpleNamespace(action_delay_seconds=0.5))
    publisher._upload_photos(page, paths, PreviewScope(), verify_order=False)

    assert page.file_input.files == [paths]
    assert page.waits == []


def test_facebook_waits_once_for_the_complete_photo_batch():
    state = {"visible": 0, "pending": 0}

    class Image:
        def is_visible(self, timeout=100):
            return True

        def evaluate(self, _script):
            return {
                "src": "blob:preview",
                "width": 100,
                "height": 100,
                "naturalWidth": 100,
                "naturalHeight": 100,
            }

    class Images:
        def count(self):
            return state["visible"]

        def nth(self, _index):
            return Image()

    class FileInput:
        files = []

        def count(self):
            return 1

        def nth(self, _index):
            return self

        @property
        def first(self):
            return self

        def get_attribute(self, _name):
            return ""

        def set_input_files(self, path):
            assert isinstance(path, list)
            self.files.append(path)
            state["pending"] = state["visible"] + len(path)

    class Scope:
        def locator(self, *_args, **_kwargs):
            return Images()

        def inner_text(self, **_kwargs):
            return ""

    class Page:
        def __init__(self):
            self.file_input = FileInput()
            self.waits = []

        def locator(self, *_args, **_kwargs):
            return self.file_input

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)
            if milliseconds == 500:
                state["visible"] = state["pending"]

    paths = ["bike/main.jpg", "bike/second.jpg", "bike/third.jpg"]
    page = Page()
    FacebookPublisher(SimpleNamespace(action_delay_seconds=0.5))._upload_photos(
        page, paths, Scope(), verify_order=False
    )

    assert page.file_input.files == [paths]
    assert page.waits == [500]


@pytest.mark.parametrize(
    ("collapsed_label", "expected"),
    [
        ("и еще 3", 7),
        ("and 3 more", 7),
        ("i jeszcze 3", 7),
        ("i 3 więcej", 7),
        ("и еще 2", 6),
    ],
)
def test_facebook_preview_count_includes_collapsed_photos(
    collapsed_label, expected
):
    class Image:
        def is_visible(self, timeout=100):
            return True

        def evaluate(self, _script):
            return {
                "src": "blob:preview",
                "width": 100,
                "height": 100,
                "naturalWidth": 100,
                "naturalHeight": 100,
            }

    class Images:
        def count(self):
            return 5

        def nth(self, _index):
            return Image()

    class Scope:
        def locator(self, *_args, **_kwargs):
            return Images()

        def inner_text(self, **_kwargs):
            return collapsed_label

    assert FacebookPublisher._photo_preview_count(Scope()) == expected


def test_facebook_preview_count_recognizes_uploaded_remote_thumbnails():
    class Image:
        def is_visible(self, timeout=100):
            return True

        def evaluate(self, _script):
            return {
                "src": "https://scontent.example/photo.jpg",
                "width": 180,
                "height": 140,
                "naturalWidth": 1600,
                "naturalHeight": 1200,
            }

    class Images:
        def count(self):
            return 6

        def nth(self, _index):
            return Image()

    class Scope:
        def locator(self, *_args, **_kwargs):
            return Images()

        def inner_text(self, **_kwargs):
            return ""

    assert FacebookPublisher._photo_preview_count(Scope()) == 6


def test_post_text_uses_only_price_and_description():
    text = FacebookPublisher._build_text(
        {
            "bike_name": "Велосипед №9",
            "price": "1200",
            "description": "Готов к поездкам",
            "frame": "XL",
            "wheels": "29",
            "fork": "RockShox",
        }
    )
    assert text == "Готов к поездкам\n\nCena: 1200 zł"
    assert "Велосипед №9" not in text


def test_post_text_does_not_repeat_price_already_in_description():
    text = FacebookPublisher._build_text(
        {"price": "4200", "description": "Cena: 4200 zł – do negocjacji."}
    )
    assert text == "Do negocjacji.\n\nCena: 4200 zł"


def test_worker_processes_pending_job(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    now = datetime.now(timezone.utc)
    bike = db.execute(
        "INSERT INTO bikes(external_id,name,price,description,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("9", "Bike", "1200", "Ready", now.isoformat(), now.isoformat()),
    )
    group = db.execute(
        "INSERT INTO groups_(name,url,created_at,updated_at) VALUES(?,?,?,?)",
        ("Group", "https://facebook.com/groups/test", now.isoformat(), now.isoformat()),
    )
    add_plan(db, [bike], [group], now, 90)
    ordered_paths = ["bike/main.jpg", "bike/detail.jpg", "bike/front.jpg"]
    stored_photo_rows = [
        ("bike/front.jpg", 2),
        ("bike/main.jpg", 0),
        ("bike/detail.jpg", 1),
    ]
    for path, sort_order in stored_photo_rows:
        db.execute(
            "INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)",
            (bike, path, sort_order),
        )

    class FakePublisher:
        def publish(self, job, photo_paths):
            assert job["description"] == "Ready"
            assert photo_paths == ordered_paths
            return "https://facebook.com/groups/test/posts/1"

    result = run_once(db, FakePublisher())
    assert result and result.success
    assert db.one("SELECT status FROM queue WHERE id=?", (result.job_id,))["status"] == "sent"
