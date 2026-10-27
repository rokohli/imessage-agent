"""Interactive one-shot test. Never call from a worker, model or retry loop."""

import getpass
import hashlib
import re
import sqlite3
import stat
import os
import sys
import time
import uuid
import warnings
from contextlib import closing
from pathlib import Path

from .applescript import AppleScriptMessagesAdapter, _literal, diagnose_applescript
from .models import Contact, Recipient, SendStatus, payload_hash
from .outbound import Outbound
from .smoke import _schema
from .storage import Store, _SCHEMA, _OUTBOUND_SCHEMA, _DRAFT_SCHEMA
from .storage import _SCHEDULER_SCHEMA, _DASHBOARD_SCHEMA, _WORKER_SCHEMA


class SupervisedError(Exception):
    """Only fixed public codes; never message data or raw exceptions."""


def _existing_state(path: Path, recipient: Recipient, body: str) -> Path:
    if path.expanduser().is_symlink():
        raise SupervisedError("unsafe_outbound_state")
    try:
        path = path.expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise SupervisedError("existing_outbound_state_required") from None
    project = Path(__file__).resolve().parents[2]
    if project in path.parents or any((parent / ".git").exists() for parent in path.parents):
        raise SupervisedError("outbound_state_inside_repository")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != os.getuid() or info.st_nlink != 1):
        raise SupervisedError("unsafe_outbound_state")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        with closing(sqlite3.connect(":memory:")) as expected:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            expected.executescript(_SCHEMA + _OUTBOUND_SCHEMA + (_DRAFT_SCHEMA if version >= 3 else "") + (_SCHEDULER_SCHEMA if version >= 4 else "") + (_DASHBOARD_SCHEMA if version >= 5 else "") + (_WORKER_SCHEMA if version >= 6 else ""))
            if version not in (2, 3, 4, 5, 6) or _schema(connection) != _schema(expected):
                raise SupervisedError("unrecognized_outbound_state")
        if not connection.execute("SELECT 1 FROM send_attempts WHERE status='unknown' LIMIT 1").fetchone():
            raise SupervisedError("prior_unknown_history_missing")
        if connection.execute(
            "SELECT 1 FROM send_attempts WHERE recipient_key=? AND payload_hash=?",
            (recipient.key, payload_hash(recipient, body)),
        ).fetchone() or connection.execute(
            "SELECT 1 FROM outbound_claims WHERE body_hash=?",
            (hashlib.sha256(body.encode("utf-8")).hexdigest(),),
        ).fetchone():
            raise SupervisedError("new_message_required")
    return path


def send_one_supervised(state: Path) -> dict:
    """At most one Outbound.submit; confirmation precedes every persistent write."""
    if not sys.stdin.isatty():
        return {"status": "FAILED", "diagnostic": "interactive_terminal_required"}
    store = None
    submitting = False
    report = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            recipient = Recipient.parse(getpass.getpass("Authorized canonical test recipient (hidden): "))
            body = getpass.getpass("NEW authorized test text, never the prior UNKNOWN text (hidden): ")
        payload_hash(recipient, body)
        if len(body) > 4000:
            raise SupervisedError("invalid_test_text")
        _literal(body)
        state = _existing_state(state, recipient, body)
        preflight = diagnose_applescript(recipient)
        if "error" in preflight:
            return dict(status="FAILED", diagnostic=preflight["error"],
                        **{key: preflight[key] for key in ("stage", "apple_error_number") if key in preflight})
        if any(preflight.get(key) != 1 for key in ("accounts", "imessage_accounts", "recipient_matches")):
            raise SupervisedError("account_or_recipient_not_verified")
        confirmation = input("Approve the exact recipient and NEW text you entered, one attempt with no retry. "
                             "Type SEND ONE NEW IMESSAGE: ")
        if confirmation != "SEND ONE NEW IMESSAGE":
            return {"status": "FAILED", "diagnostic": "operator_cancelled"}
        store = Store(state)
        previous = store.contact(recipient)
        try:
            store.put_contact(Contact(recipient, "Supervised test recipient", True))
            approval_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
            store.approve(approval_id, recipient, body, time.time() + 120)
            submitting = True
            result = Outbound(store, AppleScriptMessagesAdapter(enabled=True)).submit(
                attempt_id, recipient, body, approval_id,
            )
            status = result.outcome.status
            if status not in (SendStatus.ACCEPTED, SendStatus.FAILED, SendStatus.UNKNOWN):
                status = SendStatus.UNKNOWN
            detail = result.outcome.detail
            diagnostic = detail if re.fullmatch(r"[a-z_]{1,80}", detail) else (
                "policy_denied" if status == SendStatus.FAILED else "transport_uncertain"
            )
            report = {"status": status.value.upper(), "diagnostic": diagnostic}
        finally:
            try:
                store.put_contact(previous or Contact(recipient, "Supervised test recipient", False))
            except Exception:
                if report is not None:
                    report["cleanup_error"] = "contact_restore_failed"
    except SupervisedError as error:
        report = {"status": "FAILED", "diagnostic": str(error)}
    except (EOFError, KeyboardInterrupt):
        report = {"status": "UNKNOWN" if submitting else "FAILED",
                  "diagnostic": "attempt_interrupted" if submitting else "operator_cancelled"}
    except Exception:
        # Once submit starts, a crash/error can follow an actual send. Never retry.
        report = {"status": "UNKNOWN" if submitting else "FAILED",
                  "diagnostic": "attempt_result_unavailable" if submitting else "test_preparation_failed"}
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                if report is not None:
                    report["cleanup_error"] = "state_close_failed"
    return report
