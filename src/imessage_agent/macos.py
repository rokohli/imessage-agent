"""Explicit-path, read-only Messages access. No discovery, dispatch, or logging."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import stat
from pathlib import Path

from .attributed import extract_body_from_attributed
from .models import Batch, Direction, Message, Recipient


class MessagesAccessError(Exception):
    """Fixed, content-free error code suitable for diagnostics."""


def _file_identity(path: Path) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise MessagesAccessError("not_regular_file")
    # ctime changes during normal writes; birthtime (where available) does not.
    return f"{info.st_dev}:{info.st_ino}:{getattr(info, 'st_birthtime', 0)}"


class MacOSMessagesReader:
    """ROWID-ordered ingestion into the existing durable inbox, never a sender.

    Unknown/ambiguous identities become SYSTEM records, never reply candidates.
    Invalid required record data stops the batch rather than skipping a ROWID.
    """

    def __init__(self, path: Path) -> None:
        self._connection: sqlite3.Connection | None = None
        try:
            self.path = Path(path).expanduser().resolve(strict=True)
            self.generation = _file_identity(self.path)
            self.source_id = "messages:" + hashlib.sha256(str(self.path).encode()).hexdigest()
            self._connection = sqlite3.connect(
                self.path.as_uri() + "?mode=ro", uri=True, timeout=2,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only=ON")
            self._connection.execute("PRAGMA trusted_schema=OFF")
            self._connection.set_authorizer(self._authorize)
            self._inspect_schema()
            self._check_identity()
        except (OSError, sqlite3.Error, MessagesAccessError) as error:
            self.close()
            if isinstance(error, MessagesAccessError):
                raise
            if isinstance(error, FileNotFoundError):
                code = "not_found"
            elif isinstance(error, PermissionError):
                code = "permission_denied"
            else:
                code = "unavailable_or_unreadable"
            raise MessagesAccessError(code) from None

    @staticmethod
    def _authorize(action, first, second, database, trigger):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                      sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first in ("table_info", "query_only") and (
            first == "table_info" or second is None
        ):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _inspect_schema(self) -> None:
        required = {
            "message": {"guid", "date", "is_from_me", "handle_id"},
            "handle": {"id"},
            "chat": set(),
            "chat_message_join": {"chat_id", "message_id"},
        }
        columns = {}
        for table, needed in required.items():
            kind = self._connection.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (table,),
            ).fetchone()
            if kind is None or kind[0] != "table":
                raise MessagesAccessError("unsupported_schema")
            columns[table] = {row["name"] for row in self._connection.execute(f'PRAGMA table_info("{table}")')}
            if not needed <= columns[table]:
                raise MessagesAccessError("unsupported_schema")
        self._message_columns = columns["message"]
        self._chat_column = next((name for name in ("guid", "chat_identifier") if name in columns["chat"]), None)
        if self._chat_column is None or not {"text", "attributedBody"} & self._message_columns:
            raise MessagesAccessError("unsupported_schema")
        # Verify ROWID support without fetching any personal records.
        for table in required:
            self._connection.execute(f'SELECT ROWID FROM "{table}" LIMIT 0')

    def _check_identity(self) -> None:
        if _file_identity(self.path) != self.generation:
            raise MessagesAccessError("source_replaced")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read_after(self, sequence: int, limit: int) -> Batch:
        if type(sequence) is not int or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if self._connection is None:
            raise MessagesAccessError("reader_closed")
        try:
            self._check_identity()
            self._connection.execute("BEGIN")
            maximum = self._connection.execute("SELECT COALESCE(MAX(ROWID),0) FROM message").fetchone()[0]
            if maximum < sequence:
                raise MessagesAccessError("sequence_rollback")
            optional = ("text", "attributedBody", "item_type", "is_system_message", "associated_message_type")
            fields = ",".join(f'm."{name}"' if name in self._message_columns else f'NULL AS "{name}"' for name in optional)
            rows = self._connection.execute(
                f"SELECT m.ROWID AS sequence,m.guid,m.date,m.is_from_me,h.id AS sender,{fields} "
                "FROM message m LEFT JOIN handle h ON h.ROWID=m.handle_id "
                "WHERE m.ROWID>? ORDER BY m.ROWID LIMIT ?", (sequence, limit),
            ).fetchall()
            messages = tuple(self._message(row) for row in rows)
            self._check_identity()
            return Batch(self.source_id, self.generation, messages)
        except MessagesAccessError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
            raise MessagesAccessError("read_failed_or_invalid_record") from None
        finally:
            self._connection.rollback()

    def _message(self, row: sqlite3.Row) -> Message:
        guid = row["guid"]
        if not isinstance(guid, str) or not guid.strip():
            raise MessagesAccessError("invalid_guid")
        chats = self._connection.execute(
            f'SELECT DISTINCT c."{self._chat_column}" FROM chat_message_join j '
            'LEFT JOIN chat c ON c.ROWID=j.chat_id WHERE j.message_id=?', (row["sequence"],),
        ).fetchall()
        chat_id = chats[0][0] if len(chats) == 1 else None
        known_chat = isinstance(chat_id, str) and bool(chat_id.strip())
        if not known_chat:
            chat_id = "unresolved:" + guid
        sender = None
        if isinstance(row["sender"], str):
            try:
                sender = Recipient.parse(row["sender"])
            except (ValueError, UnicodeError):
                pass
        direction = Direction.SYSTEM
        if row["is_from_me"] == 1:
            direction = Direction.OUTBOUND
            sender = None
        elif row["is_from_me"] == 0 and sender is not None and known_chat and not any(
            row[name] not in (None, 0) for name in ("item_type", "is_system_message", "associated_message_type")
        ):
            direction = Direction.INBOUND
        body = row["text"] if isinstance(row["text"], str) and row["text"] else None
        if body is None:
            body = extract_body_from_attributed(row["attributedBody"])
        if body is not None:
            body = body.replace("\ufffc", "")
            if not body.strip():
                body = None
        date = row["date"]
        if not isinstance(date, (int, float)) or not math.isfinite(date):
            raise MessagesAccessError("invalid_timestamp")
        # shortcut: Apple epoch seconds or nanoseconds inferred by magnitude;
        # add an explicit schema variant if Apple introduces another date unit.
        occurred_at = date / 1_000_000_000 if abs(date) >= 100_000_000_000 else date
        return Message(row["sequence"], guid, chat_id, direction, sender, body, occurred_at + 978307200)
