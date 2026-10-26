"""Explicit account setup/validation with synthetic IDs and mocked AppleScript."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.account_selection import AccountSelectionError, load_account_id, save_account_id
from imessage_agent.applescript import (
    AppleScriptMessagesAdapter, _SETUP_ACCOUNTS, _script, diagnose_applescript, list_imessage_accounts,
)
from imessage_agent.models import Contact, SendStatus
from imessage_agent.outbound import Outbound
from imessage_agent.storage import Store
from test_foundation import ALICE


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="account-selection-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "account.json"
        self.path_patch = patch("imessage_agent.account_selection.selection_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.cli_path = patch("imessage_agent.__main__.selection_path", return_value=self.path)
        self.cli_path.start()
        self.addCleanup(self.cli_path.stop)
        self.platform = patch("imessage_agent.applescript.sys.platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def result(self, token, code=0):
        return subprocess.CompletedProcess([], code, token, "private stderr")

    def test_private_selection_round_trip_and_no_overwrite(self):
        save_account_id("fictional-selected-id")
        self.assertEqual(load_account_id(), "fictional-selected-id")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(AccountSelectionError, "account_selection_already_exists"):
            save_account_id("different-id")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(set(self.path.parent.iterdir()), {self.path})

    def test_missing_corrupt_permissive_and_symlink_selections_fail_closed(self):
        with self.assertRaisesRegex(AccountSelectionError, "account_selection_required"):
            load_account_id()
        save_account_id("fictional-id")
        for raw in ('{}', '{"account_id": "x", "extra": true}', '{"account_id": ""}', 'private broken json'):
            self.path.write_text(raw)
            with self.assertRaises(AccountSelectionError):
                load_account_id()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(AccountSelectionError, "unsafe_account_selection"):
            load_account_id()
        self.path.unlink()
        target = self.path.parent / "target.json"
        target.write_text('{"account_id": "fictional-id"}')
        target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaises(AccountSelectionError):
            load_account_id()

    def test_untrusted_parent_and_invalid_ids_are_not_saved(self):
        for value in (None, "", " leading", "trailing ", "bad\nline", "bad\x00id", "x" * 1025):
            with self.assertRaisesRegex(AccountSelectionError, "invalid_account_selection"):
                save_account_id(value)
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir(mode=0o755)
        self.path.parent.chmod(0o755)
        with self.assertRaisesRegex(AccountSelectionError, "unsafe_account_selection_directory"):
            save_account_id("fictional-id")
        self.assertFalse(self.path.exists())

    def test_catalog_only_includes_positive_imessage_ids_privately(self):
        with patch("subprocess.run", return_value=self.result("CATALOG\nACCOUNT:private-one\nACCOUNT:private:two\n")):
            self.assertEqual(list_imessage_accounts(), ["private-one", "private:two"])
        self.assertIn('«property styp» of candidateAccount) is «constant ****sims»', _SETUP_ACCOUNTS)
        self.assertNotIn("whose", _SETUP_ACCOUNTS)
        self.assertNotIn("ichtsend", _SETUP_ACCOUNTS)
        self.assertNotIn("«class pres»", _SETUP_ACCOUNTS)
        self.assertIn("on error number lookupError", _SETUP_ACCOUNTS)
        self.assertIn("lookupError is -1743", _SETUP_ACCOUNTS)

    def test_catalog_failures_unknown_accounts_and_duplicates_are_sanitized(self):
        cases = [("CATALOG", "no_verified_imessage_accounts"),
                 ("CATALOG\nACCOUNT:private-id\nACCOUNT:private-id", "ambiguous_account_catalog"),
                 ("CATALOG\nprivate raw reply", "invalid_account_catalog"),
                 ("SETUP_LIMIT", "account_setup_limit"),
                 ("PRE:service_type:-1743", "automation_not_authorized")]
        for token, error in cases:
            with self.subTest(error=error), patch("subprocess.run", return_value=self.result(token)), self.assertRaisesRegex(AccountSelectionError, error):
                list_imessage_accounts()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("private", 15)), self.assertRaisesRegex(
            AccountSelectionError, "account_setup_unavailable"
        ):
            list_imessage_accounts()

    def test_interactive_setup_selects_then_reverifies_id_without_disclosing_it(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="2"), patch(
            "subprocess.run", side_effect=[self.result("CATALOG\nACCOUNT:private-first\nACCOUNT:private-chosen"),
                                           self.result("READY:1:1:0")]
        ) as runner, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["setup-imessage-account"]), 0)
        self.assertEqual(load_account_id(), "private-chosen")
        self.assertIn("1. iMessage account 1", output.getvalue())
        self.assertIn("2. iMessage account 2", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn(str(self.path), output.getvalue())
        verification = runner.call_args_list[1].kwargs["input"]
        self.assertIn('set savedAccountID to "private-chosen"', verification)
        for call in runner.call_args_list:
            self.assertNotIn("ichtsend", call.kwargs["input"])

    def test_setup_rejects_existing_selection_without_queries_or_modifications(self):
        save_account_id("private-existing")
        original = self.path.read_bytes()
        with patch("sys.stdin.isatty", return_value=True), patch("subprocess.run") as runner, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["setup-imessage-account"]), 1)
            runner.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(json.loads(output.getvalue()), {"error": "account_selection_already_exists"})

    def test_invalid_choice_cancellation_and_noninteractive_setup_do_not_save(self):
        with patch("sys.stdin.isatty", return_value=False), patch("subprocess.run") as runner, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup-imessage-account"]), 1)
            runner.assert_not_called()
        for choice in ("0", "2", "private-id", ""):
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=choice), patch(
                "subprocess.run", return_value=self.result("CATALOG\nACCOUNT:private-id")
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["setup-imessage-account"]), 1)
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=EOFError), patch(
            "subprocess.run", return_value=self.result("CATALOG\nACCOUNT:private-id")
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup-imessage-account"]), 1)
        self.assertFalse(self.path.exists())

    def test_selected_account_must_be_ready_before_setup_saves_it(self):
        for token in ("ACCOUNT_DISABLED", "ACCOUNT_DISCONNECTED", "SELECTED_ACCOUNT_NOT_IMESSAGE", "SELECTED_ACCOUNT_MISSING"):
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="1"), patch(
                "subprocess.run", side_effect=[self.result("CATALOG\nACCOUNT:private-id"), self.result(token)]
            ), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["setup-imessage-account"]), 1)
                self.assertNotIn("private-id", output.getvalue())
                self.assertFalse(self.path.exists())

    def test_selected_lookup_never_classifies_unrelated_accounts_or_uses_index(self):
        script = _script(ALICE, account_id='fictional-"selected')
        self.assertIn('whose «property ID  » is savedAccountID', script)
        self.assertIn('«property styp» of targetAccount', script)
        self.assertIn('if (count of matches) is not 1', script)
        self.assertIn('if targetAccountID is not savedAccountID', script)
        self.assertNotIn("styp» of candidate", script)
        self.assertNotIn("repeat with", script)
        self.assertNotIn("****ssms", script)
        self.assertNotIn("****srcs", script)
        self.assertIn('fictional-\\"selected', script)
        self.assertNotIn("ichtsend", script)

    def test_selected_validation_and_exact_recipient_have_no_send_or_state_access(self):
        save_account_id("private-id")
        with patch("subprocess.run", return_value=self.result("READY:1:1:1")) as runner, patch(
            "getpass.getpass", return_value=ALICE.address
        ), patch("sys.stdin.isatty", return_value=True), patch(
            "imessage_agent.storage.Store.__init__", side_effect=AssertionError("no outbound state")
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--check-recipient"]), 0)
        script = runner.call_args.kwargs["input"]
        self.assertIn('set savedAccountID to "private-id"', script)
        self.assertIn('«property hndl» of targetParticipant) is not "' + ALICE.address + '"', script)
        self.assertIn("«property icsv» of targetParticipant", script)
        self.assertNotIn("ichtsend", script)
        self.assertNotIn("private-id", output.getvalue())
        self.assertNotIn(ALICE.address, output.getvalue())

    def test_direct_participant_reference_verifies_id_handle_and_owner(self):
        script = _script(ALICE, account_id="fictional-selected")
        self.assertIn('targetAccountID & ":" & "' + ALICE.address + '"', script)
        self.assertIn('get «class pres» id expectedParticipantID', script)
        self.assertNotIn('every «class pres»', script)
        self.assertNotIn('whose «property hndl»', script)
        for field in ('«property ID  » of targetParticipant', '«property hndl» of targetParticipant',
                      '«property icsv» of targetParticipant'):
            self.assertIn(field, script)
        self.assertLess(script.index('set lookupStage to "ownership"'), script.index('set recipientCount to 1'))
        self.assertNotIn('ichtsend', script)

    def test_direct_reference_identity_and_handle_mismatches_fail_without_fallback(self):
        save_account_id("private-id")
        for token, code in (("RECIPIENT_ID_MISMATCH", "recipient_identity_mismatch"),
                            ("RECIPIENT_MISMATCH", "recipient_lookup_failed"),
                            ("RECIPIENT_ACCOUNT_MISMATCH", "recipient_account_mismatch")):
            with self.subTest(token=token), patch("subprocess.run", return_value=self.result(token)) as runner:
                report = diagnose_applescript(ALICE)
                self.assertEqual(report["error"], code)
                self.assertNotIn("recipient_matches", report)
                self.assertEqual(runner.call_count, 1)

    def test_direct_reference_errors_preserve_stage_and_private_selection(self):
        save_account_id("private-id")
        original = self.path.read_bytes()
        with patch("subprocess.run", return_value=self.result("PRE:recipient_identity:-10000")) as runner:
            report = diagnose_applescript(ALICE)
        self.assertEqual(report, dict(error="apple_event_handler_failed", stage="recipient_identity",
                                      apple_error_number=-10000))
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertNotIn(ALICE.address, json.dumps(report))

    def test_missing_direct_participant_is_not_created_or_retried(self):
        save_account_id("private-id")
        with patch("subprocess.run", return_value=self.result("PRE:recipient_lookup:-1728")) as runner:
            report = diagnose_applescript(ALICE)
        self.assertEqual(report["error"], "apple_event_object_not_found")
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn("ichtsend", runner.call_args.kwargs["input"])
        self.assertNotIn("make new", runner.call_args.kwargs["input"])

    def test_missing_selection_does_not_query_or_fall_back(self):
        with patch("subprocess.run") as runner:
            self.assertEqual(diagnose_applescript(selected=True), {"error": "account_selection_required"})
            outcome = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "fictional")
            self.assertEqual(outcome.status, SendStatus.FAILED)
            self.assertEqual(outcome.detail, "account_selection_required")
            runner.assert_not_called()

    def test_selected_identity_protocol_and_readiness_denials_are_definite(self):
        save_account_id("private-id")
        for token, error in (("SELECTED_ACCOUNT_MISSING", "selected_account_missing"),
                             ("SELECTED_ACCOUNT_AMBIGUOUS", "selected_account_ambiguous"),
                             ("SELECTED_ACCOUNT_NOT_IMESSAGE", "selected_account_not_imessage"),
                             ("ACCOUNT_ID_MISMATCH", "selected_account_identity_mismatch"),
                             ("ACCOUNT_DISABLED", "imessage_account_disabled"),
                             ("ACCOUNT_DISCONNECTED", "imessage_account_disconnected"),
                             ("RECIPIENT_ACCOUNT_MISMATCH", "recipient_account_mismatch")):
            with patch("subprocess.run", return_value=self.result(token)):
                outcome = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "fictional")
                self.assertEqual((outcome.status, outcome.detail), (SendStatus.FAILED, error))

    def test_previous_unknown_survives_selection_changes_and_cannot_be_retried(self):
        store = Store(Path(self.directory.name) / "outbound.db")
        self.addCleanup(store.close)
        store.put_contact(Contact(ALICE, "Fictional", True))
        store.approve("old-approval", ALICE, "old text", 1000)
        gate = Outbound(store, AppleScriptMessagesAdapter(enabled=True), clock=lambda: 100)
        with patch("imessage_agent.applescript.load_account_id", return_value="old-selected-id"), patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("fictional", 15)
        ) as runner:
            self.assertEqual(gate.submit("old-attempt", ALICE, "old text", "old-approval").outcome.status, SendStatus.UNKNOWN)
        with patch("imessage_agent.applescript.load_account_id", return_value="new-selected-id"), patch("subprocess.run") as runner:
            self.assertEqual(gate.submit("old-attempt", ALICE, "old text", "old-approval").outcome.status, SendStatus.UNKNOWN)
            store.approve("new-approval", ALICE, "old text", 1000)
            self.assertEqual(gate.submit("new-attempt", ALICE, "old text", "new-approval").outcome.detail, "payload_already_reserved")
            runner.assert_not_called()
        self.assertEqual(store.attempt("old-attempt").outcome.status, SendStatus.UNKNOWN)
