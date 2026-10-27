"""Offline background-agent tests: synthetic state and transports only."""
import io
import json
import logging
import os
import plistlib
import signal
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from imessage_agent.launchagent import configuration, manage, LABEL
from imessage_agent.models import Recipient, Contact
from imessage_agent.scheduling import Scheduler, make_spec
from imessage_agent.storage import Store, _SCHEMA, _OUTBOUND_SCHEMA, _DRAFT_SCHEMA, _SCHEDULER_SCHEMA, _DASHBOARD_SCHEMA
from imessage_agent.worker import Worker, WorkerBusy, worker_lock, worker_status, private_logger, run_worker
from imessage_agent.notifications import notify_native, EVENTS

NOW = 1793098800.0  # synthetic UTC instant; no wall-clock dependency


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.now = NOW
        self.recipient = Recipient.parse('+12025550101')
        self.store.put_contact(Contact(self.recipient, 'Synthetic', True))
        self.output = io.StringIO()
        self.logger = logging.Logger('synthetic-worker')
        self.logger.addHandler(logging.StreamHandler(self.output))
        self.notify = Mock()
        self.worker = Worker(self.path, self.logger, clock=lambda: self.now, notify=self.notify, interval=1)
        self.scheduler = Scheduler(self.store, clock=lambda: self.now)

    def test_unavailable_reply_source_does_not_block_authorized_schedule_polling(self):
        from imessage_agent.macos import MessagesAccessError
        with patch('imessage_agent.reply_lifecycle.invalidate_stale_drafts', side_effect=MessagesAccessError('permission_denied')), patch(
                'imessage_agent.worker.Scheduler.tick') as tick:
            self.worker.poll(self.store)
        tick.assert_called_once()
        self.assertEqual(self.worker.health['error'], 'reply_source_unavailable')
        self.assertNotIn('permission_denied', self.output.getvalue())

    def test_resolved_notification_names_stay_out_of_worker_logs(self):
        with patch('imessage_agent.contacts.Contacts.resolve', return_value={
                'display_name': 'Private Synthetic Name', 'resolution': 'resolved'}):
            self.assertFalse(self.worker.present([('approval', self.recipient.address)]))
        self.notify.assert_called_once_with('approval', 'Private Synthetic Name')
        self.assertNotIn('Private Synthetic Name', self.output.getvalue())
        self.assertNotIn(self.recipient.address, self.output.getvalue())

    def tearDown(self):
        self.worker.stop.set()
        self.worker.presenter.shutdown(wait=True)
        self.store.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        from datetime import datetime, timezone
        future = datetime.fromtimestamp(NOW + 60, timezone.utc)
        spec = make_spec(self.recipient, 'SECRET SYNTHETIC TEXT', start_date=future.date().isoformat(),
                         start_time=future.time().isoformat(), timezone_name='UTC', **kwargs)
        return self.scheduler.create(spec, authorized=True)

    def enable(self, enabled=True):
        with self.store.connection:
            self.store.connection.execute('INSERT OR REPLACE INTO dashboard_preferences VALUES (?,?)',
                                          ('settings', json.dumps({'notifications': enabled})))

    def test_poll_executes_existing_scheduler_once_without_ollama(self):
        plan = self.create()
        self.now += 61
        with patch('imessage_agent.ollama.Ollama', side_effect=AssertionError('model forbidden')):
            self.worker.poll(self.store)
            Worker(self.path, self.logger, clock=lambda: self.now).poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 1)
        self.assertEqual(self.worker.health['last_poll'], self.now)
        self.notify.assert_not_called()

    def test_stop_between_occurrences_does_not_begin_another_send(self):
        from imessage_agent.adapters import MockMessagesAdapter
        one, two = self.create(), self.create()
        self.now += 61
        original = MockMessagesAdapter.dispatch
        def dispatch(adapter, *args, **kwargs):
            result = original(adapter, *args, **kwargs)
            self.worker.stop.set()
            return result
        with patch.object(MockMessagesAdapter, 'dispatch', dispatch):
            self.worker.poll(self.store)
        statuses = [r['status'] for r in self.scheduler.history()]
        self.assertCountEqual(statuses, ['accepted', 'pending'])

    def test_startup_database_unavailability_recovers(self):
        original = Store
        attempts = []
        def open_store(path):
            attempts.append(True)
            if len(attempts) == 1:
                raise sqlite3.OperationalError('PRIVATE startup lock')
            return original(path)
        original_poll = self.worker.poll
        def finish(store):
            original_poll(store)
            self.worker.stop.set()
        with patch('imessage_agent.worker.Store', side_effect=open_store), patch.object(
            self.worker, 'poll', side_effect=finish), patch.object(self.worker.stop, 'wait'):
            self.worker.run()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.worker.health['recent_errors'][0]['category'], 'sqlite_unavailable')

    def test_failure_when_outcome_persistence_is_locked_never_resends(self):
        from imessage_agent.storage import Store
        plan = self.create()
        self.now += 61
        with patch.object(Store, 'finish_send', side_effect=sqlite3.OperationalError('PRIVATE locked')):
            self.worker.poll(self.store)
        self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'unknown')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 1)

    def test_account_disconnected_and_permission_denied_use_existing_outcomes(self):
        from imessage_agent.models import SendOutcome, SendStatus
        plan = self.create(transport='live', account_id='synthetic-account')
        self.now += 61
        self.worker.live = True
        with patch('imessage_agent.applescript.AppleScriptMessagesAdapter.dispatch',
                   return_value=SendOutcome(SendStatus.FAILED, 'selected_account_unavailable')) as dispatch:
            self.worker.poll(self.store)
            self.worker.poll(self.store)
            dispatch.assert_called_once()
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'failed')
        health = worker_status(self.store, self.path, now=self.now)
        self.assertTrue(any(e['category'] == 'scheduled_failed' for e in health['recent_errors']))

    def test_real_mock_process_heartbeat_duplicate_start_and_sigterm(self):
        import subprocess
        import sys
        import time
        from imessage_agent.worker import lock_held
        command = [sys.executable, '-m', 'imessage_agent', 'background-worker', '--state', str(self.path),
                   '--logs', str(Path(self.temp.name) / 'process-logs'), '--interval', '1']
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if worker_status(self.store, self.path)['status'] == 'running':
                    break
                if process.poll() is not None:
                    self.fail('synthetic worker exited before heartbeat')
                time.sleep(0.05)
            else:
                self.fail('no fresh heartbeat')
            duplicate = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(duplicate.returncode, 0)
            self.assertIn(b'worker_already_running', duplicate.stdout)
            process.terminate()
            output, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)
            self.assertFalse(lock_held(self.path))
            self.assertEqual(worker_status(self.store, self.path)['status'], 'stopped')
            self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    def test_only_selected_transport_is_executed(self):
        plan = self.create(transport='live', account_id='synthetic-account')
        self.now += 61
        with patch('imessage_agent.applescript._execute', side_effect=AssertionError('live forbidden')):
            self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'pending')

    def test_duplicate_worker_lock_and_release(self):
        with worker_lock(self.path):
            with self.assertRaises(WorkerBusy):
                self.worker.run()
        with worker_lock(self.path):
            pass

    def test_start_shutdown_and_connection_cleanup(self):
        def finish(store):
            self.worker.stop.set()
        with patch.object(self.worker, 'poll', side_effect=finish):
            self.assertEqual(self.worker.run(), 0)
        events = [json.loads(line)['event'] for line in self.output.getvalue().splitlines()]
        self.assertEqual(events, ['started', 'stopped'])
        self.assertEqual(worker_status(self.store, self.path, now=self.now)['status'], 'stopped')

    def test_sigterm_sets_stop_and_restores_handlers(self):
        args = SimpleNamespace(state=self.path, logs=Path(self.temp.name) / 'logs', interval=1, execute_live=False, database=None)
        callbacks = {}
        def register(sig, handler):
            previous = callbacks.get(sig, signal.SIG_DFL)
            callbacks[sig] = handler
            return previous
        def run(worker):
            callbacks[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(worker.stop.is_set())
            return 0
        with patch('imessage_agent.worker.signal.signal', side_effect=register), patch.object(Worker, 'run', run):
            self.assertEqual(run_worker(args), 0)
        self.assertEqual(callbacks[signal.SIGTERM], signal.SIG_DFL)

    def test_sleep_wake_expires_and_does_not_duplicate_recurrence(self):
        plan = self.create(recurrence='daily')
        self.now += 3 * 86400 + 400
        self.worker.poll(self.store)
        first = self.scheduler.history(plan)
        self.assertTrue(all(row['status'] == 'missed' for row in first if row['send_at'] <= self.now))
        self.worker.poll(self.store)
        second = self.scheduler.history(plan)
        self.assertEqual([r['id'] for r in first], [r['id'] for r in second])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_wake_within_grace_executes_once(self):
        plan = self.create(grace=300)
        self.now += 120
        self.worker.poll(self.store)
        self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')

    def test_crash_after_reservation_never_retries_unknown(self):
        plan = self.create()
        occurrence = self.scheduler.history(plan)[0]
        self.now += 61
        with self.store.connection:
            self.store.connection.execute("UPDATE schedule_occurrences SET status='unknown',claimed_at=? WHERE id=?",
                                          (self.now, occurrence['id']))
        with patch('imessage_agent.adapters.MockMessagesAdapter.dispatch', side_effect=AssertionError('retry forbidden')):
            self.worker.poll(self.store)
            self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'unknown')

    def test_failure_after_attempt_commit_remains_unknown(self):
        plan = self.create()
        self.now += 61
        with patch('imessage_agent.storage.Store.finish_send', side_effect=RuntimeError('SECRET exception')):
            self.worker.poll(self.store)
        self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'unknown')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 1)
        self.assertNotIn('SECRET', self.output.getvalue())

    def test_slow_notification_batch_does_not_expire_next_occurrence(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timezone
        self.enable()
        for _ in range(5):
            self.create()
        future = datetime.fromtimestamp(NOW + 120, timezone.utc)
        spec = make_spec(self.recipient, 'next synthetic occurrence',
                         start_date=future.date().isoformat(), start_time=future.time().isoformat(),
                         timezone_name='UTC', grace=60)
        next_plan = self.scheduler.create(spec, authorized=True)
        entered, release = threading.Event(), threading.Event()
        presented = []
        def slow_notify(event):
            presented.append(event)
            entered.set()
            release.wait(5)
        self.worker.notify = slow_notify
        self.now = NOW + 61
        # SQLite connection belongs to this caller; poll uses a fresh connection.
        def first_poll():
            store = Store(self.path)
            try:
                self.worker.poll(store)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=1) as caller:
            poll = caller.submit(first_poll)
            try:
                self.assertTrue(entered.wait(2))
                # Poll must finish while the native notification call is still blocked.
                poll.result(timeout=1)
                self.now = NOW + 121
                self.worker.poll(self.store)
                self.assertEqual(self.scheduler.history(next_plan)[0]['status'], 'accepted')
                self.assertEqual(self.store.connection.execute(
                    'SELECT COUNT(*) FROM worker_notifications').fetchone()[0], 5)
            finally:
                self.worker.stop.set()
                release.set()
        self.worker.presentation.result(timeout=2)
        self.assertEqual(presented, ['accepted'])

    def test_notification_events_are_durable_and_private(self):
        self.enable()
        plan = self.create()
        self.now += 61
        self.worker.poll(self.store)
        self.worker.presentation.result(timeout=2)
        self.worker.poll(self.store)
        self.notify.assert_called_once_with('accepted')
        self.assertNotIn(self.recipient.address, self.output.getvalue())
        self.assertNotIn('SECRET', self.output.getvalue())
        log = json.loads(self.output.getvalue().splitlines()[0])
        self.assertEqual(log['schedule_id'], plan)
        self.assertEqual(set(log), {'timestamp', 'event', 'schedule_id', 'occurrence_id', 'outcome', 'error'})
        restarted = Worker(self.path, self.logger, clock=lambda: self.now, notify=self.notify)
        restarted.poll(self.store)
        self.notify.assert_called_once()

    def test_failed_unknown_missed_approval_and_attention_events(self):
        from imessage_agent.drafting import save_draft
        self.enable()
        for outcome in ('failed', 'unknown', 'missed'):
            plan = self.create()
            with self.store.connection:
                self.store.connection.execute('UPDATE schedule_occurrences SET status=? WHERE plan_id=?', (outcome, plan))
        draft = save_draft(self.store, 'synthetic', 'generation', dict(
            source_guid='incoming', source_sequence=1, recipient_key=self.recipient.key,
            reply='PRIVATE DRAFT', reason='review', auto_sending=False, dashboard_status='pending'))
        self.worker.attention('sqlite_unavailable')
        self.worker.notifications(self.store)
        self.worker.presentation.result(timeout=2)
        self.assertEqual({c.args[0] for c in self.notify.call_args_list}, {'failed', 'unknown', 'missed', 'approval', 'attention'})
        self.assertNotIn('PRIVATE', self.output.getvalue())
        self.assertIn('unknown', EVENTS)

    def test_notifications_disabled_then_enabled_does_not_replay_old_events(self):
        self.create()
        self.now += 61
        self.worker.poll(self.store)
        self.enable()
        self.worker.poll(self.store)
        self.notify.assert_not_called()

    def test_notification_failure_never_retries_send(self):
        self.enable()
        plan = self.create()
        self.now += 61
        self.notify.side_effect = OSError('SECRET path')
        self.worker.poll(self.store)
        self.worker.presentation.result(timeout=2)
        self.worker.poll(self.store)
        self.assertEqual(self.worker.health['error'], 'notification_unavailable')
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')
        self.assertNotIn('SECRET', self.output.getvalue())

    def test_notification_failures_coalesce_until_presentation_recovers(self):
        from datetime import datetime, timezone
        self.enable()
        plan = self.create()
        self.now += 61
        self.notify.side_effect = OSError('helper unavailable')
        self.worker.poll(self.store)
        for _ in range(6):
            if self.worker.presentation:
                self.worker.presentation.result(timeout=2)
            self.now += 10
            self.worker.poll(self.store)
        self.assertEqual(self.worker.health['error'], 'notification_unavailable')
        # Only one attention entry recorded for notification_unavailable, not crowded out across polls.
        notification_errors = [e for e in self.worker.health['recent_errors']
                               if e['category'] == 'notification_unavailable']
        self.assertEqual(len(notification_errors), 1)
        # Durable notifications table does not grow indefinitely per poll.
        rows = self.store.connection.execute('SELECT COUNT(*) FROM worker_notifications').fetchone()[0]
        self.assertEqual(rows, 2)  # 1 for occurrence, 1 for attention event
        self.assertEqual(self.notify.call_count, 2)

        # Presentation recovers on next event; error is cleared.
        self.notify.side_effect = None
        future = datetime.fromtimestamp(self.now + 60, timezone.utc)
        spec = make_spec(self.recipient, 'recovered synthetic occurrence',
                         start_date=future.date().isoformat(), start_time=future.time().isoformat(),
                         timezone_name='UTC', grace=60)
        self.scheduler.create(spec, authorized=True)
        self.now += 61
        self.worker.poll(self.store)
        if self.worker.presentation:
            self.worker.presentation.result(timeout=2)
        self.now += 10
        self.worker.poll(self.store)
        self.assertIsNone(self.worker.health['error'])
        self.assertFalse(self.worker.notification_failure)
        with worker_lock(self.path):
            self.assertEqual(worker_status(self.store, self.path, now=self.now)['status'], 'running')

        # If notification failure occurs and then user disables notifications, error clears.
        self.notify.side_effect = OSError('helper unavailable again')
        future2 = datetime.fromtimestamp(self.now + 60, timezone.utc)
        spec2 = make_spec(self.recipient, 'second occurrence',
                          start_date=future2.date().isoformat(), start_time=future2.time().isoformat(),
                          timezone_name='UTC', grace=60)
        self.scheduler.create(spec2, authorized=True)
        self.now += 61
        self.worker.poll(self.store)
        if self.worker.presentation:
            self.worker.presentation.result(timeout=2)
        self.worker.poll(self.store)
        self.assertEqual(self.worker.health['error'], 'notification_unavailable')
        self.enable(False)
        self.worker.poll(self.store)
        self.assertIsNone(self.worker.health['error'])

    def test_health_needs_lock_and_fresh_poll_not_only_heartbeat(self):
        self.worker.poll(self.store)
        self.assertEqual(worker_status(self.store, self.path, now=self.now)['status'], 'stopped')
        with worker_lock(self.path):
            self.assertEqual(worker_status(self.store, self.path, now=self.now)['status'], 'running')
            self.assertEqual(worker_status(self.store, self.path, now=self.now + 91)['status'], 'attention')
            self.assertEqual(worker_status(self.store, self.path, now=self.now - 1)['status'], 'attention')
            self.worker.health.update(last_poll=self.now - 100, heartbeat=self.now)
            from imessage_agent.worker import save_health
            save_health(self.store.connection, self.worker.health)
            self.assertEqual(worker_status(self.store, self.path, now=self.now)['status'], 'attention')

    def test_sqlite_lock_contention_recovers_without_duplicate(self):
        plan = self.create()
        self.now += 61
        blocker = sqlite3.connect(self.path)
        blocker.execute('BEGIN IMMEDIATE')
        self.store.connection.execute('PRAGMA busy_timeout=1')
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.worker.poll(self.store)
        finally:
            blocker.rollback()
            blocker.close()
        self.worker.poll(self.store)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')

    def test_loop_recovers_from_sqlite_and_transient_failure(self):
        original = self.worker.poll
        calls = []
        def poll(store):
            calls.append(True)
            if len(calls) == 1:
                raise sqlite3.OperationalError('PRIVATE lock detail')
            if len(calls) == 2:
                raise OSError('PRIVATE account permission')
            original(store)
            self.worker.stop.set()
        with patch.object(self.worker, 'poll', side_effect=poll), patch.object(self.worker.stop, 'wait'):
            self.worker.run()
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.worker.health['recent_errors']), 2)
        self.assertNotIn('PRIVATE', self.output.getvalue())

    def test_log_rotation_and_permissions(self):
        logdir = Path(self.temp.name) / 'logs'
        logger = private_logger(logdir)
        handler = logger.handlers[0]
        try:
            handler.maxBytes = 200
            for _ in range(100):
                logger.info('synthetic bounded event ' * 5)
            self.assertLessEqual(len(list(logdir.iterdir())), 4)
            for file in logdir.iterdir():
                self.assertEqual(file.stat().st_mode & 0o077, 0)
        finally:
            handler.close()

    def test_symlink_lock_and_log_refused(self):
        from imessage_agent.worker import lock_path
        target = Path(self.temp.name) / 'private'
        target.write_text('untouched')
        lock_path(self.path).symlink_to(target)
        with self.assertRaises(OSError):
            with worker_lock(self.path):
                pass
        logdir = Path(self.temp.name) / 'logs'
        logdir.mkdir(mode=0o700)
        (logdir / 'worker.jsonl').symlink_to(target)
        with self.assertRaises(ValueError):
            private_logger(logdir)
        self.assertEqual(target.read_text(), 'untouched')

    def test_v5_upgrade_preserves_attempts_and_preferences(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.executescript(_SCHEMA + _OUTBOUND_SCHEMA + _DRAFT_SCHEMA + _SCHEDULER_SCHEMA + _DASHBOARD_SCHEMA)
            db.execute('INSERT INTO dashboard_preferences VALUES (?,?)', ('settings', '{"notifications":true}'))
        legacy.chmod(0o600)
        upgraded = Store(legacy)
        try:
            self.assertEqual(upgraded.connection.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(upgraded.connection.execute('SELECT value FROM dashboard_preferences').fetchone()[0], '{"notifications":true}')
        finally:
            upgraded.close()

    def test_dashboard_worker_endpoint_is_local_and_reports_stopped(self):
        from fastapi.testclient import TestClient
        from imessage_agent.dashboard import create_app
        with TestClient(create_app(self.path), base_url='http://127.0.0.1:8000') as client:
            data = client.get('/api/worker').json()
            self.assertEqual(data['status'], 'stopped')
            self.assertIsNone(data['heartbeat'])
            self.assertEqual(client.get('/api/worker', headers={'Host': 'evil.test'}).status_code, 403)

    def test_native_notification_uses_only_fixed_event_argument(self):
        with patch('imessage_agent.notifications.notification_app', return_value=Path(self.temp.name)), patch(
            'imessage_agent.notifications.subprocess.run') as run:
            notify_native('unknown')
            self.assertEqual(run.call_args.args[0][-1], 'unknown')
            self.assertIn('/usr/bin/open', run.call_args.args[0])
            with self.assertRaises(ValueError):
                notify_native('PRIVATE TEXT')

    def test_launchagent_plist_absolute_paths_and_no_extra_services(self):
        with patch('imessage_agent.launchagent.sys.executable', '/synthetic/.venv/bin/python'):
            config = plistlib.loads(plistlib.dumps(configuration(self.path, live=True)))
        self.assertEqual(config['ProgramArguments'][0], '/synthetic/.venv/bin/python')
        self.assertIn('--execute-live', config['ProgramArguments'])
        self.assertEqual(config['KeepAlive'], {'SuccessfulExit': False})
        self.assertTrue(config['RunAtLoad'])
        self.assertEqual(config['LimitLoadToSessionType'], 'Aqua')
        self.assertNotIn('ollama', json.dumps(config).lower())
        self.assertNotIn('dashboard', json.dumps(config).lower())

    @patch('imessage_agent.launchagent.sys.platform', 'darwin')
    def test_invalid_install_does_not_publish_files_or_build_helper(self):
        home = Path(self.temp.name)
        args = SimpleNamespace(action='install', state=self.path, execute_live=False, database=None, interval=0)
        with patch('pathlib.Path.home', return_value=home), patch('imessage_agent.launchagent.build_notifier') as build:
            with self.assertRaises(ValueError):
                manage(args)
            build.assert_not_called()
        self.assertFalse((home / 'Library/LaunchAgents' / (LABEL + '.plist')).exists())

    @patch('imessage_agent.launchagent.sys.platform', 'darwin')
    def test_install_stop_restart_status_uninstall_paths(self):
        home = Path(self.temp.name)
        path = home / 'Library/LaunchAgents' / (LABEL + '.plist')
        appdir = home / 'Library/Application Support/iMessage Agent'
        app = appdir / 'Notifier.app'
        args = SimpleNamespace(action='install', state=self.path, execute_live=False, database=None, interval=10)
        with patch('pathlib.Path.home', return_value=home), patch('imessage_agent.launchagent.build_notifier') as build, patch(
            'imessage_agent.launchagent.launchctl', return_value=SimpleNamespace(returncode=0)) as ctl:
            result = manage(args)
            self.assertFalse(result['started'])
            ctl.assert_not_called()
            self.assertTrue(path.exists())
            build.assert_called_once()
            with self.assertRaises(ValueError):
                manage(args)
            for action in ('status', 'start', 'stop', 'restart'):
                args.action = action
                manage(args)
            app.mkdir()
            args.action = 'uninstall'
            manage(args)
            self.assertFalse(path.exists())
            self.assertFalse(app.exists())
            self.assertTrue(self.path.exists())
            self.assertTrue((appdir / 'logs').exists())
