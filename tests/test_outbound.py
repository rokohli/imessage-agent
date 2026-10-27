"""All AppleScript execution is mocked; no Messages or network access."""

import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from imessage_agent.adapters import MockMessagesAdapter
from imessage_agent.applescript import AppleScriptMessagesAdapter
from imessage_agent.models import Batch, Contact, Direction, Message, Recipient, SendStatus
from imessage_agent.outbound import Outbound
from imessage_agent.storage import Store, _SCHEMA
from test_foundation import ALICE, BOB


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.selection = patch("imessage_agent.applescript.load_account_id", return_value="fictional-account")
        self.selection.start()
        self.addCleanup(self.selection.stop)

    def test_disabled_by_default_even_with_environment_flags(self):
        with patch.dict(os.environ, {"ENABLE_LIVE": "true", "IMESSAGE_LIVE": "1"}), patch("subprocess.run") as run:
            self.assertEqual(AppleScriptMessagesAdapter().dispatch(ALICE, "hello").detail, "transport_disabled")
            run.assert_not_called()
        with self.assertRaises(ValueError):
            AppleScriptMessagesAdapter(enabled="true")

    def test_transport_acceptance_is_not_delivery_and_payload_is_not_in_argv(self):
        body = 'Unicode 🌱 "quoted" \\ newline\n do shell script "bad" {address} {body}'
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "ACCEPTED\n", "private detail")
        ) as run:
            outcome = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, body)
        self.assertEqual(outcome.status, SendStatus.ACCEPTED)
        self.assertIsNone(outcome.provider_id)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/osascript", "-"])
        self.assertNotIn(body, str(args))
        self.assertEqual(kwargs["timeout"], 15)
        self.assertNotIn("shell", kwargs)
        script = kwargs["input"]
        self.assertIn('«property styp» of targetAccount', script)
        self.assertIn('«constant ****sims»', script)
        self.assertIn('«class pres» id expectedParticipantID', script)
        self.assertIn('is not expectedParticipantID', script)
        self.assertIn('«event ichtsend»', script)
        self.assertNotIn('POSIX file', script)
        self.assertIn('\\"quoted\\"', script)
        self.assertIn('\\n', script)
        self.assertNotIn("private detail", outcome.detail)

    def test_template_tokens_in_addresses_are_literal(self):
        recipient = Recipient.parse("MESSAGE_BODY@example.com")
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "ACCEPTED", "")
        ) as run:
            AppleScriptMessagesAdapter(enabled=True).dispatch(recipient, "payload")
        self.assertIn('«property hndl» of targetParticipant) is not "MESSAGE_BODY@example.com"', run.call_args.kwargs["input"])

    def test_known_pre_send_denials_and_uncertain_results(self):
        cases = [(0, "DENIED_ACCOUNT", SendStatus.FAILED), (0, "DENIED_RECIPIENT", SendStatus.FAILED),
                 (0, "UNKNOWN", SendStatus.UNKNOWN), (0, "sent successfully", SendStatus.UNKNOWN),
                 (0, "", SendStatus.UNKNOWN), (1, "ACCEPTED", SendStatus.UNKNOWN),
                 (0, "ACCEPTED\nprivate detail", SendStatus.UNKNOWN)]
        for code, stdout, expected in cases:
            with self.subTest(code=code, stdout=stdout), patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], code, stdout, "private detail")
            ):
                result = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "hello")
                self.assertEqual(result.status, expected)
                self.assertNotIn("private detail", result.detail)

    def test_exceptions_timeouts_and_platform_fail_closed(self):
        for error in (subprocess.TimeoutExpired("private detail", 15), OSError("private detail"),
                      UnicodeError("private detail")):
            with patch("imessage_agent.applescript.sys.platform", "darwin"), patch("subprocess.run", side_effect=error):
                self.assertEqual(AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "hello").status, SendStatus.UNKNOWN)
        with patch("imessage_agent.applescript.sys.platform", "linux"), patch("subprocess.run") as run:
            self.assertEqual(AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "hello").detail, "macos_required")
            run.assert_not_called()

    def test_files_groups_invalid_body_and_control_chars_never_execute(self):
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch("subprocess.run") as run:
            adapter = AppleScriptMessagesAdapter(enabled=True)
            for recipient, body in (("group-id", "hello"), (ALICE, Path("attachment")),
                                    (ALICE, ""), (ALICE, "x" * 4001), (ALICE, "bad\x00text")):
                with self.subTest(body=type(body)), self.assertRaises(ValueError):
                    adapter.dispatch(recipient, body)
            run.assert_not_called()


class ControlledOutboundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="controlled-outbound-")
        self.path = Path(self.directory.name) / "state.db"
        self.store = Store(self.path)
        self.now = 100.0
        self.selection = patch("imessage_agent.applescript.load_account_id", return_value="fictional-account")
        self.selection.start()
        self.addCleanup(self.selection.stop)
        self.adapter = AppleScriptMessagesAdapter(enabled=True)
        self.gate = Outbound(self.store, self.adapter, clock=lambda: self.now)
        self.platform = patch("imessage_agent.applescript.sys.platform", "darwin")
        self.platform.start()
        self.runner = patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "ACCEPTED", ""))
        self.run = self.runner.start()

    def tearDown(self):
        self.runner.stop()
        self.platform.stop()
        self.store.close()
        self.directory.cleanup()

    def authorize(self, body="hello", recipient=ALICE, approval="approval"):
        self.store.put_contact(Contact(recipient, "Fictional", True))
        self.store.approve(approval, recipient, body, 10000)

    def reopen(self):
        self.store.close()
        self.store = Store(self.path)
        self.gate = Outbound(self.store, self.adapter, clock=lambda: self.now)

    def incoming(self, sequence=1, body="fictional inbound", direction=Direction.INBOUND, sender=ALICE):
        self.store.ingest(Batch("fictional", "v1", (
            Message(sequence, f"message-{sequence}", "fictional-chat", direction, sender, body, sequence),
        )), self.store.cursor("fictional"))

    def test_disabled_gate_does_not_consume_approval(self):
        self.authorize()
        with self.assertRaisesRegex(ValueError, "transport_disabled"):
            Outbound(self.store, AppleScriptMessagesAdapter()).submit("attempt", ALICE, "hello", "approval")
        self.assertIsNone(self.store.approval("approval").consumed_by)
        self.run.assert_not_called()

    def test_authorization_failures_never_execute(self):
        self.authorize()
        cases = [("missing", ALICE, "hello", None), ("wrong-body", ALICE, "changed", "approval"),
                 ("wrong-recipient", BOB, "hello", "approval")]
        for attempt, recipient, body, approval in cases:
            self.assertEqual(self.gate.submit(attempt, recipient, body, approval).outcome.status, SendStatus.FAILED)
        self.store.put_contact(Contact(ALICE, "Fictional", False))
        self.assertEqual(self.gate.submit("revoked", ALICE, "hello", "approval").outcome.status, SendStatus.FAILED)
        self.store.put_contact(Contact(ALICE, "Fictional", True))
        self.now = 10000
        self.assertEqual(self.gate.submit("expired", ALICE, "hello", "approval").outcome.status, SendStatus.FAILED)
        self.run.assert_not_called()

    def test_restart_and_new_attempt_cannot_duplicate_reserved_payload(self):
        self.authorize()
        self.assertEqual(self.gate.submit("attempt", ALICE, "hello", "approval").outcome.status, SendStatus.ACCEPTED)
        self.reopen()
        self.gate.submit("attempt", ALICE, "hello", "approval")
        self.authorize(approval="approval-2")
        self.now += 100
        denied = self.gate.submit("new-attempt", ALICE, "hello", "approval-2")
        self.assertEqual(denied.outcome.detail, "payload_already_reserved")
        self.assertEqual(self.run.call_count, 1)

    def test_timeout_remains_unknown_and_consumes_approval(self):
        self.authorize()
        self.run.side_effect = subprocess.TimeoutExpired("private payload", 15)
        result = self.gate.submit("attempt", ALICE, "hello", "approval")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        self.reopen()
        self.gate.submit("attempt", ALICE, "hello", "approval")
        self.assertEqual(self.gate.submit("another", ALICE, "hello", "approval").outcome.status, SendStatus.FAILED)
        self.assertEqual(self.run.call_count, 1)

    def test_crash_before_dispatch_leaves_unknown_reservation(self):
        self.authorize()
        with patch.object(self.adapter, "dispatch", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.gate.submit("attempt", ALICE, "hello", "approval")
        self.reopen()
        result = self.gate.submit("attempt", ALICE, "hello", "approval")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        self.run.assert_not_called()

    def test_crash_after_dispatch_never_repeats(self):
        self.authorize()
        with patch.object(self.store, "finish_send", side_effect=RuntimeError("crash")), self.assertRaises(RuntimeError):
            self.gate.submit("attempt", ALICE, "hello", "approval")
        self.reopen()
        self.assertEqual(self.gate.submit("attempt", ALICE, "hello", "approval").outcome.status, SendStatus.UNKNOWN)
        self.assertEqual(self.run.call_count, 1)

    def test_recipient_rate_limit_survives_restart_and_clock_rollback(self):
        self.authorize()
        self.gate.submit("first", ALICE, "hello", "approval")
        self.reopen()
        self.authorize("second", approval="second-approval")
        self.now = 99
        self.assertEqual(self.gate.submit("blocked", ALICE, "second", "second-approval").outcome.detail, "rate_limited")
        self.assertIsNone(self.store.approval("second-approval").consumed_by)
        self.now = 160
        self.assertEqual(self.gate.submit("allowed", ALICE, "second", "second-approval").outcome.status, SendStatus.ACCEPTED)
        self.assertEqual(self.run.call_count, 2)

    def test_global_limit_counts_unknown_and_transport_denials(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, "DENIED_ACCOUNT", "")
        recipients = [Recipient.parse(f"+1202555010{n}") for n in range(1, 5)]
        for n, recipient in enumerate(recipients):
            self.authorize("hello", recipient, f"approval-{n}")
            result = self.gate.submit(f"attempt-{n}", recipient, "hello", f"approval-{n}")
        self.assertEqual(result.outcome.detail, "rate_limited")
        self.assertEqual(self.run.call_count, 3)

    def test_replies_reject_outgoing_unknown_and_other_sender(self):
        self.authorize()
        self.incoming(direction=Direction.OUTBOUND, sender=None)
        self.incoming(2, direction=Direction.SYSTEM, sender=None)
        self.incoming(3, sender=BOB)
        for n in (1, 2, 3, 99):
            result = self.gate.submit(f"reply-{n}", ALICE, "hello", "approval", reply_to=("fictional", f"message-{n}"))
            self.assertEqual(result.outcome.detail, "reply_context_not_inbound")
        self.run.assert_not_called()

    def test_one_reply_reservation_per_source_message_and_agent_echo_loop(self):
        self.incoming()
        self.authorize()
        self.gate.submit("first", ALICE, "hello", "approval", reply_to=("fictional", "message-1"))
        self.reopen()
        self.authorize("different reply", approval="second")
        self.now += 100
        result = self.gate.submit("duplicate", ALICE, "different reply", "second", reply_to=("fictional", "message-1"))
        self.assertEqual(result.outcome.detail, "reply_already_reserved")
        self.incoming(2, body="hello")
        result = self.gate.submit("echo", ALICE, "different reply", "second", reply_to=("fictional", "message-2"))
        self.assertEqual(result.outcome.detail, "agent_message_loop")
        self.assertEqual(self.run.call_count, 1)

    def test_competing_process_connections_share_rate_reservations(self):
        self.authorize("first", approval="first")
        self.authorize("second", approval="second")
        ready = threading.Barrier(2)

        def reserve(name):
            store = Store(self.path)
            try:
                ready.wait(timeout=5)
                return store.claim_send(name, ALICE, name, name, 100, controlled=True)[1]
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(reserve, ("first", "second")))
        self.assertEqual(sorted(claimed), [False, True])
        self.run.assert_not_called()

    def test_reservation_failure_rolls_back_approval_and_attempt(self):
        self.authorize()
        self.store.connection.execute(
            "CREATE TEMP TRIGGER fail_claim BEFORE INSERT ON outbound_claims "
            "BEGIN SELECT RAISE(ABORT,'synthetic fault'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.gate.submit("attempt", ALICE, "hello", "approval")
        self.assertIsNone(self.store.attempt("attempt"))
        self.assertIsNone(self.store.approval("approval").consumed_by)
        self.run.assert_not_called()

    def test_noncanonical_recipient_is_refused_before_reservation(self):
        with self.assertRaisesRegex(ValueError, "canonical_recipient_required"):
            self.gate.submit("attempt", "group-id", "hello", "approval")
        self.assertIsNone(self.store.attempt("attempt"))
        self.run.assert_not_called()

    def test_null_body_cannot_be_reply_context(self):
        self.incoming(body=None)
        self.authorize()
        result = self.gate.submit("reply", ALICE, "hello", "approval", reply_to=("fictional", "message-1"))
        self.assertEqual(result.outcome.detail, "reply_context_not_inbound")
        self.run.assert_not_called()

    def test_legacy_state_migrates_without_losing_ingestion(self):
        path = Path(self.directory.name) / "legacy.db"
        connection = sqlite3.connect(path)
        connection.executescript(_SCHEMA)
        connection.execute("INSERT INTO cursors VALUES ('fictional','v1',70)")
        connection.commit()
        connection.close()
        path.chmod(0o600)
        store = Store(path)
        try:
            self.assertEqual(store.cursor("fictional").sequence, 70)
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 6)
        finally:
            store.close()
