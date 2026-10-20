from concurrent.futures import ThreadPoolExecutor
import re

import pytest

from bike_publisher.db import Database
from bike_publisher.pairing import pairing_key


def test_key_survives_database_reopen(tmp_path):
    path = tmp_path / 'app.db'
    key = pairing_key(Database(path))
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', key)
    assert pairing_key(Database(path)) == key


def test_each_installation_has_its_own_key(tmp_path):
    assert pairing_key(Database(tmp_path / 'one.db')) != pairing_key(Database(tmp_path / 'two.db'))


def test_concurrent_creation_returns_one_key(tmp_path):
    db = Database(tmp_path / 'app.db')
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: pairing_key(db), range(8)))
    assert len(set(keys)) == 1


@pytest.mark.parametrize('value', ['', 'weak', None, 123, ['bad']])
def test_invalid_saved_key_is_replaced_once(tmp_path, value):
    db = Database(tmp_path / 'app.db')
    db.set_setting('web_pairing_key', value)
    key = pairing_key(db)
    assert key != value
    assert pairing_key(db) == key


def test_malformed_json_is_replaced(tmp_path):
    db = Database(tmp_path / 'app.db')
    db.execute("INSERT INTO settings(key,value) VALUES('web_pairing_key','{broken')")
    key = pairing_key(db)
    assert pairing_key(db) == key
