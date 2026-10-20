from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bike_publisher.control import PublicationPaused
from bike_publisher.db import Database
from bike_publisher.delivery import finish_delivery, mark_attempt, recover_interrupted
from bike_publisher.facebook import FacebookPublisher
from bike_publisher.runner import run_due_checks, schedule_delivery_check
from test_redesign import plan


def test_check_waits_two_minutes_and_survives_restart(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=1)
    job = db.one('SELECT * FROM queue')
    finish_delivery(db, job, 'sent')
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    schedule_delivery_check(db, job['id'], now=now)
    db = Database(db.path)

    class Publisher:
        calls = 0

        def check_deliveries(self, jobs):
            self.calls += 1
            return {row['id']: ('published', 'post-url', 'Verified') for row in jobs}

    publisher = Publisher()
    assert not run_due_checks(db, publisher, now=now + timedelta(seconds=119))
    assert publisher.calls == 0
    db.set_setting('publication_paused', True)
    assert not run_due_checks(db, publisher, now=now + timedelta(minutes=2))
    assert publisher.calls == 0
    db.set_setting('publication_paused', False)
    assert run_due_checks(db, publisher, now=now + timedelta(minutes=2))
    assert publisher.calls == 1
    assert db.one('SELECT status FROM queue')['status'] == 'published'
    assert not db.all('SELECT * FROM delivery_checks')
    assert not run_due_checks(db, publisher, now=now + timedelta(minutes=3))


def test_unknown_is_not_republished_and_moderation_is_preserved(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=2)
    jobs = db.all('SELECT * FROM queue ORDER BY id')
    now = datetime.now(timezone.utc)
    for job, state in zip(jobs, ('unknown', 'moderation')):
        finish_delivery(db, job, state)
        schedule_delivery_check(db, job['id'], now=now - timedelta(minutes=3))

    class Publisher:
        def check_deliveries(self, jobs):
            return {row['id']: ('unknown', '', 'Not found') for row in jobs}

    assert run_due_checks(db, Publisher(), now=now)
    assert [row['status'] for row in db.all('SELECT status FROM queue ORDER BY id')] == ['unknown', 'moderation']
    assert not db.all('SELECT * FROM delivery_checks')


def test_pause_during_read_keeps_pending_checks(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=1)
    job = db.one('SELECT * FROM queue')
    schedule_delivery_check(db, job['id'], now=datetime.now(timezone.utc) - timedelta(minutes=3))

    class Publisher:
        def check_deliveries(self, jobs):
            raise PublicationPaused('Paused')

    assert not run_due_checks(db, Publisher())
    assert db.all('SELECT * FROM delivery_checks')


def test_notification_only_routes_to_exact_group_and_post():
    job = {'group_name': 'Września - Ogłoszenia', 'group_url': 'https://www.facebook.com/groups/old-id'}
    notification = {'text': 'Публикация размещена в группе Września - Ogłoszenia.\n2 мин.',
                    'href': 'https://www.facebook.com/groups/123/?multi_permalinks=456&notif_t=group_crossposting_published'}
    candidate = FacebookPublisher._notification_candidate
    assert candidate(notification, job) == 'https://www.facebook.com/groups/123/posts/456/'
    assert not candidate({**notification, 'text': 'Публикация размещена в группе Другая группа.'}, job)
    assert not candidate({**notification, 'href': notification['href'].replace('www.facebook.com', 'evil.example')}, job)
    assert not candidate({**notification, 'href': notification['href'].replace('group_crossposting_published', 'feedback_reaction_generic')}, job)
    assert not candidate({**notification, 'href': notification['href'].replace('456', '456,789')}, job)


def test_old_approval_is_not_evidence_of_new_attempt():
    now = datetime.now(timezone.utc)
    job = {'group_name': 'Group', 'group_url': 'https://www.facebook.com/groups/123', 'attempted_at': now.isoformat()}
    href = 'https://www.facebook.com/groups/123/posts/456/?notif_t=group_post_approved&notif_id='
    row = {'text': 'Approved', 'href': href + str(int((now - timedelta(hours=1)).timestamp() * 1_000_000))}
    assert not FacebookPublisher._notification_candidate(row, job)
    row['href'] = href + str(int(now.timestamp() * 1_000_000))
    assert FacebookPublisher._notification_candidate(row, job)


def test_permalink_keeps_post_id_from_notification_query():
    assert FacebookPublisher._post_permalink('https://www.facebook.com/groups/123/?multi_permalinks=456&ref=notif') == 'https://www.facebook.com/groups/123/posts/456/'
    assert FacebookPublisher._post_permalink('/groups/123/posts/456/?ref=notif') == 'https://www.facebook.com/groups/123/posts/456/'
    assert not FacebookPublisher._post_permalink('https://www.facebook.com/groups/123/')


def test_interrupted_submission_is_checked_not_retried(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=1)
    job = db.one('SELECT * FROM queue')
    db.execute("UPDATE queue SET status='running' WHERE id=?", (job['id'],))
    mark_attempt(db, [job])
    recover_interrupted(db)
    assert db.one('SELECT status FROM queue')['status'] == 'unknown'
    assert db.one('SELECT queue_id FROM delivery_checks')['queue_id'] == job['id']


def test_packet_reads_notifications_once_without_opening_any_group(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=2)
    jobs = db.all('SELECT q.*,g.url group_url,g.name group_name FROM queue q JOIN groups_ g ON g.id=q.group_id ORDER BY q.id')
    mark_attempt(db, jobs)
    now_id = str(int(datetime.now(timezone.utc).timestamp() * 1_000_000))
    rows = [{'text': f"Публикация размещена в группе {job['group_name']}.",
             'href': f"{job['group_url']}/posts/{100 + index}/?notif_t=group_crossposting_published&notif_id={now_id}"}
            for index, job in enumerate(jobs)]

    class Publisher(FacebookPublisher):
        reads = 0

        def launch(self, **kwargs):
            return SimpleNamespace(stop=lambda: None), SimpleNamespace(pages=[object()], close=lambda: None)

        def _account_id(self, context):
            return 'account'

        def _read_delivery_notifications(self, page):
            self.reads += 1
            return rows

        def _probe_group_delivery(self, *args, **kwargs):
            raise AssertionError('Group/post navigation is forbidden')

    publisher = Publisher(SimpleNamespace(db_path=db.path))
    results = publisher.check_deliveries(jobs)
    assert publisher.reads == 1
    assert all(result[0] == 'published' for result in results.values())
    rows[0]['href'] = rows[0]['href'].replace('group_crossposting_published', 'group_post_pending')
    assert publisher.check_deliveries(jobs)[jobs[0]['id']][0] == 'moderation'
    rows.pop(0)
    results = publisher.check_deliveries(jobs)
    assert results[jobs[0]['id']][0] == 'sent'  # Accepted, not claimed publicly visible.
    assert results[jobs[1]['id']][0] == 'published'
    rows.clear()
    assert publisher.check_deliveries(jobs)[jobs[0]['id']][0] == 'unknown'


def test_crosspost_acceptance_only_updates_its_own_origin(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=4)
    jobs = db.all('SELECT * FROM queue ORDER BY id')
    mark_attempt(db, jobs[:2])
    mark_attempt(db, jobs[2:])
    for job in jobs:
        finish_delivery(db, job, 'unknown')
    finish_delivery(db, jobs[1], 'published', 'post-url', 'Notification confirmed')
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs[0]['id'],))['status'] == 'sent'
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs[2]['id'],))['status'] == 'unknown'
    finish_delivery(db, jobs[0], 'moderation')
    finish_delivery(db, jobs[1], 'published')
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs[0]['id'],))['status'] == 'moderation'


def test_old_packet_cannot_confirm_a_new_attempt_of_its_origin(tmp_path):
    db, _, _ = plan(tmp_path, bikes=1, groups=2)
    jobs = db.all('SELECT * FROM queue ORDER BY id')
    mark_attempt(db, jobs)
    finish_delivery(db, jobs[0], 'unknown')
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec='seconds')
    db.execute('UPDATE deliveries SET attempted_at=? WHERE queue_id=?', (later, jobs[0]['id']))
    finish_delivery(db, jobs[1], 'published')
    assert db.one('SELECT status FROM queue WHERE id=?', (jobs[0]['id'],))['status'] == 'unknown'
