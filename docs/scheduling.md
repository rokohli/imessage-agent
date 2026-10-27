# Phase 4 smart scheduling MVP

Scheduling is independent of Ollama. No models, cloud services or dependencies
are required. The default transport is mock; the default worker is read-only.
No worker is installed or started automatically. AI replies remain disabled.

## Branch and publication

`feat/phase4-smart-scheduling` is based on the Phase 3A commit. Its future PR
initially targets `feat/phase3a-personalized-local-drafting`; after Phase 3A
merges, retarget it to `main` and reconcile the base without force-pushing.
GitHub publication was blocked during implementation by DNS/network failure.
Phase 3A is committed locally; its push/PR and this dependent PR are pending.
Neither PR is to be merged automatically.

## Safe local CLI walkthrough

Use an installed Python 3.10+ interpreter. On the development Mac:

```sh
cd /path/to/imessage-agent
PYTHON=/opt/anaconda3/bin/python
export PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1
SCHEDULE_DIR=$(mktemp -d /private/tmp/imessage-scheduler-demo.XXXXXX)
STATE="$SCHEDULE_DIR/state.sqlite3"
```

Keep this new demo state separate from existing outbound/profile/review databases.
All commands below use mock scheduling. At each hidden recipient prompt enter
`+12025550101` (fictional), and enter only synthetic text. Review the displayed
JSON; type `AUTHORIZE MOCK SCHEDULE` to authorize it. The far-future dates make
these examples safe to explore without an immediate due occurrence.

Exact time, or one random time inside a window:

```sh
"$PYTHON" -m imessage_agent schedule-create --state "$STATE" \
  --date 2099-10-27 --time 19:00 --timezone America/Chicago --grace 300
"$PYTHON" -m imessage_agent schedule-create --state "$STATE" \
  --date 2099-10-28 --time 08:00 --end-time 09:00 --timezone America/Chicago
```

Daily, weekly, or selected weekdays, with optional inclusive end date:

```sh
"$PYTHON" -m imessage_agent schedule-create --state "$STATE" \
  --date 2099-10-27 --time 08:00 --end-time 09:00 --timezone America/Chicago \
  --repeat daily --until 2099-11-10 --fold earlier
# Replace --repeat daily with --repeat weekly, or:
"$PYTHON" -m imessage_agent schedule-create --state "$STATE" \
  --date 2099-10-27 --time 08:00 --end-time 09:00 --timezone America/Chicago \
  --repeat weekdays --weekdays 0,2,4 --fold earlier
```

Monday is 0, Sunday is 6. Windows must end later on the same local day. Exact
sends omit `--end-time`. Randomization uses the standard library's system RNG.
Store the returned schedule ID as `ID` for the following commands:

```sh
ID=PASTE_SCHEDULE_ID
"$PYTHON" -m imessage_agent schedule-list --state "$STATE"
"$PYTHON" -m imessage_agent schedule-show --state "$STATE" --id "$ID"
"$PYTHON" -m imessage_agent schedule-show --state "$STATE" --id "$ID" --show-text
"$PYTHON" -m imessage_agent schedule-history --state "$STATE" --id "$ID"
"$PYTHON" -m imessage_agent schedule-edit --state "$STATE" --id "$ID" \
  --date 2099-10-29 --time 19:00 --timezone America/Chicago
"$PYTHON" -m imessage_agent schedule-cancel --state "$STATE" --id "$ID"
"$PYTHON" -m imessage_agent scheduler-run --state "$STATE" --dry-run
"$PYTHON" -m imessage_agent scheduler-run --state "$STATE" --execute-mock
# Optional foreground polling; Ctrl-C stops it:
"$PYTHON" -m imessage_agent scheduler-run --state "$STATE" --execute-mock --watch --interval 10
```

Edit requires the complete replacement specification plus hidden recipient/text
entry and fresh confirmation. It cancels old pending occurrences, returns a new
schedule ID and retains the original history. Reserved/attempted occurrences
are never reset, cancelled retroactively or retried. A replacement is a new
explicit authorization, including any intentionally repeated content. A live
replacement does not bypass suppression for a different/older schedule.

Default list/show/history outputs omit recipient, message and account ID.
`--show-text` and creation/authorization previews require terminal stdin/stdout;
JSON escaping prevents text from injecting terminal controls. Text and addresses
are entered at hidden prompts rather than shell arguments/history. State is
plaintext mode-0600 SQLite outside the checkout; keep it out of sync/backups.

## Execute immediately with a controllable clock, mock only

This complete check creates a new disposable state, advances a clock without
waiting, and proves a second worker tick does not dispatch again. It opens no
Messages/Contacts data and invokes no AppleScript or model.

```sh
"$PYTHON" - <<'PY'
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from imessage_agent.models import Contact, Recipient
from imessage_agent.scheduling import Scheduler, make_spec
from imessage_agent.storage import Store

with tempfile.TemporaryDirectory(prefix='scheduler-mock-') as directory:
    store = Store(Path(directory) / 'synthetic.sqlite3')
    try:
        recipient = Recipient.parse('+12025550101')
        store.put_contact(Contact(recipient, 'Fictional demo', True))
        now = [datetime(2099, 10, 27, 12, tzinfo=timezone.utc).timestamp()]
        worker = Scheduler(store, clock=lambda: now[0])
        spec = make_spec(recipient, 'synthetic scheduling demo',
                         start_date='2099-10-27', start_time='13:00', timezone_name='UTC')
        plan = worker.create(spec, authorized=True)
        now[0] = worker.history(plan)[0]['send_at']
        assert len(worker.tick(dry_run=True)) == 1
        assert worker.tick(dry_run=False)[0]['status'] == 'accepted'
        assert worker.tick(dry_run=False) == []
        assert store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0] == 1
        print('Mock scheduling passed; no real messages sent')
    finally:
        store.close()
PY
```

Run the full synthetic suite with network sockets blocked:

```sh
"$PYTHON" - <<'PY'
import unittest
from unittest.mock import patch
suite = unittest.defaultTestLoader.discover('tests')
with patch('socket.socket', side_effect=AssertionError('network forbidden')):
    result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

## Authorization and execution contract

The terminal authorizes the exact canonical individual recipient, exact text,
account ID for live plans, recurrence/first/final dates, timezone, DST policy,
random window and grace. A canonical specification hash binds those fields.
Trusted API callers use the same explicit `authorized=True` boundary as existing
Store approval administration; it is not proof of a human UI and must never be
exposed as a model tool. The CLI's exact confirmation also grants recipient
allowlisting atomically with schedule creation. Execution rechecks that opt-in.

Live creation is a separate explicit `--transport live` choice. It requires an
existing saved account selection and successful no-send account/recipient
verification before authorization. Only the explicit `scheduler-run
--execute-live` mode dispatches live plans. Each dispatch binds the authorized
account ID, refuses a changed saved selection, and revalidates account service,
connection/enabled state, exact recipient identity/handle and account ownership
in the existing AppleScript transport. Mock workers skip live plans and vice
versa. No live creation or execution was performed during implementation.

Validation: **293 offline tests pass** on Python 3.12.7 with network sockets
blocked, including 37 scheduling tests. The documented immediate mock example
also passes with sockets and subprocess creation blocked.

Each occurrence receives a unique ID and one persisted UTC execution timestamp.
The worker uses `BEGIN IMMEDIATE` to reserve a due occurrence as UNKNOWN before
calling Outbound. Outbound checks the specification, occurrence, allowed window,
transport/account and allowlist inside its own atomic approval/attempt claim.
Each occurrence receives one exact-payload approval at that boundary, bound to
its own attempt ID and deadline, with no second user prompt. An attempt is UNKNOWN
before transport and preserves existing ACCEPTED/SENT/DELIVERED/FAILED/PARTIAL/
UNKNOWN meanings. ACCEPTED is not delivery confirmation. All outcomes are
terminal for scheduling, including known failures: there is no automatic retry.

A crash between occurrence reservation and outbound claim can leave UNKNOWN
without an attempt; a crash after transport can leave UNKNOWN with a resolved
attempt visible as `transport_status` in history. Both deliberately sacrifice a
possible send to prevent duplicate execution. Never reset them to pending.
Cancellation stops pending work and is rechecked before the outbound claim;
an already claimed transport attempt may still finish. Scheduling does not
claim exactly-once delivery.

Live rate limits remain unchanged. Repeated-payload suppression has one narrow
exception: a prior resolved occurrence of the same authorized schedule may be
repeated on a different recurrence. Existing unrelated claims, UNKNOWN and
PARTIAL outcomes still block the same payload. No old claims or suppression
records are deleted or rewritten. Old Phase 1 `schedules` rows remain stored
but are not executed: their approvals did not authorize this scheduler contract.

## Time, downtime and schema

UTC timestamps are selected from IANA timezone-aware local dates/times. Local
wall times with offsets are rejected. A nonexistent first-day time is rejected;
ambiguous times require `--fold earlier` or `--fold later`. With the default
`reject` policy, future recurring gaps/folds are recorded MISSED. With an explicit
fold choice, one occurrence is generated for that local day, never both folds.
Future spring gaps are always MISSED, never shifted to a surprising time.
The timezone rules installed on the Mac determine future materialization.

For exact-time sends the deadline is selected time plus grace. For randomized
windows it is the earlier of selected time plus grace and window end. The
confirmation explicitly authorizes these limits. An expired due occurrence is
MISSED with no transport attempt. Messages/account unavailability that reaches
the transport records FAILED or UNKNOWN according to existing transport evidence,
with no retry. Sleeping/stopped/offline time requires no special daemon logic:
on the next tick, within-grace occurrences may run; expired ones are MISSED.
Clock rollback cannot make an occurrence execute twice. Severe clock errors can
still cause premature or missed execution; maintain the Mac's system clock.

Schema v4 adds `schedule_plans` (frozen authorized specification, hash, status,
materialization cursor and replacement link) and `schedule_occurrences` (unique
plan/local date, window, selected timestamp, deadline, reservation and outcome),
plus a due index. Upgrade is additive and serialized across connections. Existing
contacts, inbox, approvals, drafts, send attempts, legacy schedules and outbound
suppression records are preserved. Strict schema validators recognize v4.

Occurrences are materialized through seven local days ahead. After very long
downtime, each tick advances at most 366 calendar days per plan and processes at
most `--limit` due occurrences (default 100). Run additional ticks to catch up.
Read-only dry-run shows only already materialized due rows; it does not extend
recurrences, mark missed rows, migrate schemas, claim work or call transports.

Remaining work: real macOS/account/delivery validation under separate permission;
launchd installation/wake behavior; optional FastAPI/dashboard; manual UNKNOWN
reconciliation design. There is no launchd service, sleep prevention, automatic
restart, cloud service or AI auto-reply integration in this MVP. Keep the worker
running or arrange explicit future launchd setup for unattended operation.
