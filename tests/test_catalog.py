from types import SimpleNamespace

import pytest

from bike_publisher.catalog import clear_history, price_cents, sales_summary, set_bike_status
from bike_publisher.db import Database, utc_now
from bike_publisher.runner import run_job
from bike_publisher.webapp import Application


@pytest.fixture
def catalog(tmp_path):
    db = Database(tmp_path / 'app.db')
    bike = db.execute("INSERT INTO bikes(external_id,name,price,description,created_at,updated_at) VALUES('4','Bike 4','1 600,50 zł','Description',?,?)", (utc_now(), utc_now()))
    group = db.execute("INSERT INTO groups_(name,url,created_at,updated_at) VALUES('Group','https://facebook.com/groups/test',?,?)", (utc_now(), utc_now()))
    def job(status='pending', plan='test'):
        return db.execute("INSERT INTO queue(bike_id,group_id,scheduled_at,status,plan_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (bike, group, utc_now() + plan, status, plan, utc_now(), utc_now()))
    return db, bike, job


def test_sale_is_reversible_snapshot_and_idempotent(catalog):
    db, bike, job = catalog
    pending = job()
    set_bike_status(db, bike, 'sold')
    assert sales_summary(db) == {'count': 1, 'total': '1600.50'}
    assert db.one('SELECT status FROM queue WHERE id=?', (pending,))['status'] == 'cancelled'
    db.execute("UPDATE bikes SET price='2000' WHERE id=?", (bike,))
    set_bike_status(db, bike, 'sold')
    assert sales_summary(db)['total'] == '1600.50'
    set_bike_status(db, bike, 'active')
    assert sales_summary(db) == {'count': 0, 'total': '0.00'}
    assert db.one('SELECT status FROM queue WHERE id=?', (pending,))['status'] == 'cancelled'
    set_bike_status(db, bike, 'sold')
    assert sales_summary(db)['total'] == '2000.00'


def test_running_bike_cannot_be_sold_or_deleted(catalog):
    db, bike, job = catalog
    job('running')
    for status in ('sold', 'deleted'):
        with pytest.raises(ValueError, match='паузу'):
            set_bike_status(db, bike, status)
    assert sales_summary(db)['count'] == 0


def test_runner_does_not_publish_sold_bike_even_with_stale_job(catalog):
    db, bike, job = catalog
    set_bike_status(db, bike, 'sold')
    stale = job()
    result = run_job(db, object(), stale)
    assert not result.success
    assert db.one('SELECT status FROM queue WHERE id=?', (stale,))['status'] == 'cancelled'


def test_clear_history_preserves_receipts_stats_and_active_packets(catalog):
    db, bike, job = catalog
    done = job('published', 'done')
    pending = job('pending', 'active')
    db.execute("INSERT INTO deliveries(queue_id,group_id,bike_id,state,attempted_at) SELECT id,group_id,bike_id,'published',updated_at FROM queue WHERE id=?", (done,))
    clear_history(db)
    assert db.one('SELECT history_hidden FROM queue WHERE id=?', (done,))['history_hidden'] == 1
    assert db.one('SELECT history_hidden FROM queue WHERE id=?', (pending,))['history_hidden'] == 0
    assert db.one('SELECT COUNT(*) n FROM deliveries')['n'] == 1
    assert db.one("SELECT COUNT(*) n FROM queue WHERE status='published'")['n'] == 1
    clear_history(db)


def test_delete_keeps_photos_and_removes_catalog_entry(catalog):
    db, bike, job = catalog
    job()
    db.execute("INSERT INTO photos(bike_id,path) VALUES(?,'photo.jpg')", (bike,))
    set_bike_status(db, bike, 'deleted')
    assert db.one('SELECT status FROM bikes WHERE id=?', (bike,))['status'] == 'deleted'
    assert db.one('SELECT COUNT(*) n FROM photos')['n'] == 1
    with pytest.raises(ValueError):
        set_bike_status(db, bike, 'active')


def test_sold_api_requires_explicit_boolean(catalog):
    db, bike, _ = catalog
    app = Application.__new__(Application)
    app.db = db
    app.settings = SimpleNamespace()
    with pytest.raises(ValueError):
        app.action(f'/api/bikes/{bike}/sold', {'sold': 'false'})
    app.action(f'/api/bikes/{bike}/sold', {'sold': True})
    app.action(f'/api/bikes/{bike}/sold', {'sold': False})
    assert sales_summary(db)['count'] == 0


def test_plan_api_rejects_sold_bike(catalog):
    db, bike, _ = catalog
    set_bike_status(db, bike, 'sold')
    app = Application.__new__(Application)
    app.db = db
    with pytest.raises(ValueError, match='нельзя публиковать'):
        app.action('/api/plan', {'bikes': [bike], 'groups': [1], 'delay': 90})


def test_state_keeps_sold_bike_and_hides_cleared_history(catalog, monkeypatch):
    from bike_publisher import webapp
    db, bike, job = catalog
    job('published', 'done')
    set_bike_status(db, bike, 'sold')
    app = Application.__new__(Application)
    app.db = db
    app.settings = SimpleNamespace()
    app.operation = {}
    monkeypatch.setattr(webapp, 'worker_state', lambda _: SimpleNamespace(running=False))
    before = app.state()
    assert before['bikes'][0]['status'] == 'sold'
    assert before['sales']['count'] == 1
    assert len(before['history']) == 1
    app.action('/api/history/clear', {})
    after = app.state()
    assert after['history'] == []
    assert after['sales'] == before['sales']
    assert after['sent_today'] == before['sent_today']


@pytest.mark.parametrize('value', ['', '-10', 'oops', 'NaN', '100.001', '100 EUR'])
def test_invalid_sale_price(value):
    with pytest.raises(ValueError):
        price_cents(value)


@pytest.mark.parametrize(('value', 'expected'), [('450', 45000), ('1\u00a0600 zł', 160000), ('25,99 PLN', 2599), ('0', 0)])
def test_price_formats(value, expected):
    assert price_cents(value) == expected
