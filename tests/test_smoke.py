"""Smoke command tests use only temporary synthetic Messages databases."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.macos import MacOSMessagesReader
from imessage_agent.storage import Store
from test_macos import add, fixture


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="smoke-synthetic-")
        self.root = Path(self.directory.name)
        self.source = self.root / "private-source.db"
        self.state = self.root / "private-state.db"
        self.db = fixture(self.source)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def invoke(self, *options, state=None, database=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("socket.socket", side_effect=AssertionError("network forbidden")), patch(
            "subprocess.Popen", side_effect=AssertionError("subprocess forbidden")
        ):
            status = main(["ingest-smoke-test", "--database", str(database or self.source),
                           "--state", str(state or self.state), *options])
        for secret in (str(self.source), str(self.state), "private-body", "synthetic-1",
                       "synthetic-chat", "+12025550101"):
            self.assertNotIn(secret, output.getvalue())
        return status, json.loads(output.getvalue())

    def rows(self):
        with MacOSMessagesReader(self.source) as reader:
            source_id = reader.source_id
        store = Store(self.state)
        try:
            return [dict(row) for row in store.messages(source_id)], store.cursor(source_id).sequence
        finally:
            store.close()

    def test_default_dry_run_is_bounded_and_creates_no_state_or_parent(self):
        for n in range(1, 31):
            add(self.db, n, text="private-body")
        original = self.source.read_bytes()
        state = self.root / "absent" / "state.db"
        status, report = self.invoke(state=state)
        self.assertEqual(status, 0)
        self.assertEqual((report["inserted"], report["cursor_before"], report["cursor_after"]), (10, 0, 10))
        self.assertFalse(state.parent.exists())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_commit_identity_classification_nulls_and_restart(self):
        add(self.db, 1, text="private-body")
        add(self.db, 3, is_from_me=1, attributedBody=b"unsupported")
        add(self.db, 7, handle_id=999)
        original = self.source.read_bytes()
        status, report = self.invoke("--commit")
        self.assertEqual(status, 0)
        self.assertEqual(report, dict(inserted=3, inbound=1, outbound=1, system=1,
                                     null_bodies=2, cursor_before=0, cursor_after=7))
        rows, cursor = self.rows()
        self.assertEqual([row["guid"] for row in rows], ["synthetic-1", "synthetic-3", "synthetic-7"])
        self.assertEqual([row["direction"] for row in rows], ["inbound", "outbound", "system"])
        self.assertIsNone(rows[1]["body"])
        self.assertEqual(cursor, 7)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        status, report = self.invoke("--commit")
        self.assertEqual((status, report["inserted"], report["cursor_before"], report["cursor_after"]), (0, 0, 7, 7))
        self.assertEqual(len(self.rows()[0]), 3)
        self.assertEqual(self.source.read_bytes(), original)

    def test_existing_state_dry_run_preserves_bytes_cursor_and_metadata(self):
        add(self.db, 1)
        self.invoke("--commit")
        add(self.db, 4)
        before = self.state.read_bytes()
        mtime = self.state.stat().st_mtime_ns
        names = set(self.root.iterdir())
        status, report = self.invoke()
        self.assertEqual((status, report["cursor_before"], report["cursor_after"]), (0, 1, 4))
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)
        self.assertEqual(set(self.root.iterdir()), names)
        self.assertEqual(self.rows()[1], 1)
        self.assertEqual(self.invoke("--commit")[1]["inserted"], 1)
        self.assertEqual(self.rows()[1], 4)

    def test_limit_hard_maximum_and_rejections(self):
        for n in range(1, 31):
            add(self.db, n)
        self.assertEqual(self.invoke("--limit", "25")[1]["inserted"], 25)
        for value in ("0", "-1", "26", "1000"):
            self.assertEqual(self.invoke("--limit", value), (1, {"error": "invalid_limit"}))
        self.assertFalse(self.state.exists())

    def test_state_in_project_or_other_git_checkout_is_rejected(self):
        project = Path(__file__).resolve().parents[1]
        self.assertEqual(self.invoke(state=project / "new.db"), (1, {"error": "state_inside_repository"}))
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: elsewhere")
        self.assertEqual(self.invoke(state=checkout / "state.db"), (1, {"error": "state_inside_repository"}))
        alias = self.root / "alias"
        alias.symlink_to(project, target_is_directory=True)
        self.assertEqual(self.invoke(state=alias / "new.db"), (1, {"error": "state_inside_repository"}))

    def test_source_aliases_and_hardlinks_are_rejected_without_changes(self):
        original = self.source.read_bytes()
        self.assertEqual(self.invoke("--commit", state=self.source)[1]["error"], "state_is_source")
        alias = self.root / "alias.db"
        alias.symlink_to(self.source)
        self.assertEqual(self.invoke("--commit", state=alias)[1]["error"], "unsafe_state_path")
        alias.unlink()
        os.link(self.source, alias)
        self.assertEqual(self.invoke("--commit", state=alias)[1]["error"], "state_is_source")
        self.assertEqual(self.source.read_bytes(), original)

    def test_sqlite_companion_collisions_are_rejected(self):
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertEqual(self.invoke("--commit", state=Path(str(self.source) + suffix))[1]["error"],
                             "state_is_source")
            self.assertEqual(self.invoke("--commit", database=Path(str(self.state) + suffix))[1]["error"],
                             "state_is_source")
        self.assertFalse(self.state.exists())

    def test_wal_source_database_and_wal_are_unchanged(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        add(self.db, 1, text="private-body")
        wal = Path(str(self.source) + "-wal")
        original, original_wal = self.source.read_bytes(), wal.read_bytes()
        for options in ((), ("--commit",)):
            self.assertEqual(self.invoke(*options)[0], 0)
            self.assertEqual(self.source.read_bytes(), original)
            self.assertEqual(wal.read_bytes(), original_wal)

    def test_unrelated_empty_corrupt_and_spoofed_state_are_untouched(self):
        for content in (b"", b"private-body"):
            self.state.write_bytes(content)
            self.state.chmod(0o600)
            self.assertEqual(self.invoke("--commit")[0], 1)
            self.assertEqual(self.state.read_bytes(), content)
        self.state.unlink()
        conn = sqlite3.connect(self.state)
        conn.execute("CREATE TABLE unrelated(x)")
        conn.execute("PRAGMA user_version=1")
        conn.close()
        self.state.chmod(0o600)
        original = self.state.read_bytes()
        self.assertEqual(self.invoke("--commit"), (1, {"error": "unrecognized_state"}))
        self.assertEqual(self.state.read_bytes(), original)

    def test_unsafe_permissions_and_directory_are_refused(self):
        self.state.write_bytes(b"")
        self.state.chmod(0o644)
        self.assertEqual(self.invoke()[1]["error"], "unsafe_state_file")
        self.state.unlink()
        self.state.mkdir()
        self.assertEqual(self.invoke()[1]["error"], "unsafe_state_file")

    def test_database_access_failures_do_not_create_state(self):
        self.assertEqual(self.invoke("--commit", database=self.root / "missing.db"), (1, {"error": "not_found"}))
        with patch("imessage_agent.macos._file_identity", side_effect=PermissionError("private-body")):
            self.assertEqual(self.invoke("--commit"), (1, {"error": "permission_denied"}))
        self.assertFalse(self.state.exists())

    def test_invalid_record_rolls_back_batch_and_recovers(self):
        add(self.db, 1)
        self.invoke("--commit")
        add(self.db, 3)
        add(self.db, 5, guid=None)
        self.assertEqual(self.invoke("--commit"), (1, {"error": "invalid_guid"}))
        self.assertEqual(self.rows()[1], 1)
        self.assertEqual(len(self.rows()[0]), 1)
        self.db.execute("UPDATE message SET guid='repaired-synthetic' WHERE ROWID=5")
        self.db.commit()
        self.assertEqual(self.invoke("--commit")[1]["inserted"], 2)
        self.assertEqual(self.rows()[1], 5)

    def test_duplicate_guid_and_generation_change_fail_safely(self):
        add(self.db, 1)
        self.invoke("--commit")
        add(self.db, 2, guid="synthetic-1")
        self.assertEqual(self.invoke("--commit")[1]["inserted"], 0)
        self.assertEqual(self.rows()[1], 2)
        add(self.db, 3, guid="synthetic-1", text="conflict")
        self.assertEqual(self.invoke("--commit"), (1, {"error": "ingestion_validation_failed"}))
        self.assertEqual(self.rows()[1], 2)
        with patch("imessage_agent.macos._file_identity", return_value="replacement"):
            self.assertEqual(self.invoke("--commit"), (1, {"error": "ingestion_validation_failed"}))
        self.assertEqual(self.rows()[1], 2)

    def test_cursor_write_failure_rolls_back_and_restart_recovers(self):
        add(self.db, 1)
        self.invoke("--commit")
        add(self.db, 2)
        original = Store.ingest

        def fail(store, batch, previous):
            store.connection.execute("CREATE TEMP TRIGGER fail_cursor BEFORE UPDATE ON cursors "
                                     "BEGIN SELECT RAISE(ABORT,'private-body'); END")
            return original(store, batch, previous)

        with patch.object(Store, "ingest", fail):
            self.assertEqual(self.invoke("--commit"), (1, {"error": "state_unavailable"}))
        self.assertEqual(self.rows()[1], 1)
        self.assertEqual(self.invoke("--commit")[1]["inserted"], 1)

    def test_usage_errors_never_echo_arguments(self):
        for arguments in ([], ["--database", "private-body"],
                          ["--database", "private-body", "--state", "private-body", "--limit", "private-body"]):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                main(["ingest-smoke-test", *arguments])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_arguments"})

    def test_replay_deduplicates_saved_records_and_preserves_persistent_files(self):
        for n in (1, 3, 7):
            add(self.db, n, text="private-body" if n == 1 else None, is_from_me=int(n == 3))
        self.invoke("--commit")
        add(self.db, 9, text="not previously imported")
        original = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        status, report = self.invoke("--replay", "--limit", "25")
        self.assertEqual(status, 0)
        self.assertEqual(report, dict(checked=3, inserted=0, stored_before=3, stored_after=3,
                                     cursor_saved=7, replay_cursor_before=0, replay_cursor_after=7))
        self.assertEqual(set(self.root.iterdir()), set(original))
        for path, (contents, mtime) in original.items():
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(self.rows()[1], 7)
        self.assertEqual(self.invoke("--replay")[1], report)

    def test_replay_is_bounded_by_default_and_maximum(self):
        for n in range(1, 31):
            add(self.db, n)
        self.invoke("--commit", "--limit", "25")
        self.invoke("--commit")
        self.assertEqual(self.invoke("--replay")[1]["checked"], 10)
        report = self.invoke("--replay", "--limit", "25")[1]
        self.assertEqual((report["checked"], report["cursor_saved"], report["replay_cursor_after"]), (25, 30, 25))
        self.assertEqual(self.rows()[1], 30)
        self.assertEqual(self.invoke("--replay", "--limit", "26"), (1, {"error": "invalid_limit"}))

    def test_replay_requires_existing_state_and_saved_source(self):
        self.assertEqual(self.invoke("--replay"), (1, {"error": "replay_requires_state"}))
        self.assertFalse(self.state.exists())
        store = Store(self.state)
        store.close()
        self.assertEqual(self.invoke("--replay"), (1, {"error": "replay_requires_saved_cursor"}))
        self.invoke("--commit")
        self.assertEqual(self.invoke("--replay"), (1, {"error": "replay_requires_messages"}))

    def test_replay_conflicting_saved_payload_fails_without_state_changes(self):
        add(self.db, 1, text="private-body")
        self.invoke("--commit")
        original = self.state.read_bytes()
        for assignment in ("text='changed'", "is_from_me=1", "guid='changed'"):
            with self.subTest(assignment=assignment):
                self.db.execute("UPDATE message SET " + assignment + " WHERE ROWID=1")
                self.db.commit()
                self.assertEqual(self.invoke("--replay")[0], 1)
                self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.rows()[1], 1)

    def test_replay_rejects_generation_change_and_unsaved_records(self):
        add(self.db, 1)
        self.invoke("--commit")
        original = self.state.read_bytes()
        with patch("imessage_agent.macos._file_identity", return_value="replacement"):
            self.assertEqual(self.invoke("--replay"), (1, {"error": "ingestion_validation_failed"}))
        self.db.execute("DELETE FROM message WHERE ROWID=1")
        self.db.commit()
        add(self.db, 2)
        self.assertEqual(self.invoke("--replay"), (1, {"error": "replay_record_not_saved"}))
        self.assertEqual(self.state.read_bytes(), original)

    def test_replay_accepts_identical_duplicate_guids_without_new_records(self):
        add(self.db, 1)
        add(self.db, 2, guid="synthetic-1")
        add(self.db, 3)
        self.invoke("--commit")
        report = self.invoke("--replay")[1]
        self.assertEqual((report["checked"], report["inserted"], report["stored_after"]), (2, 0, 2))
        self.assertEqual(self.rows()[1], 3)

    def test_replay_and_commit_are_mutually_exclusive(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            self.invoke("--replay", "--commit")
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_arguments"})
        self.assertFalse(self.state.exists())

    def test_actual_new_process_resumes_then_deduplicates(self):
        for n in range(1, 4):
            add(self.db, n)
        command = [sys.executable, "-m", "imessage_agent", "ingest-smoke-test", "--database", str(self.source),
                   "--state", str(self.state), "--commit", "--limit", "2"]
        reports = [json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
                   for _ in range(3)]
        self.assertEqual([report["inserted"] for report in reports], [2, 1, 0])
        self.assertEqual([report["cursor_after"] for report in reports], [2, 3, 3])
        self.assertEqual(len(self.rows()[0]), 3)


if __name__ == "__main__":
    unittest.main()
