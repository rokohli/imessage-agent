"""Offline synthetic tests only: no Apple DBs, scripts, networking, or model APIs."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from imessage_agent.adapters import MockMessagesAdapter, create_adapter
from imessage_agent.attributed import extract_body_from_attributed
from imessage_agent.ingestion import ingest_once
from imessage_agent.models import (
    Batch, Contact, Direction, Message, Recipient, Schedule, SendOutcome, SendStatus,
)
from imessage_agent.outbound import Outbound
from imessage_agent.storage import Store


ALICE = Recipient.parse("+12025550101")
BOB = Recipient.parse("+12025550102")


def message(sequence: int, *, guid: str | None = None, occurred_at: float | None = None) -> Message:
    return Message(sequence, guid or f"synthetic-{sequence}", "synthetic-chat",
                   Direction.INBOUND, ALICE, "synthetic text", occurred_at if occurred_at is not None else sequence)


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="imessage-agent-test-")
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def reopen(self) -> None:
        self.store.close()
        self.store = Store(self.path)

    def allow_and_approve(self, recipient=ALICE, body="hello", approval_id="approval-1") -> None:
        self.store.put_contact(Contact(recipient, "Synthetic contact", True))
        self.store.approve(approval_id, recipient, body, 200)

    def test_mock_is_default_and_live_is_unavailable(self):
        self.assertIs(type(create_adapter()), MockMessagesAdapter)
        with self.assertRaises(ValueError):
            create_adapter("live")
        with self.assertRaises(ValueError):
            Outbound(self.store, object())

    def test_ingestion_does_not_use_network_or_subprocesses(self):
        with patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")), patch(
            "socket.socket", side_effect=AssertionError("network forbidden")
        ):
            self.assertEqual(ingest_once(self.store, MockMessagesAdapter((message(1),))), 1)

    def test_phone_identity_requires_explicit_international_syntax(self):
        self.assertEqual(ALICE.key, "phone:+12025550101")
        for value in ("Alice", "contact:1", "2025550101", "+1 202 555 0101", "chat123", " +12025550101", "+0123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Recipient.parse(value)

    def test_email_identity_normalizes_only_domain(self):
        self.assertEqual(Recipient.parse("Alice@EXAMPLE.COM").key, "email:Alice@example.com")
        self.assertNotEqual(Recipient.parse("Alice@example.com"), Recipient.parse("alice@example.com"))
        with self.assertRaises(ValueError):
            Recipient.parse("Alice <alice@example.com>")
        for invalid in ("a\x00@example.com", "a@-example.com", "a@example..com", "a..b@example.com"):
            with self.subTest(address=invalid), self.assertRaises(ValueError):
                Recipient.parse(invalid)
        self.assertEqual(Recipient.parse("Alice@bücher.example").address, "Alice@xn--bcher-kva.example")

    def test_deduplicates_guid_and_repeated_poll(self):
        first = message(1, guid="stable")
        reader = MockMessagesAdapter((first, replace(first, sequence=2)))
        self.assertEqual(ingest_once(self.store, reader), 1)
        self.assertEqual(ingest_once(self.store, reader), 0)
        self.assertEqual(len(self.store.messages("mock")), 1)
        self.assertEqual(self.store.cursor("mock").sequence, 2)

    def test_cursor_order_is_sequence_not_timestamp(self):
        reader = MockMessagesAdapter((message(3, occurred_at=50), message(1, occurred_at=300), message(2, occurred_at=1)))
        for expected in (1, 2, 3):
            self.assertEqual(ingest_once(self.store, reader, limit=1), 1)
            self.assertEqual(self.store.cursor("mock").sequence, expected)
        self.assertEqual([r["sequence"] for r in self.store.messages("mock")], [1, 2, 3])

    def test_keeps_empty_attachment_only_and_outgoing_records(self):
        empty = replace(message(1), body=None)
        outgoing = replace(message(2), direction=Direction.OUTBOUND, sender=None)
        self.assertEqual(ingest_once(self.store, MockMessagesAdapter((empty, outgoing))), 2)
        rows = self.store.messages("mock")
        self.assertIsNone(rows[0]["body"])
        self.assertEqual(rows[1]["direction"], "outbound")
        self.assertEqual(self.store.cursor("mock").sequence, 2)

    def test_restart_resumes_without_duplicate_or_skipped_records(self):
        reader = MockMessagesAdapter(tuple(message(n) for n in range(1, 5)))
        ingest_once(self.store, reader, limit=2)
        self.reopen()
        self.assertEqual(ingest_once(self.store, reader, limit=2), 2)
        self.assertEqual(len(self.store.messages("mock")), 4)
        self.assertEqual(self.store.cursor("mock").sequence, 4)

    def test_batch_failure_rolls_back_messages_and_cursor(self):
        ingest_once(self.store, MockMessagesAdapter((message(1),)))
        self.store.connection.execute(
            "CREATE TRIGGER fail_cursor BEFORE UPDATE ON cursors BEGIN SELECT RAISE(ABORT,'synthetic fault'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            ingest_once(self.store, MockMessagesAdapter((message(2),)))
        self.assertEqual(self.store.cursor("mock").sequence, 1)
        self.assertEqual(len(self.store.messages("mock")), 1)

    def test_conflicting_duplicate_rolls_back_whole_batch(self):
        ingest_once(self.store, MockMessagesAdapter((message(1),)))
        conflicting = replace(message(1), sequence=3, body="changed")
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            ingest_once(self.store, MockMessagesAdapter((message(2), conflicting)))
        self.assertEqual(self.store.cursor("mock").sequence, 1)
        self.assertEqual(len(self.store.messages("mock")), 1)

    def test_generation_change_requires_explicit_reconciliation(self):
        ingest_once(self.store, MockMessagesAdapter((message(1),)))
        with self.assertRaisesRegex(ValueError, "generation changed"):
            ingest_once(self.store, MockMessagesAdapter((message(1),), generation="replacement"))

    def test_rejects_out_of_order_batch_and_stale_cursor(self):
        with self.assertRaisesRegex(ValueError, "sequence-ordered"):
            self.store.ingest(Batch("mock", "synthetic-v1", (message(2), message(1))), None)
        self.assertIsNone(self.store.cursor("mock"))
        ingest_once(self.store, MockMessagesAdapter((message(1),)))
        with self.assertRaisesRegex(ValueError, "stale cursor"):
            self.store.ingest(Batch("mock", "synthetic-v1", (message(2),)), None)

    def test_source_ids_have_independent_cursors_and_guid_namespaces(self):
        for source in ("synthetic-a", "synthetic-b"):
            self.assertEqual(ingest_once(self.store, MockMessagesAdapter((message(1),), source_id=source)), 1)
            self.assertEqual(len(self.store.messages(source)), 1)

    def test_batch_limits_and_identity_are_validated(self):
        for limit in (0, -1, 1001, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ingest_once(self.store, MockMessagesAdapter(), limit)
        reader = MockMessagesAdapter()
        with patch.object(reader, "read_after", return_value=Batch("other", "synthetic-v1", ())):
            with self.assertRaisesRegex(ValueError, "changed stream identity"):
                ingest_once(self.store, reader)

    def test_default_deny_unlisted_and_contact_default(self):
        mock = MockMessagesAdapter()
        sender = Outbound(self.store, mock, clock=lambda: 100)
        self.assertEqual(sender.submit("one", ALICE, "hello").outcome.status, SendStatus.FAILED)
        self.store.put_contact(Contact(ALICE, "Synthetic"))
        self.store.approve("approval", ALICE, "hello", 200)
        self.assertEqual(sender.submit("two", ALICE, "hello", "approval").outcome.status, SendStatus.FAILED)
        self.assertEqual(mock.calls, [])

    def test_approval_is_required_even_for_allowlisted_recipient(self):
        self.store.put_contact(Contact(ALICE, "Synthetic", True))
        mock = MockMessagesAdapter()
        result = Outbound(self.store, mock).submit("one", ALICE, "hello")
        self.assertEqual(result.outcome.status, SendStatus.FAILED)
        self.assertEqual(mock.calls, [])

    def test_approval_is_bound_to_recipient_and_exact_body(self):
        self.allow_and_approve()
        self.store.put_contact(Contact(BOB, "Synthetic Bob", True))
        mock = MockMessagesAdapter()
        sender = Outbound(self.store, mock, clock=lambda: 100)
        for attempt, recipient, body in (("one", BOB, "hello"), ("two", ALICE, "hello ")):
            self.assertEqual(sender.submit(attempt, recipient, body, "approval-1").outcome.status, SendStatus.FAILED)
        self.assertEqual(mock.calls, [])
        self.assertIsNone(self.store.approval("approval-1").consumed_by)

    def test_expiry_and_revocation_are_checked_at_dispatch(self):
        self.allow_and_approve()
        mock = MockMessagesAdapter()
        self.assertEqual(Outbound(self.store, mock, clock=lambda: 200).submit("one", ALICE, "hello", "approval-1").outcome.status, SendStatus.FAILED)
        self.store.put_contact(Contact(ALICE, "Synthetic", False))
        self.assertEqual(Outbound(self.store, mock, clock=lambda: 100).submit("two", ALICE, "hello", "approval-1").outcome.status, SendStatus.FAILED)
        self.assertEqual(mock.calls, [])

    def test_all_structured_outcomes_persist_without_inference(self):
        for status in SendStatus:
            with self.subTest(status=status):
                approval = f"approval-{status.value}"
                self.allow_and_approve(approval_id=approval)
                outcome = SendOutcome(status, "synthetic detail", "synthetic-provider-id")
                mock = MockMessagesAdapter(outcome=outcome)
                attempt = Outbound(self.store, mock, clock=lambda: 100).submit(status.value, ALICE, "hello", approval)
                self.assertEqual(attempt.outcome, outcome)
                self.assertEqual(len(mock.calls), 1)
                self.assertEqual(self.store.attempt(status.value), attempt)

    def test_unknown_result_and_attempt_survive_restart_without_retry(self):
        self.allow_and_approve()
        mock = MockMessagesAdapter()
        first = Outbound(self.store, mock, clock=lambda: 100).submit("stable", ALICE, "hello", "approval-1")
        self.assertEqual(first.outcome.status, SendStatus.UNKNOWN)
        self.reopen()
        other_mock = MockMessagesAdapter()
        self.assertEqual(Outbound(self.store, other_mock).submit("stable", ALICE, "hello", "approval-1"), first)
        self.assertEqual(other_mock.calls, [])
        blocked = Outbound(self.store, other_mock, clock=lambda: 100).submit("new-id", ALICE, "hello", "approval-1")
        self.assertEqual(blocked.outcome.status, SendStatus.FAILED)
        self.assertEqual(other_mock.calls, [])

    def test_crash_after_claim_never_causes_automatic_retry(self):
        self.allow_and_approve()
        attempt, claimed = self.store.claim_send("crashed", ALICE, "hello", "approval-1", 100)
        self.assertTrue(claimed)
        self.assertEqual(attempt.outcome.status, SendStatus.UNKNOWN)
        self.reopen()
        mock = MockMessagesAdapter()
        result = Outbound(self.store, mock).submit("crashed", ALICE, "hello", "approval-1")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        self.assertEqual(mock.calls, [])

    def test_duplicate_attempt_id_cannot_change_payload(self):
        self.allow_and_approve()
        sender = Outbound(self.store, clock=lambda: 100)
        sender.submit("same-id", ALICE, "hello", "approval-1")
        with self.assertRaisesRegex(ValueError, "another payload"):
            sender.submit("same-id", ALICE, "changed", "approval-1")

    def test_crash_after_dispatch_before_result_commit_does_not_retry(self):
        self.allow_and_approve()
        mock = MockMessagesAdapter(outcome=SendOutcome(SendStatus.SENT))
        with patch.object(self.store, "finish_send", side_effect=RuntimeError("synthetic crash")):
            with self.assertRaises(RuntimeError):
                Outbound(self.store, mock, clock=lambda: 100).submit("one", ALICE, "hello", "approval-1")
        self.assertEqual(len(mock.calls), 1)
        self.reopen()
        result = Outbound(self.store, mock).submit("one", ALICE, "hello", "approval-1")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        self.assertEqual(len(mock.calls), 1)

    def test_unstructured_success_text_stays_unknown(self):
        self.allow_and_approve()
        mock = MockMessagesAdapter()
        with patch.object(mock, "dispatch", return_value="sent successfully"):
            result = Outbound(self.store, mock, clock=lambda: 100).submit("one", ALICE, "hello", "approval-1")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        with self.assertRaises(ValueError):
            SendOutcome("sent")

    def test_adapter_exception_is_unknown_and_does_not_leak_body(self):
        self.allow_and_approve()
        mock = MockMessagesAdapter()
        with patch.object(mock, "dispatch", side_effect=RuntimeError("private text")):
            result = Outbound(self.store, mock, clock=lambda: 100).submit("one", ALICE, "hello", "approval-1")
        self.assertEqual(result.outcome.status, SendStatus.UNKNOWN)
        self.assertNotIn("private text", result.outcome.detail)

    def test_approval_claim_is_atomic_across_connections(self):
        self.allow_and_approve()
        other = Store(self.path)
        try:
            _, claimed = self.store.claim_send("one", ALICE, "hello", "approval-1", 100)
            denied, second_claim = other.claim_send("two", ALICE, "hello", "approval-1", 100)
            self.assertTrue(claimed)
            self.assertFalse(second_claim)
            self.assertEqual(denied.outcome.status, SendStatus.FAILED)
        finally:
            other.close()

    def test_competing_writers_cannot_consume_one_approval_twice(self):
        self.allow_and_approve()
        ready = threading.Barrier(2)

        def claim(attempt_id):
            store = Store(self.path)
            try:
                ready.wait(timeout=5)
                return store.claim_send(attempt_id, ALICE, "hello", "approval-1", 100)[1]
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("first", "second")))
        self.assertEqual(sorted(results), [False, True])

    def test_schedule_model_persists_and_cancel_survives_restart(self):
        self.allow_and_approve()
        plan = Schedule("plan", ALICE, "hello", 110, 150, 125, "UTC", "approval-1")
        self.store.put_schedule(plan)
        self.reopen()
        self.assertEqual(self.store.schedule("plan"), plan)
        self.assertTrue(self.store.cancel_schedule("plan"))
        self.assertFalse(self.store.cancel_schedule("plan"))
        self.reopen()
        self.assertEqual(self.store.schedule("plan").status, "cancelled")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0], 0)

    def test_schedule_rejects_outside_window_or_mismatched_approval(self):
        self.allow_and_approve()
        plan = Schedule("plan", ALICE, "hello", 110, 150, 125, "UTC", "approval-1")
        for invalid in (replace(plan, send_at=151), replace(plan, body="changed"), replace(plan, window_end=201)):
            with self.subTest(plan=invalid), self.assertRaises(ValueError):
                self.store.put_schedule(invalid)

    def test_database_permissions_and_unrecognized_schema(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        other = Path(self.directory.name) / "unrelated.sqlite3"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.close()
        os.chmod(other, 0o600)
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            Store(other)

    def test_refuses_symlink_and_permissive_database(self):
        symlink = Path(self.directory.name) / "alias.sqlite3"
        symlink.symlink_to(self.path)
        with self.assertRaises(OSError):
            Store(symlink)
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(ValueError, "private regular"):
            Store(self.path)
        os.chmod(self.path, 0o600)


class AttributedBodyTests(unittest.TestCase):
    def test_short_unicode_and_all_length_encodings(self):
        raw = "Synthetic 🌱".encode()
        for width in (0, 2, 3, 4):
            length = bytes([len(raw)]) if width == 0 else bytes([0x7f + width]) + len(raw).to_bytes(width, "little")
            with self.subTest(width=width):
                self.assertEqual(extract_body_from_attributed(b"NSString\x01\x00\x84\x01+" + length + raw), raw.decode())

    def test_malformed_truncated_and_invalid_utf8_return_none(self):
        for raw in (None, b"", b"random", b"NSString", b"NSString\x01\x00\x84\x01+\x81\x01",
                    b"NSString\x01\x00\x84\x01+\x03x", b"NSString\x01\x00\x84\x01+\x01\xff"):
            with self.subTest(raw=raw):
                self.assertIsNone(extract_body_from_attributed(raw))


if __name__ == "__main__":
    unittest.main()
