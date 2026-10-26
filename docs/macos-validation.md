# Read-only macOS validation (manual, separate authorization)

The owner reports that the schema-only diagnostic succeeded using
`/opt/anaconda3/bin/python3`: database available, schema compatible, live sending
disabled. Content ingestion has **not** been validated against personal data.
Do not run it until the owner explicitly authorizes the database path and scope.
Prefer a dedicated macOS test account containing only fictional conversations.
Authorization to run `diagnose` is schema-only; content ingestion is a separate
scope. Do not post command output containing personal data.

## Permissions and responsible executable

Synthetic fixtures and CLI help need no privacy permissions. Direct SQLite reads
require ordinary read access to the explicit database and its parent directories;
WAL databases also require readable existing WAL/shared-memory files. Protected
Messages data is controlled by macOS TCC and normally requires Full Disk Access
for the responsible app. There is no Messages-only SQLite permission that this
Python package can request. This document records that platform limitation; it
does not request unrestricted filesystem access or instruct you to grant it.
If existing permissions do not allow access, stop and report the blocker. Do not
use sudo, change file modes, reset TCC, grant access to Codex, or bypass protection.

The process that opens SQLite is the Python executable used in the command
(`sys.executable`, also printed by diagnostics). For an interactive command in
Apple Terminal, the responsible host is `/System/Applications/Utilities/Terminal.app`;
in iTerm it is iTerm.app; in an IDE terminal it may be the IDE. macOS attributes
TCC access to that host, so granting another terminal or another Python does not
establish permission for this launch context. A separately launched Python helper
or packaged host has its own responsible executable/identity and must be assessed
separately; there is no installed helper or daemon here. The diagnostic does not
inspect TCC or prove which app has a grant; it tests effective access in the
current context. Keep the interpreter and host fixed when comparing results.

No **Contacts**, **Automation (Messages)**, **Accessibility**, screen recording,
network, or AI-provider permissions are required. No permission prompts are
requested by the code. Apple's description of protected data and responsible-app
access is in [Controlling app access to files](https://support.apple.com/en-ca/guide/security/secddd1d86a6/web).
Exact TCC behavior on the user's macOS release remains subject to authorized
validation; access denial alone cannot distinguish TCC from ordinary file ACLs.

## Schema-only diagnostic

From the project root, choose an already installed Python 3.10+ interpreter.
Substitute the **explicitly authorized** database path; there is deliberately no
default path or database discovery. The familiar personal location is
`~/Library/Messages/chat.db`, but do not probe it without authorization.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /path/to/python3 -m imessage_agent diagnose --database /authorized/path/chat.db
```

Exit 0 means the file is available and its schema is compatible; exit 1 reports a
fixed error code. `unsupported_schema` means the database opened but required
tables/columns are absent. Other codes distinguish missing files, permission
denial when the OS exposes it, and generic unreadability. Generic SQLite errors
can also mean TCC denial, locks, corruption or unsupported schema details; no raw
exception is printed. Exit 2 is CLI usage error. The output includes no message
contents, addresses, chat IDs, row counts, or database path. It does not query
message rows. A compatible schema is not proof that every row is decodable.

## Bounded content ingestion (separate manual authorization)

The commands below are for the owner to run manually after authorizing the source
and scope. Implementation and automated tests use synthetic databases only.
Choose a private local state directory outside **every Git checkout**, backups
and sync folders. Committed state contains plaintext message bodies, identifiers
and chat metadata; do not share it. Dry runs also temporarily handle plaintext
in an automatically cleaned private temporary directory (not secure erasure).
Keep this state dedicated to this source and do not run concurrent state writers.

Set these variables in the same terminal for all commands. Replace the source
placeholder with the exact authorized path. The state example is outside this
project; choose a different private, nonsynced location if needed. No permissions
are changed by these commands.

```sh
cd /Users/rohankohli/imessage-agent
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
SMOKE_DATABASE='/authorized/path/chat.db'
SMOKE_STATE="$HOME/.local/state/imessage-agent-validation/inbox.sqlite3"
```

1. Read-only dry run (default; no persistent state is created or advanced):

```sh
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE"
```

2. Explicitly authorized committed test (imports at most ten rows):

```sh
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE" --limit 10 --commit
```

3. Second run in a new process, reusing exactly the same source and state:

```sh
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE" --limit 10 --commit
```

4. Restart-recovery check: exit the terminal, open a fresh terminal in the same
host app, repeat the variable setup above, then run:

```sh
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE" --limit 10
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE" --limit 10 --commit
```

The dry run's `cursor_before` must equal the previous committed `cursor_after`.
It previews the next bounded batch using an isolated state copy. The following
commit must start at that same cursor if no other writer has changed state;
new source messages may change the previewed results. No interrupted ingestion
should leave a partly committed batch: messages and cursor share a transaction.
Do not simulate failures by editing or replacing the real database; rollback,
state-write failure, source replacement and recovery are tested synthetically.

Every invocation defaults to ten processed rows; `--limit` accepts only 1–25.
There is no automatic polling or backlog drain. A second run normally imports the
**next** batch if older pending rows remain. Only when the explicitly authorized
backlog is exhausted and no new rows arrive should `inserted` be zero and both
cursor positions remain equal. If additional batches are authorized, invoke the
same committed command manually until idle, then repeat once more to verify
zero inserts. Do not interpret new rows in a second batch as duplicates.

Output contains only `inserted`, `inbound`, `outbound`, `system`, `null_bodies`,
`cursor_before`, `cursor_after`, or a fixed `error` code. Direction/null counts
refer to newly inserted records. Identical repeated GUIDs are deduplicated while
the cursor still advances across their ROWIDs. Conflicting GUIDs fail the entire
batch. A zero insert count with an advancing cursor can therefore mean repeated
GUIDs, rather than an exhausted backlog. `cursor_after` in a dry run is a preview,
not a persisted position. Exit codes: 0 success, 1 sanitized access/state/ingestion
failure, 2 sanitized CLI usage error. Help prints static usage only.

State paths equal to the source (including hard links), SQLite companion-path
collisions, symlinks, repository-local
paths, nonprivate/nonregular files and unrelated databases are refused before
persistent writes. Existing state must match the current agent schema exactly;
empty files and extra schema objects are rejected. Use a fresh **nonexistent**
state filename or previously created valid agent state. Keep parent directories
private and do not swap paths or files while the command runs.

Use a dedicated test account with known fictional incoming, outgoing, group and
attachment-only records for manual direction/body checks. Aggregate output cannot
prove per-record mapping on arbitrary personal conversations. Synthetic tests
verify stable GUIDs, incoming/outgoing/system mapping, null/unsupported bodies,
ROWID gaps, ordered cursors, duplicate GUIDs, separate-process resumption, invalid
records, access failures and atomic rollback/recovery. Run the complete offline
suite without opening any personal database:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python3 -m unittest discover -s tests -v
```

Stop on any error; retain only the fixed code. Do not delete/reset state to hide
an identity or cursor failure. Do not run repair, VACUUM, checkpoints, journal-mode
changes, permission changes or outbound APIs. No reply/send worker exists, and
the outbound gate refuses the real reader as a transport.

## Bounded replay of already imported records

Use this check instead of importing more personal history solely to reach an idle
cursor. It requires existing valid state for the same source and generation.
Using the variables from the setup above, run manually:

```sh
/opt/anaconda3/bin/python3 -m imessage_agent ingest-smoke-test --database "$SMOKE_DATABASE" --state "$SMOKE_STATE" --replay --limit 25
```

`--replay` cannot be combined with `--commit`. It opens existing state read-only,
backs it up into a private temporary directory, and rewinds only that temporary
copy. It reads at most `--limit` source records starting at ROWID zero, capped by
the number of saved messages (default 10; maximum 25). Each replayed GUID must
already exist in saved state, and its ROWID must not exceed the saved cursor.
The normal durable store checks the saved chat, direction, sender, body and
timestamp for conflicts and performs GUID deduplication. Any unknown GUID,
changed generation, conflicting payload, or new insertion fails the check.
Persistent state and its cursor are never opened for writing by this mode.

Success output contains only counts and cursor positions:

- `checked`: source records replayed, including identical repeated GUIDs.
- `inserted`: must be zero.
- `stored_before` and `stored_after`: must be equal.
- `cursor_saved`: the cursor loaded from persistent state.
- `replay_cursor_before` and `replay_cursor_after`: temporary replay positions.

For example, with 70 previously imported sequential records and `--limit 25`,
expect `checked: 25`, `inserted: 0`, both stored counts 70, `cursor_saved: 70`,
and temporary replay cursors 0 and 25. The persistent cursor remains 70.
Repeating the command should give the same result if neither database changes.

This validates deduplication and payload consistency for a bounded prefix. It
does not check every stored row, prove that every source record still exists,
validate incoming/outgoing semantics against known conversations, or establish
continuous synchronization. Deleted or changed source rows can make a replay
fail; report only its fixed error code and do not reset persistent state.
The existing SQLite WAL/shared-memory and temporary-plaintext limitations below
also apply to replay. Keep state writers stopped during validation.

## Selected fictional-message classification

This is a separate owner-run local check, not authorization to inspect arbitrary
personal conversations. Prefer a dedicated macOS test account. Manually create
known fictional incoming/outgoing messages, including Unicode and attachment-only
examples, and record expected directions from what you actually sent/received.
Identify only those records' ROWIDs in a trusted local database viewer, using
known fictional context locally. Do not derive expected direction from the
`is_from_me` column: that would repeat the implementation instead of validating
it. Do not paste contents, IDs, screenshots or database paths into shared reports.
No selection discovery or personal-data browsing is performed by this command.

Save a local JSON list outside the repository, for example
`$HOME/.local/state/imessage-agent-validation/fictional-selection.json`:

```json
[
  {"sequence": 1, "direction": "inbound"},
  {"sequence": 3, "direction": "outbound"}
]
```

These numbers are examples only. Replace them with the explicitly selected
fictional records' ROWIDs and independently known expectations. The manifest
contains local identifiers and must stay private. It accepts 1–25 unique positive
ROWIDs and directions `inbound`, `outbound` or `system`; no body/address fields
are accepted. `system` is expected for intentionally ambiguous/unsupported
identities and recognized system events, not simply for an attachment-only body.

After separately authorizing this selected-record read, use the source variable
from the setup above and run:

```sh
/opt/anaconda3/bin/python3 -m imessage_agent validate-classification --database "$SMOKE_DATABASE" --selection "$HOME/.local/state/imessage-agent-validation/fictional-selection.json" --fictional-only
```

`--fictional-only` is an explicit operator attestation, not an automatic proof
that records are fictional. The command reads only the selected rows (or checks
the immediately following ROWID when a selected row is missing), writes no agent
state, and prints only `checked`, `matched`, `mismatched` or fixed error codes.
Exit 0 requires every expectation to match. A mismatch exits 1 with counts; a
missing row, unsupported schema or access failure exits 1 with a sanitized code.
CLI misuse exits 2 without echoing arguments. Stop and investigate locally on
mismatch; do not change expectations merely to make the check pass.

These checks have automated synthetic coverage. They have not been executed
against owner-selected real macOS fictional conversations during implementation.

## Compatibility and remaining limits

The reader requires ordinary ROWID tables `message`, `handle`, `chat`, and
`chat_message_join`. Required message columns are `guid`, `date`, `is_from_me`,
`handle_id`, plus at least one of `text`/`attributedBody`. Handles use `id`; joins
use `chat_id`/`message_id`; chats prefer `guid`, falling back to `chat_identifier`
when the GUID column is absent. Optional event flags are recognized when present.
Unknown schemas fail closed rather than guessing a new query.

Apple-epoch dates are converted to Unix seconds. Magnitudes of at least 10^11
are treated as nanoseconds; smaller magnitudes are seconds. This supports normal
modern Messages timestamps, but very early Apple-epoch nanosecond values are
ambiguous. The isolated upstream decoder is best effort; unsupported bodies and
attachments remain null text, without reading attachment files. Unsupported
sender formats are preserved as system records without inventing a recipient.

The URI uses `mode=ro`, plus `query_only=ON` and a write-denying authorizer.
The main database and WAL are not written or checkpointed. SQLite may coordinate
readers through `-shm` and OS locks; mode=ro is not a promise of zero shared-memory
bookkeeping. SQLite can create missing WAL sidecars for some read-only opens;
if zero sidecar changes are required, validate only an owner-prepared consistent
snapshot in a directory without write permission. Never set `immutable=1` on a
changing live database: that bypasses locking/change detection. See SQLite's
[URI modes](https://www.sqlite.org/uri.html) and
[read-only WAL documentation](https://www.sqlite.org/wal.html#read_only_databases).

File replacement and maximum-ROWID rollback are detected. In-place restores that
reuse ROWIDs, edits, unsends, late iCloud hydration and chat joins below the cursor
are not synchronized. This adapter imports new rows; continuous synchronization
requires a future reconciliation design. Do not silently reset the cursor after
a restore. Schema compatibility and synthetic test success do not establish live
macOS compatibility, personal-data retention safety, or readiness for sending.
