# imessage-agent

A local-first Python messaging foundation with read-only macOS ingestion and a
controlled text-only AppleScript transport. Sending defaults to disabled in every
environment; the factory remains synthetic. Phase 3A includes local Ollama drafting
and human evaluation. AI replies remain disabled. Phase 4 adds explicitly authorized scheduling,
with mock transport and a read-only worker as defaults.
Importing performs no I/O. Real sending requires
separate authorization and explicit trusted in-process enablement.

The long-term goal is a local-first macOS agent with secure auto-replies, human
approval, and randomized scheduling. Draft generation is preview-only; human ratings do not authorize sending.
The scheduling MVP is available through explicit CLI commands; see
[the scheduling runbook](docs/scheduling.md) for safe mock testing and the scoped
live-test procedure. Phase 4 MVP validation: 296 offline tests pass; the owner
reported one successful scheduled live delivery from macOS Terminal. Sleep/wake,
live recurrence and launchd remain unvalidated.

Phase 3A status: personalized general/contact profiles, conversation-aware retrieval,
claim review, punctuation policy, human review and fixed-case blind comparison are
implemented. Default: `qwen2.5:3b`, loopback Ollama, temperature 0.3, 160 output
tokens. **256 offline tests pass** (2026-10-27). The owner-reported 20-case baseline
had style 2.0/5, relevance 2.1/5, naturalness 1.9/5 and 0/20 observed inventions;
post-improvement quality is unmeasured. See the [evaluation report](docs/phase3a-evaluation-report.md).
Autonomous replies remain disabled.

## Current features

- Synthetic and read-only Messages ingestion with transactional cursors and GUID deduplication.
- Schema-only, content-free CLI diagnostics for an explicitly supplied database.
- Seconds/nanoseconds timestamps, schema variants, and best-effort attributed text.
- Exact recipient identities, default-deny permissions, and payload-bound approval
  primitives for controlled dispatches.
- Durable attempt records that prevent automatic retries of ambiguous outcomes.
- Authorized one-time/randomized/recurring scheduling, cancellation, durable claims,
  missed-window handling and execution history; no automatically installed worker.
- Durable live rate limits, reply/echo-loop checks and default-disabled individual iMessage transport.
- An isolated attributed-body decoder and standard-library offline tests.

## Installation and configuration

Clone the repository and enter its directory:

```sh
git clone https://github.com/rokohli/imessage-agent.git
cd imessage-agent
```

Run directly from source using the command below. There are no runtime
dependencies, provider credentials, or environment-based live settings. Outbound
use requires the one-time local account selection described below; ingestion and
offline tests do not. There is no installable service or background application.

## Run safe tests without installation

Python 3.10 or later is required. From this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Use an already installed compatible Python interpreter. Tests create synthetic
SQLite files only inside temporary directories and remove them afterward. They
do not import the original project or require any third-party runtime packages.
No dependency installation is needed. Packaging has not been tested; a future
package build would require the declared `setuptools>=68` backend. Report the
resolved build tools and artifacts before installing anything. There is no
automatically started daemon. Use a Python 3.10+ executable; the system `python3`
on some Macs is older. No dependencies were added or installed for the reader.

## Read-only diagnostic

Only use an explicitly authorized database path. This checks schema metadata
without displaying message contents or reading message rows:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m imessage_agent diagnose --database /authorized/path/chat.db
```

See [the separate manual procedure](docs/macos-validation.md) for permissions,
the responsible executable, content-ingestion authorization and compatibility
limits. No personal Messages access is part of the offline test suite.

## Architecture and file structure

```text
src/imessage_agent/
  models.py       exact recipient identities and immutable data contracts
  adapters.py     narrow read protocol, mock reader/dispatcher, mock-only factory
  macos.py        explicit-path read-only Messages reader and schema checks
  __main__.py     content-free diagnostics, ingestion and classification commands
  smoke.py        bounded ingestion and temporary-state replay
  validation.py   owner-selected fictional direction checks
  applescript.py  disabled-by-default individual iMessage text transport
  account_selection.py  private saved account ID, separate from outbound state
  attributed.py   isolated best-effort upstream text decoder
  storage.py      private SQLite state, cursors, contacts, approvals and attempts
  ingestion.py    ordered batch validation and atomic persistence
  outbound.py     approval gate, optional reply context and explicit live transport
tests/
  test_foundation.py
  test_macos.py    generated synthetic SQLite fixtures, including WAL
LICENSE
THIRD_PARTY_NOTICES.md
```

The control flow is (no untrusted execution is implemented):

```text
read-only source -> durable inbox -> untrusted context -> draft proposal
                                                          |
trusted local policy/approval -> durable attempt claim -> messaging adapter
```

Phase 3A adds a local-only Ollama draft generator (default `qwen2.5:3b`). It gets
filtered style examples and preceding context, with no tools or dispatch access.
See [local drafting and evaluation commands](docs/local-drafting.md) and the
[evaluation report](docs/phase3a-evaluation-report.md). Draft classifications never
grant an approval or enable auto-sending. Never give a model arbitrary Python,
direct SQLite access, contact administration, approval creation, or raw dispatch.
Python module privacy is not a security sandbox: trusted code with this process's
privileges can bypass its APIs. The policy boundary needs a separate process or
equivalent capability restriction before any untrusted execution is introduced.

## Ingestion contract

`MessagesReader.read_after(sequence, limit)` returns a strictly increasing prefix
of an append-only stream. Order is source sequence, not message timestamp. Gaps
are permitted; omission of an existing earlier record is not. The Messages adapter
uses message ROWID, with one message per row regardless of duplicate chat joins.
It never pages by timestamps.

Messages carry GUID, sequence, chat identity, direction, exact sender, optional
body, and timestamp. `(source_id, guid)` is the durable deduplication key.
Sequences are cursor positions, not message identities. Empty bodies, outbound
echoes, and system messages are stored; ingestion does not generate any reply.

Each batch inserts messages and advances the source cursor in one transaction.
An insertion or cursor-update failure rolls the whole batch back. Repeated GUIDs
with identical payloads are skipped while the cursor advances; conflicting GUID
payloads fail closed for reconciliation. A stale competing ingestion transaction
must fetch again. Cursors survive process restarts. A changed source generation
requires explicit reconciliation, never silent reset or replay.

For the Messages adapter, source identity hashes the explicit resolved path;
generation uses filesystem device, inode and birthtime where available. File
replacement fails closed, including across restarts; a maximum ROWID below the
durable cursor also fails closed. This is an append-only import, not a complete
Messages synchronizer. In-place restores that reuse ROWIDs, edits, unsends, late
iCloud hydration and late chat joins below the cursor are not detected or
re-imported. Reconciliation of these cases remains a blocker for dependable
continuous synchronization; never reset a cursor automatically.

`attributed.py` is an isolated parser helper covered by synthetic fixtures. It
is reused by the read-only adapter when plain text is absent. It rejects
malformed/truncated content and invalid UTF-8, and is not a complete typedstream
decoder. Undecodable and attachment-only rows are persisted with a null body; no
attachment paths or contents are accessed. Unknown senders, missing/ambiguous
chats, and recognized system events become system records. Invalid required GUIDs
or timestamps stop the batch without advancing the cursor. Outgoing rows remain
outbound, including unknown-chat rows. Ingestion has no automatic-reply path.

## Recipient and outbound policy

Only individual addresses are representable:

- Phones require explicit `+` and international digits; no default region,
  fuzzy name, contact selector, or national-number inference. This is syntax
  validation, not proof of a real or reachable phone number.
- Email domains are IDNA-encoded and lowercased. Local-part case is preserved
  conservatively; merging case variants or phone/email aliases needs an explicit
  trusted decision. Only unquoted ASCII local parts are supported. Display names
  are never authorization identities.
- Groups and attachments have no outbound API in Phase 1.

Every dispatch needs BOTH an exact contact with `outbound_allowed=True`
and an unused, unexpired approval bound to the exact recipient and body. All
contacts default to denied. All replies require approval in this phase; there
is no routine-message exemption or sensitive-content classifier.

`Store.put_contact` and `Store.approve` are trusted administration primitives,
not AI tools and not a human approval UI. The tests create synthetic approvals.
A real human authentication/approval channel is still a deployment blocker.

The outbound gate rechecks policy while atomically recording the attempt and
consuming its approval. It commits an `unknown` reservation BEFORE invoking the
selected dispatcher. Reusing an attempt ID with the same payload returns its stored
result without dispatch. Reusing it for a different payload raises. A consumed
approval cannot authorize a new attempt. Denied attempts are recorded as failed
and never dispatched; a new authorization decision needs a new attempt ID.

This is not an exactly-once delivery guarantee. A crash before dispatch can
leave an attempt unknown even if nothing was sent; a crash after dispatch can
leave the same state even if a send happened. There are no automatic retries.
Unknown/partial outcomes need a future reconciliation and review path. Revocation
cannot recall an action after its dispatch claim has been committed.

## Structured send outcomes

Offline transport outcomes are mocked. A later live AppleScript return can establish
transport acceptance only; the implementation never claims sent or delivered.

| Status | Meaning for a future validated transport |
| --- | --- |
| accepted | Transport accepted the request; no send or delivery proof yet. |
| sent | Transport has positively identified the matching outbound record as sent. |
| delivered | Transport positively confirmed delivery for that record. |
| failed | Denied before dispatch or positively known failed. |
| partial | Only part of a future compound operation is positively known successful. |
| unknown | Insufficient evidence, crash gap, exception, or unstructured result. |

Strings such as "sent successfully" never become success statuses. An adapter
exception becomes unknown and its arbitrary exception text is not persisted.
The mock defaults to unknown. No SMS fallback or transport retry exists.

## SQLite models

`Store` opens only the explicit agent-state path supplied by its caller. Use a
dedicated private directory. New directories use mode 0700 and the database is
created with mode 0600; permissive existing DB files and file symlinks are refused.
Parent paths must be trusted; this does not defend against a malicious same-user
process replacing files. SQLite foreign keys are enabled and schema version 2 is
checked (v1 state upgrades transactionally); unrelated nonempty version-0 databases are refused.

| Table | Purpose |
| --- | --- |
| messages | Exact synthetic/source records with GUID deduplication and direction. |
| cursors | Per-source generation and last atomically committed sequence. |
| contacts | Canonical addresses, display names, and default-deny permission. |
| approvals | Exact payload hash, expiration, and the consuming attempt. |
| schedules | Canonical recipient, exact text, UTC epoch window/chosen time, IANA timezone, approval, pending/cancelled state. |
| send_attempts | Idempotency ID, recipient, payload hash, approval, explicit outcome and provider identifier. |
| outbound_claims | Durable live rate/payload reservations, generated-body hash and optional unique reply context. |
| schedule_plans | Exact authorized specification, binding hash, status and recurrence cursor. |
| schedule_occurrences | Unique plan/local day, selected UTC time, deadline, reservation and outcome. |

The legacy `schedules` API stores reviewed plans but does not execute them or
consume their approvals. Phase 4 uses separate `schedule_plans` and
`schedule_occurrences` tables: old plans lack the scheduling-specific authorization
and are never executed automatically. See [the scheduling runbook](docs/scheduling.md)
for exact/randomized/recurring creation, authorization, cancellation and worker
execution. No scheduler starts merely by storing a pending row.

## Privacy and permissions

Offline tests need no macOS privacy permissions. The reader accepts only an
explicit path; imports and CLI help do not discover or open Apple databases. No
logging or telemetry is configured. Live sending remains disabled.

Read-only access to protected Messages data is subject to macOS file permissions
and TCC. See [the separate manual validation procedure](docs/macos-validation.md)
for the responsible executable/host, permission requirements, diagnostics, and
limits. This tool never requests broader filesystem access or changes permissions.
No personal Messages database has been accessed during offline validation.

Agent state contains plaintext message bodies, addresses, schedule bodies, and
metadata. SQLite is not encrypted. Hashes bind approvals; they do not anonymize
short predictable messages. Future real data can leak through backups, shared
directories, crash dumps, or an external client that receives it. Define retention,
backup exclusions, audit-log redaction, and model-context minimization before
ingesting personal data. Deletion/secure erasure is not implemented.

No existing macOS permissions have been inspected, requested, or changed.

## Select and validate an iMessage account without sending

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python3 -m imessage_agent setup-imessage-account
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python3 -m imessage_agent diagnose-applescript
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python3 -m imessage_agent diagnose-applescript --check-recipient
```

Setup lists positively identified iMessage accounts with numeric labels. Select
one; its stable ID is saved in a private local file, without enabling sending.
Default diagnostics and transport verify only this saved account's exact ID,
iMessage service, enabled/connected status and exact recipient ownership.
Unrelated unreadable service types do not block the operator-selected account.
The recipient command requires explicit authorization and hidden operator input.
All three commands contain no send event. Existing attempts/approvals are untouched.

See [setup, errors and a later supervised test](docs/macos-outbound-validation.md).
The prior UNKNOWN attempt must never be reset or retried. A future test requires
NEW text and fresh authorization, using the same existing outbound state.

## Supervised single-message test

After successful no-send validation, an operator can run `send-supervised-test
--state /existing/outbound-state.sqlite3` manually. It requires new hidden text,
fresh exact-payload approval and the phrase `SEND ONE NEW IMESSAGE`. It uses the
saved account and permits at most one dispatch, with no retries. The previous
UNKNOWN history is retained and its text is refused. See the
[exact command and verification steps](docs/macos-outbound-validation.md#one-supervised-live-test-operator-execution-only).
Do not execute the live command during development or in an automation loop.

## Controlled outbound and remaining blockers

`AppleScriptMessagesAdapter()` is disabled. Only explicit `enabled=True` and the
trusted `Outbound` approval gate permit a later send. Environment variables never
enable it. Text is sent to one exact participant on the explicitly selected, connected iMessage
account; there is no group destination, name matching, attachment API or fallback.
Live reservations enforce one send per recipient and three globally per rolling
60 seconds. Repeated recipient/payloads and repeated reply contexts are blocked
across restarts. Unknown outcomes are never automatically retried.

For a reply, trusted callers must supply `reply_to=(source_id, guid)`; the gate
rejects outgoing/system records, sender mismatches and known generated-text echoes.
Direct messages still require their own explicit recipient opt-in and exact-text
approval. There is no automatic reply engine. The synthetic default mock retains
its previous simulation behavior and does not impose live transport limits.

See [architecture and safety contracts](docs/architecture.md) and the
[separately authorized single-message procedure](docs/macos-outbound-validation.md).
That procedure has not been run. Automation permissions, AppleScript runtime,
routing, unknown recipients and actual delivery still need live validation.
No authenticated approval UI, process isolation, delivery reconciliation,
continuous Messages synchronization, retention/encryption policy, scheduler,
AI integration or production deployment is provided.

Phase 2A.1 was owner-validated with schema compatibility, 70 committed records,
persistent cursor recovery and a 25-record replay with zero duplicates. Those
results do not prove every classification. The
[fictional-message classification workflow](docs/macos-validation.md#selected-fictional-message-classification)
checks explicit local expectations without printing contents or identifiers.

See [SECURITY.md](SECURITY.md) for security limitations and private vulnerability
reporting, and [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance.

## Attribution and reuse

See `THIRD_PARTY_NOTICES.md` for the upstream commit, retained MIT notice,
selected code, and review of the other upstream modules. The original checkout
is not a runtime dependency and is not modified by this project.

Distributed under the [MIT license](LICENSE), including the retained upstream
copyright and permission notice.


## Phase 3A local drafting

`model-health`, `build-profiles`, `draft-preview`, `evaluate-drafts`, `compare-drafts`,
and the opt-in `review-drafts` command implement
private local style imitation and held-out evaluation. The read-only adapter must
verify individual chat membership; filtered outgoing messages form general and
per-recipient style profiles. Recent context and representative prior replies
stay on the same machine. Ollama is required at `127.0.0.1:11434`; remote/cloud
models, proxies, redirects, and provider fallbacks are not used. No new dependency
is required.

Drafts and source references are saved in private agent state. No draft sends,
creates approvals, or changes recipient permissions. Claim-based content review
accepts natural reactions and clarification, retains unverified personal claims
for approval, and discards credentials or broken output. Ambiguous wording gets
a separate local model review. Missing personal facts or decisions are marked
`NEEDS_USER_INPUT`; sending still requires the existing approval flow. Sensitive filtering is heuristic,
not a guarantee that all private content will be recognized.

See [the runbook](docs/local-drafting.md) for exact setup, health check, profile,
preview, comparison and offline-test commands. Personal history and model quality
were not validated during implementation; the report distinguishes mocked checks
from measured local-model performance.

`compare-drafts` freezes the exact 20 cases from an existing review, generates the
punctuation-fixed 3B baseline and improved 3B pipeline, and adds improved 7B only
if already installed locally. A separate private file shuffles trials for blind
rating; completed reviews report human quality, invention labels, heuristics and
latency separately. See [the fixed-case comparison commands](docs/human-review.md#blind-comparison-on-the-same-20-saved-cases).


[Human quality review](docs/human-review.md) shows private held-out comparisons
and saves style/relevance/naturalness ratings plus invention labels in a separate
local review file. It reports human quality independently of heuristic scores and
content-acceptance rates. Resume uses saved snapshots without regenerating drafts.
Ratings never grant sending approval or turn on autonomous replies.

## Phase 5 local dashboard

A React/TypeScript desktop dashboard and loopback FastAPI API now manage local
schedules, contacts, saved drafts, approvals, activity and settings. Schedule
confirmation reuses Phase 4 authorization; draft approval remains payload-bound.
The dashboard starts no worker and sends no messages. Auto replies remain disabled.

See [the dashboard runbook](docs/local-dashboard.md) for installation, state
configuration, source access opt-in, tests and remaining Phase 6 work.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dashboard,test]'
PYTHONPATH=src .venv/bin/python -m imessage_agent dashboard
# In another terminal:
cd dashboard && npm install && npm run dev
# Open http://127.0.0.1:5173
```

## Phase 6 background agent

An explicit per-user LaunchAgent now runs the existing scheduler independently of
Terminal and the dashboard. It defaults to mock schedules, preserves durable
UNKNOWN suppression and grace windows, and adds private native notifications,
rotating structured logs and fresh worker health in the dashboard.
See [background installation, management and supervised validation](docs/background-agent.md).
Nothing installs or activates automatically; live-message validation requires your
explicit confirmation. The Mac must be awake with an appropriate Messages session.

Phase 3B adds on-demand recipient name lookup and timestamped, session-aware
reply context with ending/burst/staleness suppression. Auto replies remain off.
See [Contacts and conversation behavior](docs/phase3b-conversations.md) for setup,
limitations, worker freshness checks and same-case local evaluation.

[Authentic texting style](docs/authentic-texting-style.md) adds observed wording
fingerprints, situation-matched owner examples, approved-edit learning and an
experimental wording-only second pass. Single pass is the default; no AI replies
are automatically sent. `compare-styles` prepares a same-model blind A/B/C study.
