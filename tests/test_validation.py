"""Only synthetic selected records; never discovers a personal database."""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.storage import _SCHEMA
from test_macos import add, fixture


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="fictional-direction-")
        self.root = Path(self.directory.name)
        self.database = self.root / "private-source.db"
        self.selection = self.root / "private-selection.json"
        self.db = fixture(self.database)
        add(self.db, 1, text="private body")
        add(self.db, 3, is_from_me=1)
        add(self.db, 7, handle_id=999)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def invoke(self, records):
        self.selection.write_text(json.dumps(records))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("subprocess.run", side_effect=AssertionError("no AppleScript")), patch(
            "socket.socket", side_effect=AssertionError("no network")
        ):
            status = main(["validate-classification", "--database", str(self.database),
                           "--selection", str(self.selection), "--fictional-only"])
        for secret in (str(self.database), str(self.selection), "private body", "synthetic-1",
                       "+12025550101", "synthetic-chat"):
            self.assertNotIn(secret, output.getvalue())
        return status, json.loads(output.getvalue())

    def test_selected_directions_match_without_modifying_source(self):
        original = self.database.read_bytes()
        status, report = self.invoke([dict(sequence=1, direction="inbound"), dict(sequence=3, direction="outbound"),
                                      dict(sequence=7, direction="system")])
        self.assertEqual((status, report), (0, dict(checked=3, matched=3, mismatched=0)))
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(set(self.root.iterdir()), {self.database, self.selection})

    def test_wrong_expectation_and_missing_selected_record_fail(self):
        self.assertEqual(self.invoke([dict(sequence=1, direction="outbound")]),
                         (1, dict(checked=1, matched=0, mismatched=1)))
        self.assertEqual(self.invoke([dict(sequence=2, direction="inbound")]),
                         (1, {"error": "selected_record_missing"}))

    def test_invalid_duplicate_and_excessive_selections_are_sanitized(self):
        invalid = [[], {}, [dict(sequence=True, direction="inbound")], [dict(sequence=1, direction="unknown")],
                   [dict(sequence=1, direction="inbound", body="private body")],
                   [dict(sequence=n, direction="inbound") for n in range(1, 27)],
                   [dict(sequence=1, direction="inbound")] * 2]
        for records in invalid:
            with self.subTest(records=type(records)):
                self.assertEqual(self.invoke(records), (1, {"error": "invalid_fictional_selection"}))

    def test_explicit_fictional_attestation_required(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as error:
            main(["validate-classification", "--database", str(self.database), "--selection", str(self.selection)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "invalid_arguments"})

    def test_source_access_and_bad_selection_are_sanitized(self):
        with patch("imessage_agent.macos._file_identity", side_effect=PermissionError("private body")):
            self.assertEqual(self.invoke([dict(sequence=1, direction="inbound")]),
                             (1, {"error": "permission_denied"}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["validate-classification", "--database", str(self.database),
                                   "--selection", str(self.root / "missing.json"), "--fictional-only"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"error": "invalid_fictional_selection"})


class LegacySmokeTests(unittest.TestCase):
    def test_v1_dry_run_and_replay_migrate_only_temporary_state(self):
        with tempfile.TemporaryDirectory(prefix="legacy-smoke-") as directory:
            root = Path(directory)
            database, state = root / "source.db", root / "state.db"
            db = fixture(database)
            add(db, 1)
            db.close()
            # Build genuine Phase 2A state using only the original schema.
            from imessage_agent.macos import MacOSMessagesReader
            from imessage_agent.smoke import run
            from imessage_agent.storage import Store
            with MacOSMessagesReader(database) as reader:
                batch = reader.read_after(0, 1)
            connection = sqlite3.connect(state)
            connection.executescript(_SCHEMA)
            message = batch.messages[0]
            connection.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                               (batch.source_id, message.guid, message.sequence, message.chat_id,
                                message.direction.value, message.sender.key, message.body, message.occurred_at))
            connection.execute("INSERT INTO cursors VALUES (?,?,?)", (batch.source_id, batch.generation, 1))
            connection.commit()
            connection.close()
            state.chmod(0o600)
            original = state.read_bytes()
            self.assertEqual(run(database, state)["inserted"], 0)
            self.assertEqual(state.read_bytes(), original)
            self.assertEqual(run(database, state, replay=True)["inserted"], 0)
            self.assertEqual(state.read_bytes(), original)
            store = Store(state)
            self.assertEqual(store.cursor(batch.source_id).sequence, 1)
            store.close()
