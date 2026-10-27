"""Synthetic scheduler checks: controllable UTC clock, mocked transport, no Ollama."""

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.adapters import MockMessagesAdapter
from imessage_agent.applescript import AppleScriptMessagesAdapter
from imessage_agent.models import Contact, Recipient, SendOutcome, SendStatus
from imessage_agent.outbound import Outbound
from imessage_agent.scheduling import Scheduler, SchedulingError, local_instant, make_spec
from imessage_agent.storage import Store, _SCHEMA, _OUTBOUND_SCHEMA, _DRAFT_SCHEMA
from zoneinfo import ZoneInfo


RECIPIENT = Recipient.parse('+12025550101')
NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc).timestamp()


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='scheduler-synthetic-')
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.put_contact(Contact(RECIPIENT, 'Synthetic', True))
        self.now = NOW
        self.scheduler = Scheduler(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def spec(self, **kwargs):
        options = dict(start_date='2026-10-27', start_time='13:00', timezone_name='UTC')
        options.update(kwargs)
        return make_spec(RECIPIENT, 'synthetic scheduled text', **options)

    def create(self, **kwargs):
        return self.scheduler.create(self.spec(**kwargs), authorized=True)

    def due(self, plan):
        row = self.scheduler.history(plan)[0]
        self.now = row['send_at']
        return row

    def test_exact_time_no_early_send_and_one_attempt_after_restart(self):
        plan = self.create()
        self.assertEqual(self.scheduler.tick(dry_run=False), [])
        occurrence = self.due(plan)
        self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'accepted')
        self.store.close()
        self.store = Store(self.path)
        self.scheduler = Scheduler(self.store, clock=lambda: self.now)
        self.assertEqual(self.scheduler.tick(dry_run=False), [])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 1)
        self.assertEqual(self.scheduler.history(plan)[0]['id'], occurrence['id'])

    def test_random_choice_persisted_once_and_inside_window(self):
        plan = self.create(end_time='14:00')
        first = self.scheduler.history(plan)[0]
        self.assertLessEqual(first['window_start'], first['send_at'])
        self.assertLessEqual(first['send_at'], first['window_end'])
        self.assertLessEqual(first['deadline'], first['window_end'])
        with patch.object(self.scheduler.rng, 'uniform', side_effect=AssertionError('must not resample')):
            self.scheduler.materialize(plan)
        self.assertEqual(first, self.scheduler.history(plan)[0])

    def test_daily_weekly_and_selected_weekdays_no_duplicates(self):
        for options, expected in ((dict(recurrence='daily'), 8), (dict(recurrence='weekly'), 2),
                                  (dict(recurrence='weekdays', weekdays=(0, 2, 4)), 3)):
            with self.subTest(options=options):
                plan = self.create(**options)
                self.scheduler.materialize(plan)
                rows = self.scheduler.history(plan)
                self.assertEqual(len(rows), expected)
                self.assertEqual(len({r['local_date'] for r in rows}), expected)

    def test_recurrence_end_is_inclusive(self):
        plan = self.create(recurrence='daily', until='2026-10-29')
        self.assertEqual(len(self.scheduler.history(plan)), 3)

    def test_recurring_random_time_chosen_once_per_occurrence(self):
        with patch.object(self.scheduler.rng, 'uniform', side_effect=lambda a, b: a + 30) as choose:
            plan = self.create(recurrence='daily', end_time='14:00')
            self.assertEqual(choose.call_count, 8)
            self.scheduler.materialize(plan)
            self.assertEqual(choose.call_count, 8)
        self.assertTrue(all(r['send_at'] == r['window_start'] + 30 for r in self.scheduler.history(plan)))

    def test_dst_gap_rejected_and_fold_requires_policy(self):
        zone = ZoneInfo('America/Chicago')
        with self.assertRaisesRegex(SchedulingError, 'nonexistent'):
            local_instant(date(2027, 3, 14), '02:30', zone, 'reject')
        with self.assertRaisesRegex(SchedulingError, 'ambiguous'):
            local_instant(date(2026, 11, 1), '01:30', zone, 'reject')
        early = local_instant(date(2026, 11, 1), '01:30', zone, 'earlier')
        late = local_instant(date(2026, 11, 1), '01:30', zone, 'later')
        self.assertEqual(late - early, 3600)

    def test_recurrence_records_dst_gap_as_missed(self):
        self.now = datetime(2027, 3, 12, tzinfo=timezone.utc).timestamp()
        plan = self.create(start_date='2027-03-13', start_time='02:30',
                           timezone_name='America/Chicago', recurrence='daily')
        gap = next(r for r in self.scheduler.history(plan) if r['local_date'] == '2027-03-14')
        self.assertEqual((gap['status'], gap['detail']), ('missed', 'dst_local_time_unavailable'))

    def test_dst_fold_recurring_earlier_generates_only_one_local_day(self):
        plan = self.create(start_date='2026-10-31', start_time='01:30',
                           timezone_name='America/Chicago', recurrence='daily', fold='earlier')
        rows = [r for r in self.scheduler.history(plan) if r['local_date'] == '2026-11-01']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['send_at'], datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).timestamp())

    def test_authorization_required_allowlist_required_and_no_backdating(self):
        with self.assertRaises(SchedulingError):
            self.scheduler.create(self.spec())
        with self.assertRaises(SchedulingError):
            self.scheduler.create(self.spec(), authorized=1)
        self.store.put_contact(Contact(RECIPIENT, 'Synthetic', False))
        with self.assertRaisesRegex(SchedulingError, 'not_allowlisted'):
            self.create()
        with self.assertRaisesRegex(SchedulingError, 'future'):
            self.scheduler.create(self.spec(start_time='11:00'), authorized=True)

    def test_each_authorized_spec_field_is_bound(self):
        changes = dict(recipient='+12025550102', body='different synthetic text', start_time='14:00:00',
                       end_time='14:00:00', start_date='2026-10-28', timezone='America/Chicago',
                       grace=10, fold='later', recurrence='daily', until='2026-10-28',
                       transport='live', account_id='synthetic-account')
        for key, value in changes.items():
            with self.subTest(key=key):
                plan = self.create()
                spec = self.spec()
                spec[key] = value
                with self.store.connection:
                    self.store.connection.execute('UPDATE schedule_plans SET spec=? WHERE id=?', (json.dumps(spec), plan))
                with self.assertRaises((SchedulingError, ValueError)):
                    self.scheduler.details(plan)

    def test_edit_requires_authorization_and_cancels_only_pending_history(self):
        old = self.create()
        with self.assertRaises(SchedulingError):
            self.scheduler.create(self.spec(start_time='14:00'), replaces=old)
        self.assertEqual(self.scheduler.details(old)['status'], 'active')
        replacement = self.scheduler.create(self.spec(start_time='14:00'), authorized=True, replaces=old)
        self.assertEqual(self.scheduler.details(replacement)['replaces'], old)
        self.assertEqual(self.scheduler.history(old)[0]['status'], 'cancelled')
        self.assertTrue(self.scheduler.cancel(replacement))
        self.assertFalse(self.scheduler.cancel(replacement))
        self.now += 10000
        self.assertEqual(self.scheduler.tick(dry_run=False), [])

    def test_revoked_contact_fails_without_transport(self):
        plan = self.create()
        self.due(plan)
        self.store.put_contact(Contact(RECIPIENT, 'Synthetic', False))
        with patch.object(MockMessagesAdapter, 'dispatch', side_effect=AssertionError('no dispatch')):
            self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'failed')

    def test_grace_expired_marks_missed_without_attempt(self):
        plan = self.create(grace=60)
        row = self.due(plan)
        self.now = row['deadline'] + 0.001
        self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'missed')
        self.assertIsNone(self.store.attempt(row['id']))

    def test_exact_deadline_allowed_and_window_end_never_extended(self):
        plan = self.create(grace=60)
        row = self.due(plan)
        self.now = row['deadline']
        self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'accepted')
        self.now = NOW
        plan = self.create(end_time='13:01', grace=3600)
        self.assertEqual(self.scheduler.history(plan)[0]['deadline'], NOW + 3660)

    def test_crash_between_reservation_and_outbound_is_unknown_without_retry(self):
        plan = self.create()
        row = self.due(plan)
        with patch.object(Outbound, 'submit', side_effect=SystemExit('synthetic crash')):
            with self.assertRaises(SystemExit):
                self.scheduler.tick(dry_run=False)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'unknown')
        self.assertIsNone(self.store.attempt(row['id']))
        self.assertEqual(self.scheduler.tick(dry_run=False), [])

    def test_crash_during_dispatch_preserves_unknown_attempt(self):
        plan = self.create()
        row = self.due(plan)
        with patch.object(MockMessagesAdapter, 'dispatch', side_effect=SystemExit('synthetic crash')):
            with self.assertRaises(SystemExit):
                self.scheduler.tick(dry_run=False)
        self.assertEqual(self.store.attempt(row['id']).outcome.status, SendStatus.UNKNOWN)
        self.assertEqual(self.scheduler.tick(dry_run=False), [])

    def test_unknown_and_every_other_transport_outcome_never_retried(self):
        for status in SendStatus:
            with self.subTest(status=status):
                self.now = NOW
                plan = self.create()
                row = self.due(plan)
                with patch.object(MockMessagesAdapter, 'dispatch', return_value=SendOutcome(status, 'synthetic')) as dispatch:
                    self.scheduler.tick(dry_run=False)
                    self.scheduler.tick(dry_run=False)
                    self.assertEqual(dispatch.call_count, 1)
                self.assertEqual(self.store.attempt(row['id']).outcome.status, status)
                self.assertEqual(self.scheduler.history(plan)[0]['status'], status.value)

    def test_concurrent_workers_claim_one_transport_attempt(self):
        plan = self.create()
        self.due(plan)
        barrier = threading.Barrier(2)
        def work():
            store = Store(self.path)
            try:
                barrier.wait()
                return Scheduler(store, clock=lambda: self.now).tick(dry_run=False)
            finally:
                store.close()
        with patch.object(MockMessagesAdapter, 'dispatch', return_value=SendOutcome(SendStatus.ACCEPTED)) as dispatch:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: work(), range(2)))
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(sum(len(r) for r in results), 1)

    def test_dry_run_does_not_change_state_or_call_transport(self):
        plan = self.create()
        self.due(plan)
        before = '\n'.join(self.store.connection.iterdump())
        with patch.object(MockMessagesAdapter, 'dispatch', side_effect=AssertionError('dry run')):
            self.assertEqual(len(self.scheduler.tick()), 1)
        self.assertEqual(before, '\n'.join(self.store.connection.iterdump()))

    def test_live_recurring_resolved_same_plan_can_repeat_without_resetting_claims(self):
        plan = self.create(recurrence='daily', transport='live', account_id='synthetic-account')
        rows = self.scheduler.history(plan)
        with patch.object(AppleScriptMessagesAdapter, 'dispatch', return_value=SendOutcome(SendStatus.ACCEPTED)) as dispatch:
            for row in rows[:2]:
                self.now = row['send_at']
                self.assertEqual(self.scheduler.tick(dry_run=False, live=True)[0]['status'], 'accepted')
            self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM outbound_claims').fetchone()[0], 2)

    def test_prior_unknown_blocks_next_identical_live_occurrence(self):
        plan = self.create(recurrence='daily', transport='live', account_id='synthetic-account')
        rows = self.scheduler.history(plan)
        with patch.object(AppleScriptMessagesAdapter, 'dispatch', return_value=SendOutcome(SendStatus.UNKNOWN)) as dispatch:
            self.now = rows[0]['send_at']
            self.scheduler.tick(dry_run=False, live=True)
            self.now = rows[1]['send_at']
            self.assertEqual(self.scheduler.tick(dry_run=False, live=True)[0]['status'], 'failed')
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(self.store.attempt(rows[1]['id']).outcome.detail, 'payload_already_reserved')

    def test_unrelated_prior_suppression_is_not_bypassed(self):
        self.store.approve('old-approval', RECIPIENT, 'synthetic scheduled text', NOW + 100000)
        self.store.claim_send('prior', RECIPIENT, 'synthetic scheduled text', 'old-approval', NOW, controlled=True)
        plan = self.create(transport='live', account_id='synthetic-account')
        self.due(plan)
        with patch.object(AppleScriptMessagesAdapter, 'dispatch', side_effect=AssertionError('suppressed')):
            self.assertEqual(self.scheduler.tick(dry_run=False, live=True)[0]['status'], 'failed')
        self.assertEqual(self.store.attempt('prior').outcome.status, SendStatus.UNKNOWN)

    def test_live_adapter_refuses_changed_account_without_script(self):
        adapter = AppleScriptMessagesAdapter(enabled=True, account_id='authorized-synthetic-account')
        with patch('imessage_agent.applescript.load_account_id', return_value='other-synthetic-account'), patch(
            'imessage_agent.applescript._execute', side_effect=AssertionError('no AppleScript')
        ):
            self.assertEqual(adapter.dispatch(RECIPIENT, 'synthetic').detail, 'schedule_account_selection_changed')

    def test_mock_worker_does_not_execute_live_schedule(self):
        plan = self.create(transport='live', account_id='synthetic-account')
        self.due(plan)
        with patch.object(AppleScriptMessagesAdapter, 'dispatch', side_effect=AssertionError('no live send')):
            self.assertEqual(self.scheduler.tick(dry_run=False), [])
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'pending')

    def test_cli_create_confirmation_and_read_only_dry_run(self):
        output = io.StringIO()
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout', output), patch.object(
            output, 'isatty', return_value=True
        ), patch('getpass.getpass', side_effect=[RECIPIENT.address, 'synthetic cli text']), patch(
            'builtins.input', return_value='AUTHORIZE MOCK SCHEDULE'
        ), patch('imessage_agent.scheduling.time.time', return_value=NOW):
            # Scheduler's default clock is bound at definition; use dates safely in the future.
            result = main(['schedule-create', '--state', str(self.path), '--date', '2099-10-27',
                           '--time', '13:00', '--timezone', 'UTC'])
        self.assertEqual(result, 0, output.getvalue())
        before = self.path.read_bytes()
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(main(['scheduler-run', '--state', str(self.path), '--dry-run']), 0)
        self.assertEqual(before, self.path.read_bytes())

    def test_cli_noninteractive_creation_rejected_before_state_creation(self):
        state = Path(self.directory.name) / 'absent.sqlite3'
        with patch('sys.stdin.isatty', return_value=False), patch('sys.stdout', io.StringIO()):
            self.assertEqual(main(['schedule-create', '--state', str(state), '--date', '2099-10-27',
                                   '--time', '13:00', '--timezone', 'UTC']), 1)
        self.assertFalse(state.exists())

    def test_v3_migration_preserves_attempts_approvals_and_suppression(self):
        oldpath = Path(self.directory.name) / 'old.sqlite3'
        with sqlite3.connect(oldpath) as db:
            db.executescript(_SCHEMA + _OUTBOUND_SCHEMA + _DRAFT_SCHEMA)
        oldpath.chmod(0o600)
        # Seed a legacy reservation before opening Store, so this really tests the upgrade.
        from imessage_agent.models import payload_hash
        with sqlite3.connect(oldpath) as db:
            db.execute('INSERT INTO approvals VALUES (?,?,?,NULL)',
                       ('prior', payload_hash(RECIPIENT, 'prior synthetic text'), NOW + 100))
            db.execute('INSERT INTO send_attempts VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('prior', RECIPIENT.key, RECIPIENT.kind, RECIPIENT.address,
                        payload_hash(RECIPIENT, 'prior synthetic text'), 'prior', 'unknown', '', None, NOW))
            db.execute("UPDATE approvals SET consumed_by='prior' WHERE id='prior'")
            db.execute('INSERT INTO outbound_claims VALUES (?,?,NULL,NULL)', ('prior', 'synthetic-hash'))
        old = Store(oldpath)
        try:
            self.assertEqual(old.connection.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(old.attempt('prior').outcome.status, SendStatus.UNKNOWN)
            self.assertEqual(old.approval('prior').consumed_by, 'prior')
            self.assertEqual(old.connection.execute('SELECT COUNT(*) FROM outbound_claims').fetchone()[0], 1)
        finally:
            old.close()

    def test_invalid_windows_and_parameters(self):
        for options in (dict(end_time='12:00'), dict(start_time='13:00+01:00'), dict(grace=-1),
                        dict(grace=1.0), dict(recurrence='monthly'), dict(weekdays=(7,)),
                        dict(transport='live'), dict(recurrence='weekdays'),
                        dict(until='2026-10-26'), dict(timezone_name='Invalid/Zone')):
            with self.subTest(options=options), self.assertRaises((ValueError, KeyError)):
                self.spec(**options)

    def test_replacement_failure_rolls_back_cancellation_and_recipient_opt_in(self):
        plan = self.create()
        self.store.put_contact(Contact(RECIPIENT, 'Synthetic', False))
        with patch.object(self.scheduler.rng, 'uniform', side_effect=RuntimeError('synthetic disk/selection failure')):
            with self.assertRaises(RuntimeError):
                self.scheduler.create(self.spec(end_time='14:00'), authorized=True,
                                      replaces=plan, authorize_recipient=True)
        self.assertEqual(self.scheduler.details(plan)['status'], 'active')
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'pending')
        self.assertFalse(self.store.contact(RECIPIENT).outbound_allowed)
        self.assertEqual(len(self.scheduler.plans()), 1)

    def test_cancellation_after_reservation_prevents_unclaimed_dispatch(self):
        plan = self.create()
        self.due(plan)
        original = Outbound.submit
        def cancel_then_submit(outbound, *args, **kwargs):
            self.scheduler.cancel(plan)
            return original(outbound, *args, **kwargs)
        with patch.object(Outbound, 'submit', cancel_then_submit), patch.object(
            MockMessagesAdapter, 'dispatch', side_effect=AssertionError('cancelled')
        ):
            self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'failed')

    def test_deadline_expires_between_reservation_and_outbound_claim(self):
        plan = self.create(grace=1)
        row = self.due(plan)
        original = Outbound.submit
        def advance_then_submit(outbound, *args, **kwargs):
            self.now = row['deadline'] + 1
            return original(outbound, *args, **kwargs)
        with patch.object(Outbound, 'submit', advance_then_submit), patch.object(
            MockMessagesAdapter, 'dispatch', side_effect=AssertionError('expired')
        ):
            self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'missed')
        self.assertIsNone(self.store.attempt(row['id']))

    def test_modified_occurrence_window_fails_closed(self):
        plan = self.create()
        row = self.due(plan)
        with self.store.connection:
            self.store.connection.execute('UPDATE schedule_occurrences SET window_start=window_start-60 WHERE id=?',
                                          (row['id'],))
        with patch.object(MockMessagesAdapter, 'dispatch', side_effect=AssertionError('invalid window')):
            self.assertEqual(self.scheduler.tick(dry_run=False)[0]['status'], 'failed')

    def test_outbound_cannot_use_unbound_account_for_schedule(self):
        plan = self.create(transport='live', account_id='synthetic-account')
        row = self.due(plan)
        with self.store.connection:
            self.store.connection.execute("UPDATE schedule_occurrences SET status='unknown',claimed_at=? WHERE id=?",
                                          (self.now, row['id']))
        with patch.object(AppleScriptMessagesAdapter, 'dispatch', side_effect=AssertionError('unbound')):
            with self.assertRaisesRegex(SchedulingError, 'transport_mismatch'):
                Outbound(self.store, AppleScriptMessagesAdapter(enabled=True), clock=lambda: self.now).submit(
                    row['id'], RECIPIENT, self.spec()['body'], schedule_occurrence=row['id'])

    def test_live_adapter_revalidates_bound_recipient_and_account_in_same_script(self):
        from types import SimpleNamespace
        adapter = AppleScriptMessagesAdapter(enabled=True, account_id='synthetic-account')
        with patch('imessage_agent.applescript.load_account_id', return_value='synthetic-account'), patch(
            'imessage_agent.applescript._execute', return_value=SimpleNamespace(returncode=0, stdout='ACCEPTED')
        ) as execute:
            self.assertEqual(adapter.dispatch(RECIPIENT, 'synthetic').status, SendStatus.ACCEPTED)
        script = execute.call_args.args[0]
        for fragment in ('synthetic-account', RECIPIENT.address, 'RECIPIENT_ID_MISMATCH',
                         'RECIPIENT_MISMATCH', 'ACCOUNT_ID_MISMATCH'):
            self.assertIn(fragment, script)

    def test_concurrent_schema_upgrades_are_serialized(self):
        legacy = Path(self.directory.name) / 'concurrent.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.executescript(_SCHEMA + _OUTBOUND_SCHEMA + _DRAFT_SCHEMA)
        legacy.chmod(0o600)
        barrier = threading.Barrier(2)
        def upgrade(_):
            barrier.wait()
            store = Store(legacy)
            try:
                return store.connection.execute('PRAGMA user_version').fetchone()[0]
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(upgrade, range(2))), [4, 4])

    def test_cli_list_show_history_edit_cancel_and_mock_execution(self):
        plan = self.create()
        self.due(plan)
        for command in ('schedule-list', 'schedule-show', 'schedule-history'):
            argv = [command, '--state', str(self.path)]
            if command != 'schedule-list':
                argv += ['--id', plan]
            output = io.StringIO()
            with patch('sys.stdout', output):
                self.assertEqual(main(argv), 0)
            self.assertNotIn(RECIPIENT.address, output.getvalue())
            self.assertNotIn('synthetic scheduled text', output.getvalue())
        with patch('sys.stdout', io.StringIO()), patch('imessage_agent.schedule_cli.Scheduler', return_value=self.scheduler):
            self.assertEqual(main(['scheduler-run', '--state', str(self.path), '--execute-mock']), 0)
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')
        output = io.StringIO()
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout', output), patch.object(
            output, 'isatty', return_value=True
        ), patch('getpass.getpass', side_effect=[RECIPIENT.address, 'edited synthetic text']), patch(
            'builtins.input', return_value='AUTHORIZE MOCK SCHEDULE'
        ):
            self.assertEqual(main(['schedule-edit', '--state', str(self.path), '--id', plan,
                                   '--date', '2099-10-27', '--time', '14:00', '--timezone', 'UTC']), 0)
        replacement = json.loads(output.getvalue().splitlines()[-1])['schedule_id']
        self.assertEqual(self.scheduler.history(plan)[0]['status'], 'accepted')
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(main(['schedule-cancel', '--state', str(self.path), '--id', replacement]), 0)
        self.assertEqual(self.scheduler.details(replacement)['status'], 'cancelled')

    def test_cli_declined_authorization_does_not_create_state(self):
        state = Path(self.directory.name) / 'declined.sqlite3'
        output = io.StringIO()
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout', output), patch.object(
            output, 'isatty', return_value=True
        ), patch('getpass.getpass', side_effect=[RECIPIENT.address, 'synthetic']), patch('builtins.input', return_value='no'):
            self.assertEqual(main(['schedule-create', '--state', str(state), '--date', '2099-10-27',
                                   '--time', '13:00', '--timezone', 'UTC']), 1)
        self.assertFalse(state.exists())
