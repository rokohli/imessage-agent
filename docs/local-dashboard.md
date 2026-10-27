# Phase 5: local dashboard

Phase 4 is implemented and merged in [PR #3](https://github.com/rokohli/imessage-agent/pull/3),
merge `3ee129c74fc2de58f5cbcae0a843c5bc165b01d9`. Inspection and a fresh run confirmed
296 existing synthetic tests, including 38 scheduling tests. Scheduling supports
one-time/exact/random windows, daily/weekly/selected weekdays, IANA zones and DST
policies, explicit specification authorization, edit-by-replacement, cancellation,
durable occurrence history, grace/missed handling and UNKNOWN suppression. The
foreground CLI worker exists. No launchd service, wake management or monitored
worker heartbeat exists. Previously reported live delivery is not fresh validation.
The supplied workspace snapshot exactly matched merged main; no Phase 4 edits
needed publication. Phase 5 starts from that main commit and targets main separately.

## Install and launch

Use Python 3.10+ and Node 22.12+ (Vite's requirement). No Docker is needed.
From the repository root, install the optional dashboard dependencies:

```sh
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dashboard,test]'
```

Start the backend, choosing **agent state**, not Apple's Messages database:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent dashboard \
  --state "$HOME/.local/state/imessage-agent/state.sqlite3"
```

The default `--state` is that same private path, so `PYTHONPATH=src
.venv/bin/python -m imessage_agent dashboard` also works. Keep state outside the
checkout in a private directory. Existing state must be a mode-0600 regular file
owned by you, with no symlinks or extra hardlinks. Opening state transactionally
upgrades schema v1–v4 to v5 without clearing existing records. Back up private
state before upgrades; downgrading to older application versions is not supported.

In another terminal:

```sh
cd dashboard
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. The Vite proxy forwards `/api` to the backend at
**127.0.0.1:8000**. Both ports are fixed; stop a conflicting process rather than
opening the listener to the LAN. Use the numeric loopback address, not `localhost`.
No external account, hosted database, tracking scripts or fonts are used.

For a production frontend on the same backend:

```sh
cd dashboard
npm run build
cd ..
PYTHONPATH=src .venv/bin/python -m imessage_agent dashboard
```

Open **http://127.0.0.1:8000**. The backend serves built static assets and the seven
page routes. Never place state, message exports or contact files in `dashboard/`.
The production bundle contains code only. Build output and node_modules are ignored.

## Use the dashboard

- **Overview:** actual pending occurrences, draft/approval counts, recent attempts,
  local service checks, and Schedule Message. Worker readiness is distinguished
  from process liveness; the dashboard does not monitor a worker heartbeat.
- **Schedules:** exact canonical phone/email recipient, full message, date/time,
  optional same-day random window, recurrence, selected weekdays, optional end
  date, timezone, grace, DST fold policy and mock/live transport. Review all fields
  and check the explicit authorization box before saving. The operation calls
  `make_spec` and `Scheduler.create`, with no separate schedule approval queue.
  Granting recipient permission is an explicit separate checkbox in that same
  review. Random times are persisted by the scheduler and shown in history.
  Recurring occurrences beyond the seven-day materialization horizon appear when
  the separate worker advances the horizon; the UI labels missing selections.
  Editing reauthorizes a full replacement, cancels old pending occurrences and
  keeps old history. Already claimed sends may finish after cancellation.
- **Contacts:** search known canonical addresses from agent contacts, saved
  profiles, drafts and ingested senders; view individual handles and aggregate
  style metrics. Handles are separate identities: no guessed person/group merging.
  A saved `ask_first` preference records future intent and enables no replies.
- **AI Drafts:** view saved `draft-preview` output and locally saved referenced
  context when available; edit, regenerate, submit, or discard. Empty replies must
  be edited before submission. Regeneration uses the existing verified individual
  chat resolver and `generate()` pipeline, and requires the explicit source below.
  It never infers chat membership from sender names. A stale source generation or
  concurrent human edit fails closed. Model errors preserve the current draft.
- **Approvals:** submitted drafts display exact recipient, text, reason, context
  and creation time. Explicit approval calls `Store.approve` with the existing
  payload hash, a new ID and a 24-hour expiry. Editing, regenerating, resubmitting,
  rejecting or discarding invalidates an unused previous permit. A revision check
  rejects stale tabs. Approval grants no send, worker or autonomous-reply action.
  The original `approvals` table stores grants, not pending request text; legacy
  grants cannot be reconstructed as pending requests. This queue shows submitted
  saved drafts. Authorized schedules continue to use Phase 4 authorization only.
- **Activity:** scheduled occurrences and transport attempts, filtered by exact
  handle text, local calendar date range and outcome. ACCEPTED does not prove
  delivery. UNKNOWN records have no retry action or API.
- **Settings:** local model, default timezone/grace, future notification preference,
  account selection/verification, service readiness, disabled auto replies. Defaults
  affect new plans only. Notifications are stored but not delivered in Phase 5.
  Account changes use the existing private selection file; old live plans remain
  pinned to their authorized account and fail if the selection changes.

To enable explicit read-only source diagnostics and regeneration, supply the
Messages path at launch (never through the browser):

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent dashboard \
  --state "$HOME/.local/state/imessage-agent/state.sqlite3" \
  --database "$HOME/Library/Messages/chat.db"
```

Only do this when you intend local Messages access. This project does not request
macOS permissions for you. Without the option the UI reports `not configured` and
regeneration gives an actionable error; saved drafts remain editable. Draft source
IDs/generations must match that database. Profiles and messages are never embedded
in the frontend bundle. Context remains in private runtime API responses only.

Live schedules require a previously verified account (Settings or the existing
`setup-imessage-account` CLI). The dashboard never dispatches. To execute schedules,
use the existing [Phase 4 foreground worker](scheduling.md) separately and explicitly
choose mock or live mode. No daemon or auto-start execution is installed here.

## API and local boundary

FastAPI typed models are in `dashboard_models.py`, orchestration in
`dashboard_service.py`, and routes/local request protection in `dashboard.py`.
The typed frontend client is `dashboard/src/api.ts`. Its schema types are generated:

```sh
PYTHONPATH=src .venv/bin/python scripts/dashboard_types.py
PYTHONPATH=src .venv/bin/python scripts/dashboard_types.py --check
```

The API exposes `/api/summary`, `/api/schedules` and `/{id}/occurrences`,
`/api/occurrences`, `/api/contacts`, `/api/drafts`, `/api/approvals`, `/api/attempts`,
`/api/settings`, `/api/accounts`, `/api/health` and `/api/session`. Extra input fields
and type coercions are rejected. Mutations require JSON, an approved exact origin,
and an in-memory session token from the same local service; request bodies are
never interpolated into SQL. Host validation prevents DNS rebinding. No arbitrary
SQL, filesystem browser, dispatch, retry or worker-control endpoint is exposed.
API validation errors omit private input values; access logging is disabled.
Responses are no-store with frame, referrer and MIME protections. Installed local
software and processes running as your user remain trusted; this is not an OS
sandbox or multi-user authentication system. Anyone with local browser access to
the permitted origin can administer this local control plane.

## Validation

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv/bin/python scripts/dashboard_types.py --check
cd dashboard
npm test
npm run typecheck
npm run build
```

Tests use temporary synthetic SQLite data, mocked AppleScript/account/model calls
and FastAPI's in-process ASGI client. No tests send real iMessages. Core-only users
can install the test extras when running the dashboard endpoint tests. UI tests
cover confirmation, form validation, draft editing/regeneration, approvals,
canonical contacts, UNKNOWN filtering, local API errors, routing and appearance.

The implementation environment blocked package downloads. Backend checks used
preinstalled FastAPI/Uvicorn/HTTPX in `/opt/anaconda3/bin/python`. Frontend checks
used preinstalled Node dependencies, with official React Router 6.30.1 source
compiled locally for validation; these temporary dependencies are not committed.
A clean internet-connected `npm install` remains a separate reproducibility check.
This sandbox also forbids binding local ports, so live browser rendering/screenshots
could not be captured here; component tests and the production build were checked.

## Before Phase 6

Decide background worker ownership, launchd lifecycle, sleep/wake behavior,
notification delivery, heartbeat and reconciliation UX. Exercise those separately
with explicit authorization and document physical macOS evidence. Validate the
real Messages permissions/account setup and local Ollama model on the operator's
Mac; synthetic tests do not prove live compatibility or style quality. Autonomous
replies and automatic UNKNOWN retry remain disabled. No Phase 6 executor is added.
