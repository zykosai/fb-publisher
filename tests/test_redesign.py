from datetime import datetime, timezone, timedelta

import pytest

from bike_publisher.control import PublicationPaused
from bike_publisher.db import Database, utc_now
from bike_publisher.facebook import BatchUnavailable, UnsupportedGroupError
from bike_publisher.joined_groups import save_snapshot, update_permissions, save_group_check
from bike_publisher.queue import add_plan, get_next_job
from bike_publisher.runner import run_once
from bike_publisher.process_lock import worker_lock
from zoneinfo import ZoneInfo


def plan(tmp_path, bikes=2, groups=3):
    db = Database(tmp_path / 'app.db')
    db.set_setting('publication_paused', False)
    db.set_setting('facebook_account_id', 'account')
    bike_ids = [db.execute('INSERT INTO bikes(external_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)', (str(i), f'Bike {i}', 'Description', utc_now(), utc_now())) for i in range(bikes)]
    save_snapshot(db, 'account', [{'name': f'Group {i}', 'url': f'https://facebook.com/groups/group{i}'} for i in range(groups)])
    group_ids = [row['id'] for row in db.all('SELECT id FROM groups_ ORDER BY id')]
    for group_id in group_ids:
        save_group_check(db, 'account', group_id, {'state': 'ready', 'joined': True})
    update_permissions(db, 'account', group_ids)
    add_plan(db, bike_ids, group_ids, datetime.now(timezone.utc)-timedelta(seconds=5), 0, mode='joined_batch')
    return db, bike_ids, group_ids


def test_batch_does_not_mix_bicycles_and_next_interval_starts_at_completion(tmp_path):
    db, bikes, groups = plan(tmp_path)
    first = get_next_job(db)
    db.set_setting(f"plan:{first['plan_id']}", {'delay_minutes': 60})
    calls = []
    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            calls.append(jobs)
            assert {row['bike_id'] for row in jobs} == {bikes[0]}
            before_submit(jobs)
            return {row['id']: ('sent', '', '') for row in jobs}
    assert run_once(db, Publisher()).success
    assert len(calls[0]) == len(groups)
    assert get_next_job(db) is None
    next_bike = db.one('SELECT * FROM queue WHERE bike_id=?', (bikes[1],))
    assert datetime.fromisoformat(next_bike['scheduled_at']) > datetime.now(timezone.utc)+timedelta(minutes=59)


def test_pause_returns_unsubmitted_packet_to_pending(tmp_path):
    db, bikes, groups = plan(tmp_path, bikes=1)
    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            db.set_setting('publication_paused', True)
            before_submit(jobs)
            pytest.fail('Submit must never be reached')
    result = run_once(db, Publisher())
    assert not result.success
    assert all(row['status']=='pending' and row['attempts']==0 for row in db.all('SELECT * FROM queue'))
    assert db.all('SELECT * FROM deliveries') == []
    assert get_next_job(db) is None


def test_sell_only_first_group_does_not_block_other_groups(tmp_path):
    db, bikes, groups = plan(tmp_path, bikes=1)
    calls = []
    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            calls.append(jobs[0]['group_id'])
            if jobs[0]['group_id'] == groups[0]:
                raise UnsupportedGroupError('Only Sell')
            before_submit(jobs)
            return {row['id']: ('sent', '', '') for row in jobs}
    assert run_once(db, Publisher()).success
    assert calls == groups[:2]
    assert [row['status'] for row in db.all('SELECT status FROM queue ORDER BY id')] == ['skipped','sent','sent']


def test_missing_picker_never_falls_back_to_individual_posts(tmp_path):
    db, bikes, groups = plan(tmp_path, bikes=1)
    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            raise BatchUnavailable('No group picker')
        def publish(self, *args, **kwargs):
            pytest.fail('Single-group fallback is forbidden')
    assert not run_once(db, Publisher()).success
    assert [row['status'] for row in db.all('SELECT status FROM queue ORDER BY id')] == ['failed', 'pending', 'pending']


def test_second_packet_completes_same_bike_before_next(tmp_path):
    db, bikes, groups = plan(tmp_path, groups=12)
    calls = []
    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            selected = jobs[:10]
            calls.append((selected[0]['bike_id'], len(selected)))
            before_submit(selected)
            return {row['id']: ('sent' if row in selected else 'deferred', '', '') for row in jobs}
    publisher = Publisher()
    run_once(db, publisher)
    run_once(db, publisher)
    run_once(db, publisher)
    assert calls == [(bikes[0],10),(bikes[0],2),(bikes[1],10)]


def test_all_37_groups_complete_before_next_bicycle(tmp_path):
    db, bikes, _ = plan(tmp_path, groups=37)
    packets = []

    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            selected = jobs[:10]
            packets.append([row['id'] for row in selected])
            before_submit(selected)
            return {row['id']: ('published' if row in selected else 'deferred', '', '') for row in jobs}

    publisher = Publisher()
    for _ in range(4):
        assert run_once(db, publisher).success
    assert [len(packet) for packet in packets] == [10, 10, 10, 7]
    assert len(set(sum(packets, []))) == 37
    assert db.one("SELECT COUNT(*) n FROM queue WHERE bike_id=? AND status='published'", (bikes[0],))['n'] == 37
    assert db.one("SELECT COUNT(*) n FROM queue WHERE bike_id=? AND status='pending'", (bikes[1],))['n'] == 37


def test_missing_additional_groups_rotate_to_primary_and_are_not_lost(tmp_path):
    db, bikes, groups = plan(tmp_path, bikes=1, groups=37)
    packets = []
    invisible = set(groups[1:15])

    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            selected = [jobs[0]] + [row for row in jobs[1:] if row['group_id'] not in invisible][:9]
            packets.append([row['group_id'] for row in selected])
            before_submit(selected)
            return {row['id']: ('published' if row in selected else 'deferred', '', '') for row in jobs}

    publisher = Publisher()
    for _ in range(40):
        if get_next_job(db) is None:
            break
        run_once(db, publisher)
    assert len(set(sum(packets, []))) == len(sum(packets, [])) == 37
    assert all(row['status'] == 'published' for row in db.all('SELECT status FROM queue'))


def test_error_after_partial_submit_does_not_fail_unselected_groups(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=37)

    class Publisher:
        def publish_batch(self, jobs, photos, before_submit):
            before_submit(jobs[:8])
            raise RuntimeError('Connection lost after clicking Submit')

    run_once(db, Publisher())
    counts = {row['status']: row['n'] for row in db.all('SELECT status,COUNT(*) n FROM queue GROUP BY status')}
    assert counts == {'unknown': 8, 'pending': 29}


def test_existing_avatar_labels_are_cleaned_without_restoring_archived_groups(tmp_path):
    db, _, groups = plan(tmp_path, bikes=1, groups=1)
    db.execute('UPDATE groups_ SET name=?,archived=1 WHERE id=?', ('Фото профиля Ogłoszenia Śrem i okolice', groups[0]))
    db = Database(db.path)
    group = db.one('SELECT name,archived FROM groups_ WHERE id=?', (groups[0],))
    assert group == {'name': 'Ogłoszenia Śrem i okolice', 'archived': 1}


def test_pause_between_packets_is_persisted_without_advancing_next_bike(tmp_path):
    from types import SimpleNamespace
    db, bikes, _ = plan(tmp_path, groups=37)

    class Publisher:
        settings = SimpleNamespace(between_posts_seconds=60)

        def publish_batch(self, jobs, photos, before_submit):
            before_submit(jobs[:10])
            return {row['id']: ('published' if row in jobs[:10] else 'deferred', '', '') for row in jobs}

    run_once(db, Publisher())
    assert get_next_job(db) is None
    remaining = db.all("SELECT scheduled_at FROM queue WHERE bike_id=? AND status='pending'", (bikes[0],))
    assert len(remaining) == 27
    assert all(datetime.fromisoformat(row['scheduled_at']) > datetime.now(timezone.utc) + timedelta(seconds=58) for row in remaining)


def test_polish_schedule_is_stored_in_utc(tmp_path):
    db, bikes, groups = plan(tmp_path, bikes=1, groups=1)
    db.execute("DELETE FROM queue")
    start = datetime(2026, 10, 8, 14, tzinfo=ZoneInfo('Europe/Warsaw'))
    add_plan(db, bikes, groups, start, 90, mode='joined_batch')
    scheduled = datetime.fromisoformat(db.one('SELECT scheduled_at FROM queue')['scheduled_at'])
    assert scheduled == datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    assert scheduled.utcoffset() == timedelta(0)


def test_only_one_worker_can_hold_the_process_lock(tmp_path):
    path = tmp_path/'worker.lock'
    with worker_lock(path) as first:
        assert first
        with worker_lock(path) as second:
            assert not second
    with worker_lock(path) as after_release:
        assert after_release
