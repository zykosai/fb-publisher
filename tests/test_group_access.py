import pytest

from bike_publisher.db import Database
from bike_publisher.group_access import classify_access
from bike_publisher.group_checks import get_group_check
from bike_publisher.joined_groups import save_group_check, save_snapshot, update_permissions


def test_screenshot_pending_limit_is_not_rejection():
    text = 'Ожидает подтверждения администратора\n21 публикация\nПодробнее\nДостигнуто ограничение по контенту на рассмотрении в этой группе.'
    result = classify_access(text, membership=True, composer=True)
    assert result['state'] == 'moderation_limit'
    assert result['pending_count'] == 21
    assert result['rejected_count'] is None
    assert result['joined'] is True


@pytest.mark.parametrize(('text', 'count'), [
    ('Ожидает подтверждения администратора\n4 публикации', 4),
    ('Pending posts (4)', 4),
    ('Awaiting admin approval\n4 posts', 4),
    ('Oczekujące posty\n4 posty', 4),
    ('Ожидает подтверждения администратора\n1 234 публикации', 1234),
])
def test_pending_counts_do_not_block_usable_composer(text, count):
    result = classify_access(text, membership=True, composer=True)
    assert result['state'] == 'ready'
    assert result['pending_count'] == count
    assert result['rejected_count'] is None


@pytest.mark.parametrize(('text', 'state'), [
    ('Вас заблокировали в этой группе', 'blocked'),
    ("You've been blocked from this group", 'blocked'),
    ('Zablokowano Cię w tej grupie', 'blocked'),
    ('Вас исключили из группы', 'removed'),
    ('You were removed from this group', 'removed'),
    ('Usunięto Cię z tej grupy', 'removed'),
    ('You have reached the pending post limit', 'moderation_limit'),
    ('Osiągnięto limit oczekujących postów', 'moderation_limit'),
])
def test_explicit_restriction_wins_over_composer(text, state):
    assert classify_access(text, composer=True, membership=True)['state'] == state


def test_pending_is_never_invented_as_rejection_or_ban():
    result = classify_access('На проверке\nПодробнее\n12 октября 2026', membership=True)
    assert result['state'] == 'moderation'
    assert result['pending_count'] is None
    assert result['rejected_count'] is None
    assert classify_access('', membership=True)['state'] == 'unavailable'
    assert classify_access('', membership=False)['state'] == 'not_member'


def test_rejection_count_requires_explicit_label():
    result = classify_access('Pending posts (21)\nDeclined posts (4)', composer=True)
    assert result['pending_count'] == 21
    assert result['rejected_count'] == 4
    assert result['state'] == 'ready'


def test_membership_pending_is_not_post_moderation():
    result = classify_access('', join_pending=True, composer=True)
    assert result['state'] == 'pending'
    assert result['joined'] is False


def test_unknown_membership_kept_and_nonready_unselected(tmp_path):
    db = Database(tmp_path / 'app.db')
    save_snapshot(db, 'me', [{'name': 'Group', 'url': 'https://facebook.com/groups/group'}])
    group = db.one('SELECT id FROM groups_')['id']
    update_permissions(db, 'me', [group])
    save_group_check(db, 'me', group, classify_access('Вас заблокировали в этой группе', membership=True))
    membership = db.one("SELECT * FROM joined_groups WHERE account_id='me'")
    assert membership['present'] == 1
    assert membership['allowed'] == 0
    assert get_group_check(db, group, 'me')['state'] == 'blocked'
    save_group_check(db, 'me', group, classify_access('', membership=False))
    assert db.one("SELECT present FROM joined_groups WHERE account_id='me'")['present'] == 0
    save_group_check(db, 'me', group, classify_access('', membership=True, composer=True))
    assert db.one("SELECT present FROM joined_groups WHERE account_id='me'")['present'] == 1
