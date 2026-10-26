"""Bounded ingestion with private state and content-free reports."""

import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from pathlib import Path

from .ingestion import ingest_once
from .macos import MacOSMessagesReader, MessagesAccessError
from .storage import Store, _SCHEMA, _OUTBOUND_SCHEMA


class SmokeError(Exception):
    """A fixed public error code, never exception details."""


def _schema(connection):
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()


def _state_path(database: Path, state: Path) -> Path:
    source = database.expanduser().resolve()
    candidate = state.expanduser()
    if candidate.is_symlink():
        raise SmokeError("unsafe_state_path")
    candidate = candidate.resolve()
    project = Path(__file__).resolve().parents[2]
    roots = {project}
    for path in (project, candidate.parent):
        roots.update(parent for parent in (path, *path.parents) if (parent / ".git").exists())
    if any(candidate == root or root in candidate.parents for root in roots):
        raise SmokeError("state_inside_repository")
    if candidate == source or (candidate.exists() and source.exists() and candidate.samefile(source)):
        raise SmokeError("state_is_source")
    # SQLite may write these companions even though the main state path differs.
    companions = {Path(str(candidate) + suffix) for suffix in ("-wal", "-shm", "-journal")}
    if source in companions or any(path.exists() and source.exists() and path.samefile(source) for path in companions):
        raise SmokeError("state_is_source")
    if candidate in {Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")}:
        raise SmokeError("state_is_source")
    if candidate.exists():
        info = candidate.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
            raise SmokeError("unsafe_state_file")
        if info.st_nlink != 1:
            raise SmokeError("unsafe_state_file")
    return candidate


def _replay(store: Store, reader: MacOSMessagesReader, limit: int) -> dict:
    saved = store.cursor(reader.source_id)
    if saved is None:
        raise SmokeError("replay_requires_saved_cursor")
    if saved.generation != reader.generation:
        raise SmokeError("ingestion_validation_failed")
    stored_before = store.connection.execute(
        "SELECT COUNT(*) FROM messages WHERE source_id=?", (reader.source_id,),
    ).fetchone()[0]
    if not stored_before:
        raise SmokeError("replay_requires_messages")
    batch = reader.read_after(0, min(limit, stored_before))
    if not batch.messages:
        raise SmokeError("replay_records_missing")
    for message in batch.messages:
        if message.sequence > saved.sequence or store.connection.execute(
            "SELECT 1 FROM messages WHERE source_id=? AND guid=?", (reader.source_id, message.guid),
        ).fetchone() is None:
            raise SmokeError("replay_record_not_saved")
    # Rewind only the disposable copy; Store.ingest checks duplicate payloads atomically.
    with store.connection:
        store.connection.execute("DELETE FROM cursors WHERE source_id=?", (reader.source_id,))
    inserted = store.ingest(batch, None)
    stored_after = store.connection.execute(
        "SELECT COUNT(*) FROM messages WHERE source_id=?", (reader.source_id,),
    ).fetchone()[0]
    if inserted or stored_before != stored_after:
        raise SmokeError("replay_deduplication_failed")
    return dict(checked=len(batch.messages), inserted=inserted, stored_before=stored_before,
                stored_after=stored_after, cursor_saved=saved.sequence, replay_cursor_before=0,
                replay_cursor_after=store.cursor(reader.source_id).sequence)


def run(database: Path, state: Path, *, commit: bool = False, limit: int = 10, replay: bool = False) -> dict:
    """Never open persistent state for writing in a dry run."""
    if type(limit) is not int or not 1 <= limit <= 25:
        raise SmokeError("invalid_limit")
    if replay and commit:
        raise SmokeError("replay_cannot_commit")
    try:
        state = _state_path(database, state)
        if replay and not state.exists():
            raise SmokeError("replay_requires_state")
        with tempfile.TemporaryDirectory(prefix="imessage-smoke-") as directory:
            temporary = Store(Path(directory) / "state.sqlite3")
            try:
                if state.exists():
                    with closing(sqlite3.connect(state.as_uri() + "?mode=ro", uri=True, timeout=2)) as existing:
                        existing.execute("PRAGMA query_only=ON")
                        existing.execute("PRAGMA trusted_schema=OFF")
                        version = existing.execute("PRAGMA user_version").fetchone()[0]
                        with closing(sqlite3.connect(":memory:")) as expected:
                            expected.executescript(_SCHEMA + (_OUTBOUND_SCHEMA if version == 2 else ""))
                            if version not in (1, 2) or _schema(existing) != _schema(expected):
                                raise SmokeError("unrecognized_state")
                        if not commit:
                            existing.backup(temporary.connection)
                            if version == 1:
                                temporary.connection.executescript("BEGIN IMMEDIATE;" + _OUTBOUND_SCHEMA + "COMMIT;")
                # Open the source before creating persistent state, so access errors leave no new DB.
                with MacOSMessagesReader(database) as reader:
                    store = Store(state) if commit else temporary
                    try:
                        if replay:
                            return _replay(store, reader, limit)
                        previous = store.cursor(reader.source_id)
                        before = previous.sequence if previous else 0
                        inserted = ingest_once(store, reader, limit)
                        after = store.cursor(reader.source_id).sequence
                        counts = dict(store.connection.execute(
                            "SELECT direction,COUNT(*) FROM messages WHERE source_id=? AND sequence>? "
                            "GROUP BY direction", (reader.source_id, before),
                        ).fetchall())
                        null_bodies = store.connection.execute(
                            "SELECT COUNT(*) FROM messages WHERE source_id=? AND sequence>? AND body IS NULL",
                            (reader.source_id, before),
                        ).fetchone()[0]
                        return dict(inserted=inserted, inbound=counts.get("inbound", 0),
                                    outbound=counts.get("outbound", 0), system=counts.get("system", 0),
                                    null_bodies=null_bodies, cursor_before=before, cursor_after=after)
                    finally:
                        if commit:
                            store.close()
            finally:
                temporary.close()
    except (SmokeError, MessagesAccessError):
        raise
    except (OSError, sqlite3.Error, RuntimeError):
        raise SmokeError("state_unavailable") from None
    except (ValueError, TypeError, OverflowError):
        raise SmokeError("ingestion_validation_failed") from None
