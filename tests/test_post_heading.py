from types import SimpleNamespace

import pytest

from bike_publisher.db import Database, utc_now
from bike_publisher.queue import get_job
from bike_publisher.webapp import Application


def test_saved_heading_preview_ownership_validation_and_queue(tmp_path):
    db = Database(tmp_path / 'app.db')
    db.set_setting('facebook_account_id', 'A')
    product = db.execute('''INSERT INTO bikes(external_id,name,owner_account_id,price,description,created_at,updated_at)
        VALUES('1','Catalog name','A','500','Original description',?,?)''', (utc_now(), utc_now()))
    app = Application.__new__(Application)
    app.db, app.settings, app.operation = db, SimpleNamespace(), {}
    data = {'price': '500', 'description': 'Original description', 'post_heading': '  My heading  '}
    app.action(f'/api/bikes/{product}', data)
    row = db.one('SELECT * FROM bikes WHERE id=?', (product,))
    assert row['post_heading'] == 'My heading'
    assert row['name'] == 'Catalog name'
    assert row['description'] == 'Original description'
    assert app.action('/api/post/preview', data)['text'] == 'My heading\n\nOriginal description\n\nCena: 500 zł'
    # Old clients editing other fields must preserve the custom heading.
    app.action(f'/api/bikes/{product}', {'price': '600', 'description': 'Original description'})
    assert db.one('SELECT post_heading FROM bikes')['post_heading'] == 'My heading'
    group = db.execute("INSERT INTO groups_(name,url,created_at,updated_at) VALUES('Group','https://facebook.com/groups/test',?,?)", (utc_now(), utc_now()))
    job = db.execute('INSERT INTO queue(bike_id,group_id,scheduled_at,created_at,updated_at) VALUES(?,?,?,?,?)', (product, group, utc_now(), utc_now(), utc_now()))
    assert get_job(db, job)['post_heading'] == 'My heading'
    for invalid in ['a\nb', 'a' * 161, None]:
        with pytest.raises(ValueError): app.action(f'/api/bikes/{product}', {**data, 'post_heading': invalid})
    assert db.one('SELECT post_heading FROM bikes')['post_heading'] == 'My heading'
    db.set_setting('facebook_account_id', 'B')
    with pytest.raises(ValueError): app.action(f'/api/bikes/{product}', data)
    db.set_setting('facebook_account_id', 'A')
    app.action(f'/api/bikes/{product}', {**data, 'post_heading': ''})
    assert db.one('SELECT post_heading FROM bikes')['post_heading'] == ''
    assert Database(db.path).one('SELECT description FROM bikes')['description'] == 'Original description'
