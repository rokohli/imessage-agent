"""Mocked diagnostics only. No live sends, account queries or state writes."""

import contextlib
import io
import getpass
import json
import subprocess
import unittest
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.applescript import AppleScriptMessagesAdapter, _script, diagnose_applescript
from imessage_agent.models import SendStatus
from test_foundation import ALICE


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.selection = patch("imessage_agent.applescript.load_account_id", return_value="fictional-account")
        self.selection.start()
        self.addCleanup(self.selection.stop)

    def run_result(self, token, *, code=0, stderr="private stderr", recipient=None):
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], code, token, stderr)
        ) as runner:
            report = diagnose_applescript(recipient)
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn(ALICE.address, json.dumps(report))
        return report, runner.call_args

    def test_account_diagnostic_has_no_send_event_or_recipient_lookup(self):
        report, call = self.run_result("READY:2:1:0")
        self.assertEqual(report, {"accounts": 2, "imessage_accounts": 1})
        script = call.kwargs["input"]
        self.assertNotIn("ichtsend", script)
        self.assertNotIn("send ", script)
        self.assertNotIn("«class pres»", script)
        self.assertNotIn(ALICE.address, script)
        self.assertEqual(call.args[0], ["/usr/bin/osascript", "-"])
        self.assertIn('tell application id messagesApplication', script)

    def test_authorized_recipient_diagnostic_checks_handle_and_ownership_without_send(self):
        report, call = self.run_result("READY:1:1:1", recipient=ALICE)
        self.assertEqual(report, {"accounts": 1, "imessage_accounts": 1, "recipient_matches": 1})
        script = call.kwargs["input"]
        self.assertIn("«property hndl»", script)
        self.assertIn("«property icsv» of targetParticipant", script)
        self.assertIn("«property ID  » of participantAccount", script)
        self.assertNotIn("ichtsend", script)
        self.assertNotIn(ALICE.address, str(call.args))

    def test_dictionary_codes_and_dispatch_boundary(self):
        script = _script(ALICE, body="fictional text")
        for code in ("«class icsv»", "«property styp»", "«constant ****sims»", "«property enbl»",
                     "«property ssta»", "«constant ****conn»", "«class pres»", "«property hndl»"):
            self.assertIn(code, script)
        self.assertLess(script.index("set dispatchStarted to true"), script.index("«event ichtsend»"))
        self.assertLess(script.index('set lookupStage to "ownership"'), script.index("set dispatchStarted to true"))
        self.assertIn('given «class TO  »:targetParticipant', script)

    def test_stage_specific_errors_are_sanitized(self):
        cases = [("PRE:account_lookup:-1728", "apple_event_object_not_found", "account_lookup"),
                 ("PRE:recipient_lookup:-1728", "apple_event_object_not_found", "recipient_lookup"),
                 ("PRE:connection_status:-1708", "apple_event_not_handled", "connection_status"),
                 ("PRE:handle:-1728", "apple_event_object_not_found", "handle"),
                 ("PRE:ownership:-1700", "apple_event_type_mismatch", "ownership"),
                 ("PRE:application:-1743", "automation_not_authorized", "application"),
                 ("PRE:application:-600", "application_unavailable", "application")]
        for token, error, stage in cases:
            with self.subTest(token=token):
                self.assertEqual(self.run_result(token)[0], {"error": error, "stage": stage, "apple_error_number": int(token.rsplit(":", 1)[1])})

    def test_process_errors_classify_only_numeric_codes_never_stderr_text(self):
        for number, error in ((-2741, "applescript_syntax_failed"), (-2753, "scripting_terminology_unavailable"),
                              (-1743, "automation_not_authorized"), (-10827, "application_unavailable")):
            report, _ = self.run_result("", code=1, stderr=f"private address and text ({number})")
            self.assertEqual(report, {"error": error, "stage": "unconfirmed", "apple_error_number": number})

    def test_account_and_recipient_absence_and_ambiguity(self):
        cases = [("DENIED_ACCOUNT:2:0", "imessage_account_unavailable"),
                 ("DENIED_ACCOUNT:3:2", "imessage_account_ambiguous"),
                 ("DENIED_RECIPIENT:0", "recipient_not_found"),
                 ("DENIED_RECIPIENT:2", "recipient_ambiguous"),
                 ("ACCOUNT_DISABLED", "imessage_account_disabled"),
                 ("ACCOUNT_DISCONNECTED", "imessage_account_disconnected"),
                 ("RECIPIENT_ACCOUNT_MISMATCH", "recipient_account_mismatch")]
        for token, error in cases:
            with self.subTest(token=token):
                self.assertEqual(self.run_result(token)[0]["error"], error)

    def test_timeouts_unrecognized_output_and_platform(self):
        for error in (subprocess.TimeoutExpired("private", 15), OSError("private"), UnicodeError("private")):
            with patch("imessage_agent.applescript.sys.platform", "darwin"), patch("subprocess.run", side_effect=error):
                self.assertEqual(diagnose_applescript(), {"error": "diagnostic_unavailable"})
        self.assertEqual(self.run_result("private output")[0]["error"], "transport_uncertain")
        self.assertEqual(self.run_result("PRE:private:-1728")[0]["error"], "transport_uncertain")
        with patch("imessage_agent.applescript.sys.platform", "linux"), patch("subprocess.run") as runner:
            self.assertEqual(diagnose_applescript(), {"error": "macos_required"})
            runner.assert_not_called()

    def test_cli_account_mode_never_opens_state_or_source(self):
        with patch("imessage_agent.__main__.diagnose_applescript", return_value={"accounts": 1, "imessage_accounts": 1}) as diagnostic, patch(
            "imessage_agent.storage.Store.__init__", side_effect=AssertionError("no state")
        ), patch("imessage_agent.macos.MacOSMessagesReader.__init__", side_effect=AssertionError("no source")), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript"]), 0)
        diagnostic.assert_called_once_with(None, selected=True)
        self.assertEqual(json.loads(output.getvalue()), {"accounts": 1, "imessage_accounts": 1})

    def test_cli_recipient_is_hidden_explicit_and_not_in_output(self):
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", return_value=ALICE.address) as prompt, patch(
            "imessage_agent.__main__.diagnose_applescript", return_value={"recipient_matches": 1}
        ) as diagnostic, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--check-recipient"]), 0)
        prompt.assert_called_once()
        diagnostic.assert_called_once_with(ALICE, selected=True)
        self.assertNotIn(ALICE.address, output.getvalue())

    def test_noninteractive_or_invalid_recipient_never_queries_messages(self):
        with patch("sys.stdin.isatty", return_value=False), patch("imessage_agent.__main__.diagnose_applescript") as diagnostic, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--check-recipient"]), 1)
            diagnostic.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"error": "interactive_terminal_required"})
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", return_value="private invalid address"), patch(
            "imessage_agent.__main__.diagnose_applescript"
        ) as diagnostic, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--check-recipient"]), 1)
            diagnostic.assert_not_called()
        self.assertNotIn("private", output.getvalue())

    def test_hidden_prompt_failure_never_falls_back_to_echoed_input(self):
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=getpass.GetPassWarning), patch(
            "imessage_agent.__main__.diagnose_applescript"
        ) as diagnostic, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--check-recipient"]), 1)
            diagnostic.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"error": "invalid_or_cancelled_recipient"})

    def test_structured_pre_dispatch_error_is_failed_but_dispatch_error_remains_unknown(self):
        for token, expected in (("PRE:recipient_lookup:-1728", SendStatus.FAILED),
                                ("PRE:connection_status:-1708", SendStatus.FAILED),
                                ("PRE:application:-1743", SendStatus.FAILED),
                                ("DISPATCH_ERROR:-1743", SendStatus.UNKNOWN),
                                ("DISPATCH_ERROR:-1712", SendStatus.UNKNOWN)):
            with self.subTest(token=token), patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], 0, token, "private")
            ):
                outcome = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "fictional text")
                self.assertEqual(outcome.status, expected)
                self.assertNotIn("private", outcome.detail)

    def test_nonzero_exit_never_reclassifies_a_claimed_send_as_retryable(self):
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "private syntax (-2741)")
        ):
            outcome = AppleScriptMessagesAdapter(enabled=True).dispatch(ALICE, "fictional")
        self.assertEqual(outcome.status, SendStatus.UNKNOWN)
        self.assertEqual(outcome.detail, "applescript_syntax_failed")
