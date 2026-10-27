"""Local drafting, content-free diagnostics and a separately supervised send test."""

import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

from .applescript import diagnose_applescript, list_imessage_accounts, probe_accounts
from .account_selection import AccountSelectionError, save_account_id, selection_path
from .models import Recipient
from .macos import MacOSMessagesReader, MessagesAccessError
from .smoke import SmokeError, run
from .validation import validate_classification
from .supervised import send_one_supervised
from .draft_cli import COMMANDS, add_commands, run_command
from . import schedule_cli, launchagent, contacts


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '{"error": "invalid_arguments"}\n')


def _setup_account() -> int:
    if not sys.stdin.isatty():
        print(json.dumps({"error": "interactive_terminal_required"}))
        return 1
    try:
        path = selection_path()
        if path.exists() or path.is_symlink():
            raise AccountSelectionError("account_selection_already_exists")
        accounts = list_imessage_accounts()
        for index in range(1, len(accounts) + 1):
            print(f"{index}. iMessage account {index}")
        choice = input("Select an iMessage account number (no send): ")
        if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(accounts):
            raise AccountSelectionError("invalid_account_choice")
        account_id = accounts[int(choice) - 1]
        report = diagnose_applescript(account_id=account_id)
        if "error" in report:
            print(json.dumps(report, sort_keys=True))
            return 1
        if report.get("accounts") != 1 or report.get("imessage_accounts") != 1:
            raise AccountSelectionError("selected_account_verification_failed")
        save_account_id(account_id)
        print(json.dumps({"account_saved": True, "live_sending": "disabled"}))
        return 0
    except AccountSelectionError as error:
        print(json.dumps({"error": str(error)}))
    except (EOFError, KeyboardInterrupt):
        print(json.dumps({"error": "account_setup_cancelled"}))
    except (OSError, ValueError):
        print(json.dumps({"error": "account_setup_unavailable"}))
    return 1


def main(argv=None) -> int:
    parser = SafeParser(description="Read-only Messages schema diagnostic (no message contents).")
    commands = parser.add_subparsers(dest="command", required=True)
    diagnose = commands.add_parser("diagnose", help="Check only the explicit database path")
    diagnose.add_argument("--database", type=Path, required=True)
    smoke = commands.add_parser("ingest-smoke-test", help="Bounded read-only ingestion; dry run unless --commit")
    smoke.add_argument("--database", type=Path, required=True)
    smoke.add_argument("--state", type=Path, required=True)
    smoke.add_argument("--limit", type=int, default=10)
    mode = smoke.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--replay", action="store_true", help="Check saved-record deduplication in temporary state")
    classify = commands.add_parser("validate-classification", help="Check selected fictional message directions")
    classify.add_argument("--database", type=Path, required=True)
    classify.add_argument("--selection", type=Path, required=True)
    classify.add_argument("--fictional-only", action="store_true", required=True,
                          help="Attest that every selected record is a fictional test message")
    scripting = commands.add_parser("diagnose-applescript", help="No-send scripting/account compatibility checks")
    scripting_mode = scripting.add_mutually_exclusive_group()
    scripting_mode.add_argument("--probe-accounts", action="store_true",
                                help="Probe account metadata independently; never recipients or sending")
    scripting_mode.add_argument("--correlate-accounts", action="store_true",
                                help="Correlate metadata on each account using temporary sample numbers; no sending")
    scripting_mode.add_argument("--check-recipient", action="store_true",
                           help="Authorize a lookup for a canonical test address entered at a hidden prompt")
    commands.add_parser("setup-imessage-account", help="Select and privately save a verified iMessage account; no send")
    supervised = commands.add_parser("send-supervised-test", help="LIVE: one manually confirmed NEW iMessage, no retries")
    supervised.add_argument("--state", type=Path, required=True, help="Existing outbound state containing the prior UNKNOWN")
    contacts.add_commands(commands)
    add_commands(commands)
    schedule_cli.add_commands(commands)
    launchagent.add_commands(commands)
    dashboard = commands.add_parser('dashboard', help='Loopback-only local web dashboard; never starts a worker')
    dashboard.add_argument('--state', type=Path, default=Path.home() / '.local/state/imessage-agent/state.sqlite3')
    dashboard.add_argument('--database', type=Path, help='Explicit read-only Messages source for diagnostics/regeneration')
    args = parser.parse_args(argv)
    if args.command == 'contacts':
        return contacts.run_command(args)
    if args.command in ('background-worker', 'launchagent'):
        return launchagent.run_command(args)
    if args.command == 'dashboard':
        from .dashboard import run_dashboard
        return run_dashboard(args)
    if args.command in schedule_cli.COMMANDS:
        return schedule_cli.run_command(args)
    if args.command in COMMANDS:
        return run_command(args)
    if args.command == "send-supervised-test":
        report = send_one_supervised(args.state)
        print(json.dumps(report, sort_keys=True))
        return {"ACCEPTED": 0, "FAILED": 1, "UNKNOWN": 3}[report["status"]]
    if args.command == "setup-imessage-account":
        return _setup_account()
    if args.command == "diagnose-applescript":
        if args.probe_accounts or args.correlate_accounts:
            report = probe_accounts(correlate=True) if args.correlate_accounts else probe_accounts()
            print(json.dumps(report, sort_keys=True))
            return int("error" in report or any("error" in entry or entry.get("result") in ("invalid_type", "other", "empty")
                                               for entry in report.get("probes", [])))
        recipient = None
        if args.check_recipient:
            if not sys.stdin.isatty():
                print(json.dumps({"error": "interactive_terminal_required"}))
                return 1
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    recipient = Recipient.parse(getpass.getpass("Authorized canonical test address (hidden; no send): "))
            except (ValueError, UnicodeError, EOFError, KeyboardInterrupt, getpass.GetPassWarning):
                print(json.dumps({"error": "invalid_or_cancelled_recipient"}))
                return 1
        report = diagnose_applescript(recipient, selected=True)
        print(json.dumps(report, sort_keys=True))
        return int("error" in report)
    if args.command in ("ingest-smoke-test", "validate-classification"):
        try:
            if args.command == "validate-classification":
                report = validate_classification(args.database, args.selection)
                status = int(report["mismatched"] != 0)
            else:
                report = run(args.database, args.state, commit=args.commit, limit=args.limit, replay=args.replay)
                status = 0
        except (SmokeError, MessagesAccessError) as error:
            # Only these classes carry fixed codes; never print raw exception details.
            report = {"error": str(error)}
            status = 1
        print(json.dumps(report, sort_keys=True))
        return status
    report = {
        "database": "unavailable", "schema": "not_checked", "live_sending": "disabled",
        "permissions": "File read access required. Protected Messages access may require macOS Full Disk Access "
        "for the responsible terminal/host app; this tool never requests it. Contacts, Automation and "
        "Accessibility permissions are not needed. See docs/macos-validation.md.",
        "python_executable": sys.executable,
    }
    try:
        with MacOSMessagesReader(args.database):
            pass
        report.update(database="available", schema="compatible")
        status = 0
    except MessagesAccessError as error:
        code = str(error)
        report["error"] = code
        if code == "unsupported_schema":
            report.update(database="available", schema="unsupported")
        status = 1
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
