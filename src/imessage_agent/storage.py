"""SQLite-owned agent state. Never opens or discovers Apple databases."""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import (
    Approval, Batch, Contact, Cursor, Message, Recipient, Schedule,
    SendAttempt, SendOutcome, SendStatus, payload_hash,
)


_SCHEMA = """
CREATE TABLE contacts (
    recipient_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('phone','email')),
    address TEXT NOT NULL,
    display_name TEXT NOT NULL,
    outbound_allowed INTEGER NOT NULL DEFAULT 0 CHECK(outbound_allowed IN (0,1))
);
CREATE TABLE cursors (
    source_id TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0)
);
CREATE TABLE messages (
    source_id TEXT NOT NULL,
    guid TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    chat_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound','system')),
    sender_key TEXT,
    body TEXT,
    occurred_at REAL NOT NULL,
    PRIMARY KEY(source_id, guid),
    UNIQUE(source_id, sequence)
);
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_by TEXT UNIQUE REFERENCES send_attempts(id)
);
CREATE TABLE schedules (
    id TEXT PRIMARY KEY,
    recipient_key TEXT NOT NULL REFERENCES contacts(recipient_key),
    body TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    window_start REAL NOT NULL,
    window_end REAL NOT NULL,
    send_at REAL NOT NULL,
    timezone TEXT NOT NULL,
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','cancelled')),
    CHECK(window_start <= send_at AND send_at <= window_end)
);
CREATE TABLE send_attempts (
    id TEXT PRIMARY KEY,
    recipient_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('phone','email')),
    address TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    approval_id TEXT REFERENCES approvals(id),
    status TEXT NOT NULL CHECK(status IN ('accepted','sent','delivered','failed','partial','unknown')),
    detail TEXT NOT NULL,
    provider_id TEXT,
    created_at REAL NOT NULL
);
PRAGMA user_version = 1;
"""


_OUTBOUND_SCHEMA = """
CREATE TABLE outbound_claims (
    attempt_id TEXT PRIMARY KEY REFERENCES send_attempts(id),
    body_hash TEXT NOT NULL,
    reply_source TEXT,
    reply_guid TEXT,
    UNIQUE(reply_source, reply_guid),
    CHECK((reply_source IS NULL) = (reply_guid IS NULL))
);
PRAGMA user_version = 2;
"""


_DRAFT_SCHEMA = """
CREATE TABLE style_profiles (
    source_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    cutoff INTEGER NOT NULL,
    profile_json TEXT NOT NULL,
    PRIMARY KEY(source_id, profile_key)
);
CREATE TABLE reply_drafts (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    source_guid TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    recipient_key TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
PRAGMA user_version = 3;
"""


_SCHEDULER_SCHEMA = """
CREATE TABLE schedule_plans (
    id TEXT PRIMARY KEY,
    spec TEXT NOT NULL,
    authorization_hash TEXT NOT NULL,
    authorized_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','cancelled')),
    next_date TEXT NOT NULL,
    replaces TEXT REFERENCES schedule_plans(id)
);
CREATE TABLE schedule_occurrences (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES schedule_plans(id),
    local_date TEXT NOT NULL,
    window_start REAL NOT NULL,
    window_end REAL NOT NULL,
    send_at REAL NOT NULL,
    deadline REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('pending','cancelled','missed','unknown','accepted','sent','delivered','failed','partial')),
    detail TEXT NOT NULL DEFAULT '',
    claimed_at REAL,
    UNIQUE(plan_id, local_date),
    CHECK(window_start <= send_at AND send_at <= window_end AND deadline >= send_at)
);
CREATE INDEX schedule_due ON schedule_occurrences(status,send_at);
PRAGMA user_version = 4;
"""


_DASHBOARD_SCHEMA = """
CREATE TABLE dashboard_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
PRAGMA user_version = 5;
"""


_WORKER_SCHEMA = """
CREATE TABLE worker_notifications (
    event_key TEXT PRIMARY KEY,
    observed_at REAL NOT NULL
);
PRAGMA user_version = 6;
"""


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("identifiers must contain 1 to 128 characters")


def _finite(value: float) -> None:
    if not math.isfinite(value):
        raise ValueError("timestamp must be finite")


class Store:
    """One connection per thread; concurrent processes serialize write claims.

    Use a dedicated private state directory. The database is created owner-only;
    pre-existing symlinks, non-regular files, and permissive DB files are refused.
    """

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("state DB must be a private regular file (mode 0600)")
            if info.st_uid != os.getuid():
                raise ValueError("state DB must belong to the current user")
        finally:
            os.close(fd)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                version = self.connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2, 3, 4, 5, 6):
                    raise ValueError("unsupported agent schema version")
                if version == 0 and self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ValueError("refusing an unrecognized database")
                migrations = (_SCHEMA, _OUTBOUND_SCHEMA, _DRAFT_SCHEMA, _SCHEDULER_SCHEMA, _DASHBOARD_SCHEMA, _WORKER_SCHEMA)
                for migration in migrations[version:]:
                    for statement in migration.split(';'):
                        if statement.strip():
                            self.connection.execute(statement)
                # Discard the superseded bulk address-book cache without reading it.
                self.connection.execute("DELETE FROM dashboard_preferences WHERE key='contacts:cache'")
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def cursor(self, source_id: str) -> Cursor | None:
        row = self.connection.execute("SELECT * FROM cursors WHERE source_id=?", (source_id,)).fetchone()
        return Cursor(**dict(row)) if row else None

    def ingest(self, batch: Batch, previous: Cursor | None) -> int:
        _identifier(batch.source_id)
        _identifier(batch.generation)
        if previous and (previous.source_id != batch.source_id or previous.generation != batch.generation):
            raise ValueError("cursor stream identity mismatch")
        last = previous.sequence if previous else 0
        for message in batch.messages:
            if message.sequence <= last:
                raise ValueError("adapter must return a strictly sequence-ordered prefix")
            last = message.sequence
        inserted = 0
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.cursor(batch.source_id) != previous:
                raise ValueError("stale cursor; read again before committing")
            for m in batch.messages:
                values = (m.chat_id, m.direction.value, m.sender.key if m.sender else None, m.body, m.occurred_at)
                existing = self.connection.execute(
                    "SELECT chat_id,direction,sender_key,body,occurred_at FROM messages WHERE source_id=? AND guid=?",
                    (batch.source_id, m.guid),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != values:
                        raise ValueError("conflicting duplicate GUID; reconciliation is required")
                    continue
                self.connection.execute(
                    "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                    (batch.source_id, m.guid, m.sequence, *values),
                )
                inserted += 1
            self.connection.execute(
                "INSERT INTO cursors VALUES (?,?,?) ON CONFLICT(source_id) DO UPDATE SET sequence=excluded.sequence",
                (batch.source_id, batch.generation, last),
            )
        return inserted

    def messages(self, source_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM messages WHERE source_id=? ORDER BY sequence", (source_id,)
        ).fetchall()

    def put_contact(self, contact: Contact) -> None:
        """Trusted local administration only; ingestion never grants send rights."""
        if type(contact.outbound_allowed) is not bool:
            raise ValueError("outbound permission must be an explicit boolean")
        with self.connection:
            self.connection.execute(
                "INSERT INTO contacts VALUES (?,?,?,?,?) ON CONFLICT(recipient_key) DO UPDATE SET "
                "display_name=excluded.display_name,outbound_allowed=excluded.outbound_allowed",
                (contact.recipient.key, contact.recipient.kind, contact.recipient.address,
                 contact.display_name, contact.outbound_allowed),
            )

    def contact(self, recipient: Recipient) -> Contact | None:
        row = self.connection.execute("SELECT * FROM contacts WHERE recipient_key=?", (recipient.key,)).fetchone()
        return Contact(recipient, row["display_name"], bool(row["outbound_allowed"])) if row else None

    def approve(self, approval_id: str, recipient: Recipient, body: str, expires_at: float) -> Approval:
        """Trusted local administration only. This method is not proof of a human UI."""
        _identifier(approval_id)
        _finite(expires_at)
        digest = payload_hash(recipient, body)
        with self.connection:
            self.connection.execute("INSERT INTO approvals VALUES (?,?,?,NULL)", (approval_id, digest, expires_at))
        return self.approval(approval_id)

    def approval(self, approval_id: str) -> Approval | None:
        row = self.connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return Approval(**dict(row)) if row else None

    def put_schedule(self, schedule: Schedule) -> None:
        """Persist a reviewed plan only. Phase 1 has no scheduling worker."""
        _identifier(schedule.id)
        for value in (schedule.window_start, schedule.window_end, schedule.send_at):
            _finite(value)
        ZoneInfo(schedule.timezone)
        if not schedule.window_start <= schedule.send_at <= schedule.window_end:
            raise ValueError("chosen send time must fall inside the window")
        if schedule.status != "pending":
            raise ValueError("new schedules must be pending")
        digest = payload_hash(schedule.recipient, schedule.body)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            contact = self.contact(schedule.recipient)
            approval = self.approval(schedule.approval_id)
            if not contact or not contact.outbound_allowed:
                raise ValueError("recipient is not allowlisted")
            if not approval or approval.payload_hash != digest or approval.consumed_by:
                raise ValueError("schedule needs an unused approval for this exact payload")
            if approval.expires_at <= schedule.window_end:
                raise ValueError("approval must remain valid throughout the schedule window")
            self.connection.execute(
                "INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?)",
                (schedule.id, schedule.recipient.key, schedule.body, digest,
                 schedule.window_start, schedule.window_end, schedule.send_at,
                 schedule.timezone, schedule.approval_id, schedule.status),
            )

    def schedule(self, schedule_id: str) -> Schedule | None:
        row = self.connection.execute(
            "SELECT s.*,c.kind,c.address FROM schedules s JOIN contacts c ON s.recipient_key=c.recipient_key WHERE s.id=?",
            (schedule_id,),
        ).fetchone()
        if row is None:
            return None
        return Schedule(row["id"], Recipient(row["kind"], row["address"]), row["body"],
                        row["window_start"], row["window_end"], row["send_at"], row["timezone"],
                        row["approval_id"], row["status"])

    def cancel_schedule(self, schedule_id: str) -> bool:
        with self.connection:
            changed = self.connection.execute(
                "UPDATE schedules SET status='cancelled' WHERE id=? AND status='pending'", (schedule_id,)
            ).rowcount
        return changed == 1

    def attempt(self, attempt_id: str) -> SendAttempt | None:
        row = self.connection.execute("SELECT * FROM send_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            return None
        return SendAttempt(row["id"], Recipient(row["kind"], row["address"]), row["payload_hash"],
                           row["approval_id"], SendOutcome(SendStatus(row["status"]), row["detail"], row["provider_id"]),
                           row["created_at"])

    def claim_send(
        self, attempt_id: str, recipient: Recipient, body: str,
        approval_id: str | None, now: float, *, controlled: bool = False,
        reply_to: tuple[str, str] | None = None,
        schedule_occurrence: str | None = None,
        schedule_account_id: str | None = None,
    ) -> tuple[SendAttempt, bool]:
        _identifier(attempt_id)
        _finite(now)
        digest = payload_hash(recipient, body)
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if reply_to is not None:
            if not isinstance(reply_to, tuple) or len(reply_to) != 2:
                raise ValueError("reply_context_requires_source_and_guid")
            for value in reply_to:
                _identifier(value)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            previous = self.attempt(attempt_id)
            if previous:
                if previous.payload_hash != digest:
                    raise ValueError("attempt ID already belongs to another payload")
                return previous, False
            scheduled = None
            if schedule_occurrence is not None:
                from .scheduling import validate_occurrence, SchedulingError
                if attempt_id != schedule_occurrence or approval_id is not None or reply_to is not None:
                    raise SchedulingError('invalid_scheduled_attempt')
                scheduled, spec = validate_occurrence(self.connection, schedule_occurrence, recipient, body, now)
                if (controlled != (spec['transport'] == 'live')
                        or schedule_account_id != spec['account_id']):
                    raise SchedulingError('schedule_transport_mismatch')
                approval_id = 'schedule-' + schedule_occurrence
                self.connection.execute('INSERT INTO approvals VALUES (?,?,?,NULL)',
                                        (approval_id, digest, math.nextafter(scheduled['deadline'], math.inf)))
            contact = self.contact(recipient)
            approval = self.approval(approval_id) if approval_id else None
            denial = ""
            if not contact or not contact.outbound_allowed:
                denial = "Recipient is not allowlisted"
            elif not approval or approval.payload_hash != digest:
                denial = "Approval is missing or does not match the exact payload"
            elif approval.expires_at <= now:
                denial = "Approval has expired"
            elif approval.consumed_by:
                denial = "Approval was already consumed"
            if not denial and reply_to is not None:
                source_message = self.connection.execute(
                    "SELECT direction,sender_key,body FROM messages WHERE source_id=? AND guid=?", reply_to,
                ).fetchone()
                if (source_message is None or source_message["direction"] != "inbound"
                        or source_message["sender_key"] != recipient.key or source_message["body"] is None):
                    denial = "reply_context_not_inbound"
                elif self.connection.execute(
                    "SELECT 1 FROM outbound_claims WHERE reply_source=? AND reply_guid=?", reply_to,
                ).fetchone():
                    denial = "reply_already_reserved"
                elif self.connection.execute(
                    "SELECT 1 FROM outbound_claims WHERE body_hash=?",
                    (hashlib.sha256(source_message["body"].encode("utf-8")).hexdigest(),),
                ).fetchone():
                    denial = "agent_message_loop"
            if not denial and controlled:
                if self.connection.execute(
                    "SELECT 1 FROM outbound_claims c JOIN send_attempts a ON a.id=c.attempt_id "
                    "LEFT JOIN schedule_occurrences o ON o.id=a.id "
                    "WHERE a.recipient_key=? AND a.payload_hash=? AND NOT "
                    "COALESCE((o.plan_id=? AND o.status IN ('accepted','sent','delivered','failed') "
                    "AND a.status=o.status),0)",
                    (recipient.key, digest, scheduled['plan_id'] if scheduled is not None else ''),
                ).fetchone():
                    denial = "payload_already_reserved"
                counts = self.connection.execute(
                    "SELECT COUNT(*),COALESCE(SUM(a.recipient_key=?),0) FROM outbound_claims c "
                    "JOIN send_attempts a ON a.id=c.attempt_id WHERE a.created_at>?",
                    (recipient.key, now - 60),
                ).fetchone()
                if not denial and (counts[0] >= 3 or counts[1] >= 1):
                    denial = "rate_limited"
            outcome = SendOutcome(SendStatus.FAILED, denial) if denial else SendOutcome(
                SendStatus.UNKNOWN, "Dispatch reserved; outcome may require reconciliation"
            )
            self.connection.execute(
                "INSERT INTO send_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, recipient.key, recipient.kind, recipient.address, digest,
                 approval.id if approval else None, outcome.status.value, outcome.detail, None, now),
            )
            if not denial:
                if controlled or reply_to is not None:
                    self.connection.execute(
                        "INSERT INTO outbound_claims VALUES (?,?,?,?)",
                        (attempt_id, body_hash, *(reply_to if reply_to else (None, None))),
                    )
                self.connection.execute("UPDATE approvals SET consumed_by=? WHERE id=?", (attempt_id, approval.id))
        return self.attempt(attempt_id), not denial

    def finish_send(self, attempt_id: str, outcome: SendOutcome) -> SendAttempt:
        with self.connection:
            changed = self.connection.execute(
                "UPDATE send_attempts SET status=?,detail=?,provider_id=? WHERE id=? AND status='unknown' AND approval_id IS NOT NULL",
                (outcome.status.value, outcome.detail, outcome.provider_id, attempt_id),
            ).rowcount
            if changed != 1:
                raise ValueError("attempt is missing or already resolved")
        return self.attempt(attempt_id)
