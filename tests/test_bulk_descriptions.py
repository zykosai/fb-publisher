import pytest

from bike_publisher.bulk_descriptions import preview_descriptions, apply_descriptions, undo_descriptions
from bike_publisher.db import Database, utc_now


@pytest.fixture
def catalog(tmp_path):
    db = Database(tmp_path / 'app.db')
    db.set_setting('facebook_account_id', 'A')
    for account, status in [('A', 'active'), ('A', 'sold'), ('B', 'active'), ('A', 'deleted')]:
        db.execute('''INSERT INTO bikes(external_id,name,owner_account_id,status,description,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)''', (account+status, account+status, account, status,
            '⚠️ WIĘCEJ ROWERÓW ZNAJDZIESZ NA MOIM PROFILU! ⚠️\nKoła: 26. Widoczne rysy.', utc_now(), utc_now()))
    return db


def test_preview_apply_undo_and_account_isolation(catalog):
    db = catalog
    originals = db.all('SELECT id,description FROM bikes ORDER BY id')
    preview = preview_descriptions(db, {'mode': 'remove_promo'})
    assert len(preview['edits']) == 2
    assert db.all('SELECT id,description FROM bikes ORDER BY id') == originals
    assert apply_descriptions(db, preview['token']) == 2
    assert db.one('SELECT description FROM bikes WHERE id=1')['description'] == 'Koła: 26. Widoczne rysy.'
    assert db.one('SELECT description FROM bikes WHERE id=3')['description'] == originals[2]['description']
    assert db.one('SELECT description FROM bikes WHERE id=4')['description'] == originals[3]['description']
    with pytest.raises(ValueError): apply_descriptions(db, preview['token'])
    assert undo_descriptions(db) == 2
    assert db.all('SELECT id,description FROM bikes ORDER BY id') == originals


@pytest.mark.parametrize('phrase', ['Wiecej rowerow w profilu', 'Więcej rowerów na moim profilu!', '⚠ WIĘCEJ ROWERÓW ZNAJDZIESZ NA MOIM PROFILU! ⚠'])
def test_profile_variations(catalog, phrase):
    catalog.execute('UPDATE bikes SET description=? WHERE id=1', (phrase+'\nOpis zostaje.',))
    preview = preview_descriptions(catalog, {'mode': 'remove_promo'})
    assert preview['edits'][0]['after'] == 'Opis zostaje.'


def test_conflicts_roll_back_all_changes(catalog):
    preview = preview_descriptions(catalog, {'mode': 'replace', 'find': '26', 'replacement': '28'})
    catalog.execute("UPDATE bikes SET description='Manual edit' WHERE id=2")
    before = catalog.all('SELECT description FROM bikes ORDER BY id')
    with pytest.raises(ValueError): apply_descriptions(catalog, preview['token'])
    assert catalog.all('SELECT description FROM bikes ORDER BY id') == before


def test_foreign_token_and_empty_description_are_rejected(catalog):
    preview = preview_descriptions(catalog, {'mode': 'remove_promo'})
    catalog.set_setting('facebook_account_id', 'B')
    with pytest.raises(ValueError): apply_descriptions(catalog, preview['token'])
    with pytest.raises(ValueError): preview_descriptions(catalog, {'mode': 'replace', 'find': ''})
    catalog.execute("UPDATE bikes SET description='Entire text' WHERE id=3")
    with pytest.raises(ValueError): preview_descriptions(catalog, {'mode': 'replace', 'find': 'Entire text', 'replacement': ''})


def test_undo_does_not_overwrite_manual_edits(catalog):
    preview = preview_descriptions(catalog, {'mode': 'remove_promo'})
    apply_descriptions(catalog, preview['token'])
    catalog.execute("UPDATE bikes SET description='New description' WHERE id=1")
    with pytest.raises(ValueError): undo_descriptions(catalog)
    assert catalog.one('SELECT description FROM bikes WHERE id=1')['description'] == 'New description'
