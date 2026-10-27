"""Synthetic state and mocked subprocesses only; no real sends or account queries."""

import contextlib
import getpass
import io
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.account_selection import save_account_id
from imessage_agent.models import Contact
from imessage_agent.storage import Store
from test_foundation import ALICE


class SupervisedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="supervised-offline-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "outbound.db"
        self.config = self.root / "private" / "account.json"
        self.path_patch = patch("imessage_agent.account_selection.selection_path", return_value=self.config)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        save_account_id("fictional-selected-id")
        self.platform = patch("imessage_agent.applescript.sys.platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        store = Store(self.state)
        store.put_contact(Contact(ALICE, "Fictional", True))
        store.approve("prior-approval", ALICE, "prior UNKNOWN text", time.time() + 600)
        store.claim_send("prior-attempt", ALICE, "prior UNKNOWN text", "prior-approval", 1, controlled=True)
        store.put_contact(Contact(ALICE, "Fictional", False))
        store.close()
        self.prior = self.history()

    def history(self):
        with sqlite3.connect(self.state) as connection:
            return [connection.execute(sql).fetchone() for sql in (
                "SELECT * FROM send_attempts WHERE id='prior-attempt'",
                "SELECT * FROM approvals WHERE id='prior-approval'",
                "SELECT * FROM outbound_claims WHERE attempt_id='prior-attempt'",
            )]

    def invoke(self, *, body="new fictional test text", confirmation="SEND ONE NEW IMESSAGE", outcomes=None, state=None):
        if outcomes is None:
            outcomes = [subprocess.CompletedProcess([], 0, "READY:1:1:1", ""),
                        subprocess.CompletedProcess([], 0, "ACCEPTED", "")]
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=[ALICE.address, body]), patch(
            "builtins.input", return_value=confirmation
        ), patch("subprocess.run", side_effect=outcomes) as runner, contextlib.redirect_stdout(io.StringIO()) as output:
            status = main(["send-supervised-test", "--state", str(state or self.state)])
        report = json.loads(output.getvalue())
        for private in (ALICE.address, body, str(self.state), "fictional-selected-id", "prior-attempt"):
            if private:
                self.assertNotIn(private, output.getvalue())
        self.assertEqual(self.history(), self.prior)
        return status, report, runner

    def test_one_dispatch_uses_selected_account_after_preflight_and_fresh_confirmation(self):
        status, report, runner = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(report, dict(status="ACCEPTED", diagnostic="transport_accepted_delivery_unverified"))
        scripts = [call.kwargs["input"] for call in runner.call_args_list]
        self.assertEqual(len(scripts), 2)
        self.assertNotIn("ichtsend", scripts[0])
        self.assertEqual(scripts[1].count("«event ichtsend»"), 1)
        for script in scripts:
            self.assertIn('set savedAccountID to "fictional-selected-id"', script)
            self.assertIn("«property styp» of targetAccount", script)
            self.assertIn("get «class pres» id expectedParticipantID", script)
            self.assertNotIn("«property styp» of candidate", script)
            self.assertNotIn("****ssms", script)
            self.assertNotIn("****srcs", script)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM approvals WHERE consumed_by IS NOT NULL").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT outbound_allowed FROM contacts").fetchone()[0], 0)

    def test_missing_confirmation_leaves_persistent_state_unchanged(self):
        before = self.state.read_bytes()
        status, report, runner = self.invoke(confirmation="no")
        self.assertEqual((status, report), (1, dict(status="FAILED", diagnostic="operator_cancelled")))
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.state.read_bytes(), before)

    def test_old_unknown_text_is_refused_before_queries_or_new_authorization(self):
        before = self.state.read_bytes()
        status, report, runner = self.invoke(body="prior UNKNOWN text")
        self.assertEqual(report, dict(status="FAILED", diagnostic="new_message_required"))
        self.assertEqual(status, 1)
        runner.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)

    def test_timeout_is_unknown_and_never_retried(self):
        status, report, runner = self.invoke(outcomes=[
            subprocess.CompletedProcess([], 0, "READY:1:1:1", ""), subprocess.TimeoutExpired("private data", 15),
        ])
        self.assertEqual((status, report["status"]), (3, "UNKNOWN"))
        self.assertEqual(runner.call_count, 2)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM send_attempts WHERE status='unknown'").fetchone()[0], 2)
        # Re-running with fresh IDs still cannot repeat the same reserved text.
        self.assertEqual(self.invoke()[1]["diagnostic"], "new_message_required")

    def test_preflight_failure_has_sanitized_diagnostics_and_no_state_changes(self):
        before = self.state.read_bytes()
        status, report, runner = self.invoke(outcomes=[subprocess.CompletedProcess([], 0, "PRE:recipient_lookup:-10000", "private")])
        self.assertEqual(report, dict(status="FAILED", diagnostic="apple_event_handler_failed",
                                     stage="recipient_lookup", apple_error_number=-10000))
        self.assertEqual(status, 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.state.read_bytes(), before)

    def test_definite_transport_denial_remains_failed_without_retry(self):
        status, report, runner = self.invoke(outcomes=[subprocess.CompletedProcess([], 0, "READY:1:1:1", ""),
                                                     subprocess.CompletedProcess([], 0, "RECIPIENT_ID_MISMATCH", "")])
        self.assertEqual((status, report), (1, dict(status="FAILED", diagnostic="recipient_identity_mismatch")))
        self.assertEqual(runner.call_count, 2)

    def test_failure_after_dispatch_leaves_durable_unknown_and_no_retry(self):
        with patch.object(Store, "finish_send", side_effect=sqlite3.OperationalError("private text")):
            status, report, runner = self.invoke()
        self.assertEqual((status, report), (3, dict(status="UNKNOWN", diagnostic="attempt_result_unavailable")))
        self.assertEqual(runner.call_count, 2)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM send_attempts WHERE status='unknown'").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT outbound_allowed FROM contacts").fetchone()[0], 0)

    def test_interruption_during_submit_is_unknown_and_never_retried(self):
        status, report, runner = self.invoke(outcomes=[
            subprocess.CompletedProcess([], 0, "READY:1:1:1", ""), KeyboardInterrupt(),
        ])
        self.assertEqual((status, report), (3, dict(status="UNKNOWN", diagnostic="attempt_interrupted")))
        self.assertEqual(runner.call_count, 2)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM send_attempts WHERE status='unknown'").fetchone()[0], 2)

    def test_missing_new_or_unrelated_state_is_not_created_or_modified(self):
        missing = self.root / "missing.db"
        self.assertEqual(self.invoke(state=missing)[1]["diagnostic"], "existing_outbound_state_required")
        self.assertFalse(missing.exists())
        fresh = self.root / "fresh.db"
        store = Store(fresh)
        store.close()
        original = fresh.read_bytes()
        self.assertEqual(self.invoke(state=fresh)[1]["diagnostic"], "prior_unknown_history_missing")
        self.assertEqual(fresh.read_bytes(), original)
        unrelated = self.root / "unrelated.db"
        with sqlite3.connect(unrelated) as connection:
            connection.execute("CREATE TABLE unrelated(x)")
        unrelated.chmod(0o600)
        original = unrelated.read_bytes()
        self.assertEqual(self.invoke(state=unrelated)[1]["diagnostic"], "unrecognized_outbound_state")
        self.assertEqual(unrelated.read_bytes(), original)

    def test_unsafe_state_and_symlinks_are_refused(self):
        alias = self.root / "alias.db"
        alias.symlink_to(self.state)
        self.assertEqual(self.invoke(state=alias)[1]["diagnostic"], "unsafe_outbound_state")
        self.state.chmod(0o644)
        self.assertEqual(self.invoke()[1]["diagnostic"], "unsafe_outbound_state")
        self.state.chmod(0o600)

    def test_noninteractive_and_hidden_prompt_failure_never_dispatch(self):
        for patches in ((patch("sys.stdin.isatty", return_value=False), patch("getpass.getpass")),
                        (patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=getpass.GetPassWarning))):
            with patches[0], patches[1], patch("subprocess.run") as runner, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["send-supervised-test", "--state", str(self.state)]), 1)
                runner.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["status"], "FAILED")
        self.assertEqual(self.history(), self.prior)

    def test_invalid_new_body_is_refused_before_authorization(self):
        for body in ("", "x" * 4001, "bad\x00body"):
            _, report, runner = self.invoke(body=body)
            self.assertEqual(report["status"], "FAILED")
            runner.assert_not_called()

    def test_existing_rate_limits_are_not_bypassed(self):
        with sqlite3.connect(self.state) as connection:
            connection.execute("UPDATE send_attempts SET created_at=? WHERE id='prior-attempt'", (time.time(),))
        self.prior = self.history()
        status, report, runner = self.invoke()
        self.assertEqual((status, report), (1, dict(status="FAILED", diagnostic="rate_limited")))
        self.assertEqual(runner.call_count, 1)
