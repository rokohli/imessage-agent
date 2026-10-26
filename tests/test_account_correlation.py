"""Synthetic correlated account metadata; no AppleScript or state access."""

import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.applescript import _ACCOUNT_LOOKUP, _account_probe_script, probe_accounts


def correlated(*rows):
    lines = [f"ACCOUNTS:{len(rows)}"]
    for sample, row in enumerate(rows, 1):
        values = dict(service_type="IMESSAGE", enabled="ENABLED", connection_status="CONNECTED",
                      account_id="PRESENT", identity="STABLE")
        values.update(row)
        lines.extend(f"{sample}:dereferenced:{stage}:{value}" for stage, value in values.items())
    return "\n".join(lines)


class AccountCorrelationTests(unittest.TestCase):
    def probe(self, stdout):
        with patch("imessage_agent.applescript.sys.platform", "darwin"), patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout, "private stderr")
        ) as runner:
            report = probe_accounts(correlate=True)
        self.assertNotIn("private", json.dumps(report))
        return report, runner

    def test_six_accounts_one_known_imessage_three_unknown_are_not_unique(self):
        report, _ = self.probe(correlated({}, {"service_type": "SMS"}, {"service_type": "RCS"},
                                         {"service_type": "ERROR:-10000"},
                                         {"service_type": "ERROR:-10000"},
                                         {"service_type": "ERROR:-10000", "enabled": "ERROR:-10000",
                                          "connection_status": "ERROR:-10000"}))
        self.assertEqual(report["accounts"], 6)
        self.assertEqual(report["known_imessage_accounts"], 1)
        self.assertEqual(report["unknown_service_accounts"], 3)
        self.assertFalse(report["account_selection_verified"])
        self.assertEqual(report["error"], "unknown_service_types_prevent_unique_selection")
        checks = report["account_checks"]
        self.assertEqual(checks[0]["service_type"], {"result": "imessage"})
        self.assertEqual(checks[0]["enabled"], {"result": "enabled"})
        self.assertEqual(checks[5]["service_type"]["apple_error_number"], -10000)
        self.assertEqual([row["sample"] for row in checks], list(range(1, 7)))

    def test_unknown_disabled_disconnected_account_still_cannot_be_excluded(self):
        report, _ = self.probe(correlated({}, dict(service_type="ERROR:-10000", enabled="DISABLED",
                                                 connection_status="DISCONNECTED")))
        self.assertFalse(report["account_selection_verified"])
        self.assertEqual(report["unknown_service_accounts"], 1)

    def test_known_imessage_metadata_is_correlated_not_borrowed_from_other_accounts(self):
        for field, value in (("enabled", "ERROR:-10000"), ("enabled", "DISABLED"),
                             ("connection_status", "DISCONNECTED"), ("account_id", "EMPTY")):
            with self.subTest(field=field, value=value):
                report, _ = self.probe(correlated({field: value}, {"service_type": "SMS"}))
                self.assertEqual(report["error"], "imessage_account_metadata_not_ready")
                self.assertFalse(report["account_selection_verified"])

    def test_fully_classified_unique_ready_account_is_only_diagnostic_evidence(self):
        report, _ = self.probe(correlated({}, {"service_type": "SMS", "enabled": "ERROR:-10000"},
                                         {"service_type": "RCS", "connection_status": "ERROR:-10000"}))
        self.assertTrue(report["account_selection_verified"])
        self.assertNotIn("error", report)
        self.assertNotIn("selected_account", report)
        self.assertNotIn("account_id_value", json.dumps(report))
        # Production still fails closed on any unreadable service type.
        self.assertIn("«property styp» of candidate", _ACCOUNT_LOOKUP)
        self.assertNotIn("on error", _ACCOUNT_LOOKUP)

    def test_missing_multiple_and_unrecognized_service_types_block_selection(self):
        for rows, expected in ((({"service_type": "SMS"},), "imessage_account_unavailable"),
                               (({}, {}), "imessage_account_ambiguous"),
                               (({}, {"service_type": "OTHER"}), "unknown_service_types_prevent_unique_selection")):
            self.assertEqual(self.probe(correlated(*rows))[0]["error"], expected)

    def test_identity_change_or_failed_verification_blocks_correlation(self):
        for value in ("CHANGED", "UNAVAILABLE", "ERROR:-1728"):
            report, _ = self.probe(correlated({}, {"service_type": "SMS", "identity": value}))
            self.assertEqual(report["error"], "account_identity_unverified")
            self.assertFalse(report["account_selection_verified"])

    def test_incomplete_duplicate_or_out_of_range_samples_are_rejected(self):
        valid = correlated({})
        for raw in (valid.replace("1:dereferenced:enabled", "2:dereferenced:enabled"),
                    valid.replace("1:dereferenced:enabled", "1:dereferenced:service_type"),
                    valid.replace("PRESENT", "private-account-id"),
                    valid.replace("1:dereferenced:identity:STABLE", ""), valid + "\nprivate raw reply"):
            self.assertEqual(self.probe(raw)[0], {"error": "invalid_probe_output"})

    def test_script_has_only_correlated_metadata_and_private_identity_checks(self):
        script = _account_probe_script(correlate=True)
        for excluded in ("ichtsend", "«class pres»", "«class imct»", "«property hndl»", "«property msdn»"):
            self.assertNotIn(excluded, script)
        self.assertEqual(script.count("on error number propertyError"), 4)
        self.assertIn("identityStart is identityEnd", script)
        self.assertNotIn('outputLines & identityStart', script)
        self.assertNotIn('outputLines & identityEnd', script)
        self.assertNotIn('whose', script)

    def test_cli_correlation_has_no_recipient_or_persistent_state_access(self):
        with patch("imessage_agent.__main__.probe_accounts", return_value=dict(
            error="unknown_service_types_prevent_unique_selection", accounts=6
        )) as probe, patch("getpass.getpass", side_effect=AssertionError("no recipient")), patch(
            "imessage_agent.storage.Store.__init__", side_effect=AssertionError("no state")
        ), patch("imessage_agent.macos.MacOSMessagesReader.__init__", side_effect=AssertionError("no database")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["diagnose-applescript", "--correlate-accounts"]), 1)
        probe.assert_called_once_with(correlate=True)

    def test_cli_modes_remain_mutually_exclusive(self):
        for other in ("--probe-accounts", "--check-recipient"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["diagnose-applescript", "--correlate-accounts", other])
            self.assertEqual(error.exception.code, 2)
