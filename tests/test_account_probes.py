"""Account probes are mocked, aggregate-only and never select a send account."""

import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.applescript import (
    _ACCOUNT_LOOKUP, _PROBE_OBJECTS, _PROBE_PROPERTIES, _account_probe_script, _error_code, probe_accounts,
)


def matrix(count=1, overrides=None):
    values = dict(service_type="IMESSAGE", enabled="ENABLED", connection_status="CONNECTED", account_id="PRESENT")
    lines = [f"ACCOUNTS:{count}"]
    for _ in range(count):
        for style in _PROBE_OBJECTS:
            for stage in _PROBE_PROPERTIES:
                value = (overrides or {}).get((style, stage), values[stage])
                lines.append(f"{style}:{stage}:{value}")
    return "\n".join(lines)


class AccountProbeTests(unittest.TestCase):
    def probe(self, stdout, *, code=0, stderr="private account contents"):
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], code, stdout, stderr)
        ) as runner:
            report = probe_accounts()
        self.assertNotIn("private", json.dumps(report))
        return report, runner

    def test_metadata_queries_are_independent_and_never_include_send_or_recipients(self):
        script = _account_probe_script()
        self.assertIn('set lookupStage to "account_count"', script)
        self.assertIn('set lookupStage to "account_enumeration"', script)
        self.assertIn("of accountRef", script)
        self.assertIn("of (contents of accountRef)", script)
        self.assertIn("of («class icsv» accountIndex)", script)
        self.assertEqual(script.count("on error number propertyError"), 12)
        self.assertIn("accountCount > 10", script)
        for disallowed in ("ichtsend", "«class pres»", "«property hndl»", "«class imct»", "msdn"):
            self.assertNotIn(disallowed, script)
        self.assertNotIn("propertyValue as text", script)

    def test_mixed_property_failures_are_aggregated_without_masking_success(self):
        report, _ = self.probe(matrix(2, {("list_reference", "service_type"): "ERROR:-1728"}))
        self.assertEqual(report["accounts"], 2)
        errors = [entry for entry in report["probes"] if "error" in entry]
        self.assertEqual(errors, [dict(object_access="list_reference", stage="service_type",
                                       error="apple_event_object_not_found", apple_error_number=-1728, count=2)])
        successes = [entry for entry in report["probes"] if entry.get("result") == "imessage"]
        self.assertEqual(len(successes), 2)
        self.assertTrue(all(entry["count"] == 2 for entry in successes))
        # Probe results do not edit the strict transport lookup or choose a fallback.
        self.assertIn("«property styp» of candidate", _ACCOUNT_LOOKUP)
        self.assertIn("imessageCount is not 1", _ACCOUNT_LOOKUP)

    def test_service_types_and_disconnection_are_fixed_categories(self):
        for value in ("SMS", "RCS", "OTHER"):
            report, _ = self.probe(matrix(overrides={("direct_index", "service_type"): value,
                                                    ("direct_index", "connection_status"): "DISCONNECTED"}))
            self.assertTrue(any(entry.get("result") == value.lower() for entry in report["probes"]))
            self.assertTrue(any(entry.get("result") == "disconnected" for entry in report["probes"]))
            self.assertNotIn("selected_account", report)

    def test_object_errors_are_not_mislabeled_unsupported_properties(self):
        expected = {-1728: "apple_event_object_not_found", -1700: "apple_event_type_mismatch",
                    -1708: "apple_event_not_handled", -10000: "apple_event_handler_failed",
                    -1743: "automation_not_authorized", -10005: "read_authorization_failed",
                    -1723: "unsupported_scripting_property", -1712: "apple_event_timed_out",
                    -1705: "invalid_object_reference"}
        for number, error in expected.items():
            self.assertEqual(_error_code(number, "service_type"), error)

    def test_enumeration_and_property_failures_have_distinct_stages(self):
        for stage in ("application", "account_count", "account_enumeration"):
            report, _ = self.probe(f"PROBE_ERROR:{stage}:-1728")
            self.assertEqual(report, dict(error="apple_event_object_not_found", stage=stage, apple_error_number=-1728))
        report, _ = self.probe(matrix(overrides={("direct_index", "enabled"): "ERROR:-1743"}))
        self.assertTrue(any(entry.get("error") == "automation_not_authorized" for entry in report["probes"]))

    def test_malformed_incomplete_and_personal_output_are_rejected(self):
        valid = matrix()
        for stdout in ("private reply", valid + "\nprivate reply", valid.replace("IMESSAGE", "private address"),
                       valid.replace("direct_index:enabled:ENABLED", "list_reference:enabled:ENABLED"),
                       "\n".join(valid.splitlines()[:-1]), "ACCOUNTS:11", "ACCOUNTS:1\nERROR:private"):
            self.assertEqual(self.probe(stdout)[0], {"error": "invalid_probe_output"})

    def test_zero_accounts_is_not_compatibility_success(self):
        self.assertEqual(self.probe("ACCOUNTS:0")[0],
                         dict(accounts=0, probes=[], error="imessage_account_unavailable"))

    def test_limit_changes_process_errors_and_timeouts_are_sanitized(self):
        self.assertEqual(self.probe("PROBE_LIMIT")[0], {"error": "account_probe_limit"})
        self.assertEqual(self.probe("PROBE_CHANGED")[0], {"error": "accounts_changed_during_probe"})
        self.assertEqual(self.probe("", code=1, stderr="private (-2741)")[0],
                         dict(error="applescript_syntax_failed", stage="unconfirmed", apple_error_number=-2741))
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("private", 15)
        ):
            self.assertEqual(probe_accounts(), {"error": "diagnostic_unavailable"})

    def test_cli_does_not_read_state_source_or_prompt_for_recipient(self):
        with patch("imessage_agent.__main__.probe_accounts", return_value={"accounts": 1, "probes": []}), patch(
            "imessage_agent.storage.Store.__init__", side_effect=AssertionError("no state")
        ), patch("imessage_agent.macos.MacOSMessagesReader.__init__", side_effect=AssertionError("no source")), patch(
            "getpass.getpass", side_effect=AssertionError("no recipient")
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["diagnose-applescript", "--probe-accounts"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"accounts": 1, "probes": []})

    def test_cli_probe_and_recipient_flags_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["diagnose-applescript", "--probe-accounts", "--check-recipient"])
        self.assertEqual(error.exception.code, 2)

    def test_cli_reports_partial_probe_failure_with_nonzero_exit(self):
        with patch("imessage_agent.__main__.probe_accounts", return_value={"accounts": 1, "probes": [
            dict(stage="service_type", error="apple_event_object_not_found", apple_error_number=-1728)
        ]}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["diagnose-applescript", "--probe-accounts"]), 1)
