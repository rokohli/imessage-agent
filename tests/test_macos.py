"""Synthetic databases only. Never discovers or opens a personal database."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.ingestion import ingest_once
from imessage_agent.macos import MacOSMessagesReader, MessagesAccessError
from imessage_agent.models import Direction
from imessage_agent.outbound import Outbound
from imessage_agent.storage import Store


def fixture(path, *, text=True, attributed=True, chat_column="guid", flags=True):
    connection = sqlite3.connect(path)
    columns = ["guid TEXT", "date INTEGER", "is_from_me INTEGER", "handle_id INTEGER"]
    if text:
        columns.append("text TEXT")
    if attributed:
        columns.append("attributedBody BLOB")
    if flags:
        columns.extend(["item_type INTEGER", "is_system_message INTEGER", "associated_message_type INTEGER"])
    connection.execute("CREATE TABLE message (" + ",".join(columns) + ")")
    connection.execute("CREATE TABLE handle (id TEXT)")
    connection.execute(f"CREATE TABLE chat ({chat_column} TEXT)")
    connection.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
    connection.execute("INSERT INTO handle VALUES ('+12025550101')")
    connection.execute("INSERT INTO chat VALUES ('synthetic-chat')")
    connection.commit()
    return connection


def add(connection, sequence, **values):
    record = dict(ROWID=sequence, guid=f"synthetic-{sequence}", date=700_000_000,
                  is_from_me=0, handle_id=1)
    record.update(values)
    columns = ",".join(record)
    connection.execute(f"INSERT INTO message ({columns}) VALUES ({','.join('?' for _ in record)})", tuple(record.values()))
    connection.execute("INSERT INTO chat_message_join VALUES (1,?)", (sequence,))
    connection.commit()


class MacOSReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="messages-synthetic-")
        self.path = Path(self.directory.name) / "synthetic ?#.db"
        self.db = fixture(self.path)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def read(self, sequence=0, limit=100):
        with MacOSMessagesReader(self.path) as reader:
            return reader.read_after(sequence, limit).messages

    def test_structured_identity_direction_and_seconds(self):
        add(self.db, 1, text="Synthetic text")
        row, = self.read()
        self.assertEqual((row.guid, row.sequence, row.chat_id), ("synthetic-1", 1, "synthetic-chat"))
        self.assertEqual(row.sender.key, "phone:+12025550101")
        self.assertEqual(row.direction, Direction.INBOUND)
        self.assertEqual(row.body, "Synthetic text")
        self.assertEqual(row.occurred_at, 1678307200)

    def test_schema_variants(self):
        for variant in (dict(text=False), dict(attributed=False, flags=False, chat_column="chat_identifier")):
            with self.subTest(variant=variant):
                path = Path(self.directory.name) / f"variant-{len(variant)}.db"
                db = fixture(path, **variant)
                try:
                    values = {"attributedBody": b"NSString\x01\x00\x84\x01+\x02hi"} if variant.get("text") is False else {"text": "hi"}
                    add(db, 1, **values)
                    with MacOSMessagesReader(path) as reader:
                        self.assertEqual(reader.read_after(0, 10).messages[0].body, "hi")
                finally:
                    db.close()

    def test_nanoseconds_and_timestamp_order_do_not_control_cursor(self):
        add(self.db, 1, date=800_000_000_000_000_000)
        add(self.db, 3, date=600_000_000)
        first, second = self.read()
        self.assertGreater(first.occurred_at, second.occurred_at)
        self.assertEqual([first.sequence, second.sequence], [1, 3])
        self.assertEqual(self.read(1, 1), (second,))

    def test_attributed_malformed_missing_text_and_attachments(self):
        blobs = [b"NSString\x01\x00\x84\x01+\x02hi", b"NSString", b"\xff", None]
        for number, blob in enumerate(blobs, 1):
            add(self.db, number, attributedBody=blob)
        add(self.db, 5, text="\ufffc")
        add(self.db, 6, text="preferred", attributedBody=blobs[0])
        self.db.execute("CREATE TABLE attachment (filename TEXT)")
        self.db.execute("INSERT INTO attachment VALUES ('/never/open/this')")
        self.db.commit()
        self.assertEqual([row.body for row in self.read()], ["hi", None, None, None, None, "preferred"])

    def test_outgoing_and_system_records_never_become_inbound(self):
        add(self.db, 1, is_from_me=1, text="echo")
        add(self.db, 2, item_type=1)
        add(self.db, 3, associated_message_type=2000)
        add(self.db, 4, is_system_message=1)
        add(self.db, 5, is_from_me=None)
        rows = self.read()
        self.assertEqual([row.direction for row in rows], [Direction.OUTBOUND] + [Direction.SYSTEM] * 4)
        self.assertIsNone(rows[0].sender)

    def test_unknown_sender_and_ambiguous_chat_are_system_records(self):
        add(self.db, 1, handle_id=99)
        add(self.db, 2)
        self.db.execute("INSERT INTO chat VALUES ('other-chat')")
        self.db.execute("INSERT INTO chat_message_join VALUES (2,2)")
        self.db.commit()
        self.assertTrue(all(row.direction == Direction.SYSTEM for row in self.read()))
        self.assertEqual(self.read()[1].chat_id, "unresolved:synthetic-2")

    def test_unparseable_sender_and_unlinked_chat_are_preserved(self):
        add(self.db, 1)
        self.db.execute("UPDATE handle SET id='national-number'")
        self.db.execute("DELETE FROM chat_message_join")
        self.db.commit()
        row, = self.read()
        self.assertEqual(row.direction, Direction.SYSTEM)
        self.assertIsNone(row.sender)

    def test_duplicate_chat_joins_do_not_duplicate_or_skip_messages(self):
        add(self.db, 1)
        add(self.db, 2)
        self.db.execute("INSERT INTO chat_message_join VALUES (1,1)")
        self.db.commit()
        self.assertEqual([row.sequence for row in self.read(0, 2)], [1, 2])

    def test_readonly_query_only_and_authorizer_block_mutations(self):
        add(self.db, 1)
        original = self.path.read_bytes()
        names = set(self.path.parent.iterdir())
        with MacOSMessagesReader(self.path) as reader:
            self.assertEqual(reader._connection.execute("PRAGMA query_only").fetchone()[0], 1)
            for sql in ("DELETE FROM message", "CREATE TABLE bad(x)", "PRAGMA query_only=OFF",
                        "ATTACH DATABASE ':memory:' AS other", "PRAGMA user_version=9"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    reader._connection.execute(sql)
            reader.read_after(0, 10)
            # URI mode must protect the source even if both SQL guards are removed.
            reader._connection.set_authorizer(None)
            reader._connection.execute("PRAGMA query_only=OFF")
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                reader._connection.execute("DELETE FROM message")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(set(self.path.parent.iterdir()), names)

    def test_reads_committed_wal_without_checkpointing(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        add(self.db, 1)
        main_before = self.path.read_bytes()
        wal_before = Path(str(self.path) + "-wal").read_bytes()
        self.assertEqual(len(self.read()), 1)
        self.assertEqual(self.path.read_bytes(), main_before)
        self.assertEqual(Path(str(self.path) + "-wal").read_bytes(), wal_before)

    def test_ingestion_deduplication_and_restart(self):
        add(self.db, 1, guid="stable", text="same")
        add(self.db, 2, guid="stable", text="same")
        add(self.db, 4, is_from_me=1)
        state = Path(self.directory.name) / "state.db"
        store = Store(state)
        try:
            with MacOSMessagesReader(self.path) as reader:
                source = reader.source_id
                self.assertEqual(ingest_once(store, reader, 1), 1)
            store.close()
            store = Store(state)
            with MacOSMessagesReader(self.path) as reader:
                self.assertEqual(ingest_once(store, reader, 1), 0)
                self.assertEqual(ingest_once(store, reader, 1), 1)
                self.assertEqual(ingest_once(store, reader), 0)
                with self.assertRaises(ValueError):
                    Outbound(store, reader)
            self.assertEqual(store.cursor(source).sequence, 4)
            self.assertEqual(len(store.messages(source)), 2)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0], 0)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 0)
        finally:
            store.close()

    def test_invalid_record_does_not_advance_durable_cursor(self):
        add(self.db, 1)
        add(self.db, 2, guid=None)
        store = Store(Path(self.directory.name) / "state.db")
        try:
            with MacOSMessagesReader(self.path) as reader:
                with self.assertRaisesRegex(MessagesAccessError, "invalid_guid"):
                    ingest_once(store, reader)
                self.assertIsNone(store.cursor(reader.source_id))
                self.assertEqual(store.messages(reader.source_id), [])
        finally:
            store.close()

    def test_invalid_timestamp_is_content_free_failure(self):
        add(self.db, 1, date="private-invalid-value")
        with self.assertRaisesRegex(MessagesAccessError, "^invalid_timestamp$"):
            self.read()

    def test_replaced_file_and_sequence_rollback_fail_closed(self):
        add(self.db, 1)
        with MacOSMessagesReader(self.path) as reader:
            with self.assertRaisesRegex(MessagesAccessError, "sequence_rollback"):
                reader.read_after(2, 1)
            other = self.path.with_name("replacement.db")
            db = fixture(other)
            db.close()
            other.replace(self.path)
            with self.assertRaisesRegex(MessagesAccessError, "source_replaced"):
                reader.read_after(0, 1)

    def test_replacement_across_restart_requires_reconciliation(self):
        store = Store(Path(self.directory.name) / "state.db")
        try:
            add(self.db, 1)
            with MacOSMessagesReader(self.path) as reader:
                ingest_once(store, reader)
            other = self.path.with_name("replacement.db")
            db = fixture(other)
            db.close()
            other.replace(self.path)
            with MacOSMessagesReader(self.path) as reader:
                with self.assertRaisesRegex(ValueError, "generation changed"):
                    ingest_once(store, reader)
        finally:
            store.close()

    def test_incompatible_schema_and_views_are_rejected(self):
        self.db.execute("ALTER TABLE message RENAME COLUMN guid TO unknown")
        self.db.commit()
        with self.assertRaisesRegex(MessagesAccessError, "unsupported_schema"):
            MacOSMessagesReader(self.path)
        self.db.execute("ALTER TABLE message RENAME TO original")
        self.db.execute("CREATE VIEW message AS SELECT * FROM original")
        self.db.commit()
        with self.assertRaisesRegex(MessagesAccessError, "unsupported_schema"):
            MacOSMessagesReader(self.path)

    def test_missing_file_is_never_created(self):
        path = self.path.with_name("absent.db")
        with self.assertRaisesRegex(MessagesAccessError, "not_found"):
            MacOSMessagesReader(path)
        self.assertFalse(path.exists())

    def test_limits_and_closed_reader(self):
        with MacOSMessagesReader(self.path) as reader:
            for sequence, limit in ((-1, 1), (True, 1), (0, 0), (0, True), (0, 1001)):
                with self.subTest(sequence=sequence, limit=limit), self.assertRaises(ValueError):
                    reader.read_after(sequence, limit)
        with self.assertRaisesRegex(MessagesAccessError, "reader_closed"):
            reader.read_after(0, 1)

    def test_diagnostic_never_reads_message_rows_or_displays_contents(self):
        add(self.db, 1, text="private-body", guid="private-guid")
        calls = []
        original = sqlite3.connect

        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(calls.append)
            return connection

        output = io.StringIO()
        with patch("imessage_agent.macos.sqlite3.connect", side_effect=connect), contextlib.redirect_stdout(output):
            self.assertEqual(main(["diagnose", "--database", str(self.path)]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["schema"], "compatible")
        self.assertEqual(report["live_sending"], "disabled")
        for secret in ("private-body", "private-guid", "+12025550101", "synthetic-chat", str(self.path)):
            self.assertNotIn(secret, output.getvalue())
        self.assertFalse(any("FROM message" in sql and "LIMIT 0" not in sql for sql in calls))

    def test_diagnostic_errors_do_not_leak_sqlite_exception_details(self):
        output = io.StringIO()
        with patch("imessage_agent.macos.sqlite3.connect", side_effect=sqlite3.OperationalError("private detail")), contextlib.redirect_stdout(output):
            self.assertEqual(main(["diagnose", "--database", str(self.path)]), 1)
        self.assertNotIn("private detail", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["database"], "unavailable")

    def test_no_network_subprocess_or_contacts_access(self):
        add(self.db, 1)
        with patch("socket.socket", side_effect=AssertionError("no network")), patch(
            "subprocess.Popen", side_effect=AssertionError("no subprocess")
        ):
            self.assertEqual(len(self.read()), 1)


if __name__ == "__main__":
    unittest.main()
