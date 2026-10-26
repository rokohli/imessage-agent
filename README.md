# imessage-agent

An offline, mock-only Python foundation for a future local-first macOS iMessage
agent. No personal Messages or Contacts database discovery/access, AppleScript,
network client, AI API integration, background worker, or live send implementation
is included. Importing the package does no I/O. The default adapter is synthetic;
`create_adapter("live")` raises an error. There is no enable-live setting.

The long-term goal is a local-first macOS agent with secure auto-replies, human
approval, and randomized scheduling. Those user-facing capabilities are planned,
not operational. This repository currently provides only the offline foundation.

## Current features

- Synthetic message ingestion with transactional cursors and GUID deduplication.
- Exact recipient identities, default-deny permissions, and payload-bound approval
  primitives for mock dispatches.
- Durable attempt records that prevent automatic retries of ambiguous outcomes.
- Schedule storage and cancellation, without random selection or execution.
- An isolated attributed-body decoder and standard-library offline tests.

## Installation and configuration

Clone the repository and enter its directory:

```sh
git clone https://github.com/rokohli/imessage-agent.git
cd imessage-agent
```

Run directly from source using the command below. There are no runtime
dependencies, environment variables, credentials, or configuration files to set
up. An `.env.example` is therefore unnecessary. There is no installable service
or application entry point yet.

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
resolved build tools and artifacts before installing anything. There is no CLI
or automatically started daemon in this phase.

## Architecture and file structure

```text
src/imessage_agent/
  models.py       exact recipient identities and immutable data contracts
  adapters.py     narrow read protocol, mock reader/dispatcher, mock-only factory
  attributed.py   isolated best-effort upstream text decoder
  storage.py      private SQLite state, cursors, contacts, approvals and attempts
  ingestion.py    ordered batch validation and atomic persistence
  outbound.py     default-deny outbound gate; synthetic adapter only
tests/
  test_foundation.py
LICENSE
THIRD_PARTY_NOTICES.md
```

The future process boundary is:

```text
read-only source -> durable inbox -> untrusted context -> draft proposal
                                                          |
trusted local policy/approval -> durable attempt claim -> messaging adapter
```

There is currently no model or AI tool interface. A future model should receive
only selected context and propose drafts. Never give it arbitrary Python, direct
SQLite access, contact administration, approval creation, or a raw dispatch tool.
Python module privacy is not a security sandbox: trusted code with this process's
privileges can bypass its APIs. The policy boundary needs a separate process or
equivalent capability restriction before any untrusted execution is introduced.

## Ingestion contract

`MessagesReader.read_after(sequence, limit)` returns a strictly increasing prefix
of an append-only stream. Order is source sequence, not message timestamp. Gaps
are permitted; omission of an existing earlier record is not. A future source
adapter must establish this guarantee rather than trusting the model to page.

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

The generation is an adapter-supplied stream incarnation, not a verified Apple
database identity. A live adapter must detect replacement/restoration, sequence
rollback, late materialization, and updates to existing rows. This implementation
does not track edits, unsends, read receipts, or delivery updates on existing rows.

`attributed.py` is an isolated parser helper covered by synthetic fixtures. It
is not wired to a live database. It rejects malformed/truncated content and
invalid UTF-8, and is not a complete typedstream decoder. Preserve undecodable
records for review instead of silently advancing past them in a future adapter.

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

Every mock dispatch needs BOTH an exact contact with `outbound_allowed=True`
and an unused, unexpired approval bound to the exact recipient and body. All
contacts default to denied. All replies require approval in this phase; there
is no routine-message exemption or sensitive-content classifier.

`Store.put_contact` and `Store.approve` are trusted administration primitives,
not AI tools and not a human approval UI. The tests create synthetic approvals.
A real human authentication/approval channel is still a deployment blocker.

The outbound gate rechecks policy while atomically recording the attempt and
consuming its approval. It commits an `unknown` reservation BEFORE invoking the
mock dispatcher. Reusing an attempt ID with the same payload returns its stored
result without dispatch. Reusing it for a different payload raises. A consumed
approval cannot authorize a new attempt. Denied attempts are recorded as failed
and never dispatched; a new authorization decision needs a new attempt ID.

This is not an exactly-once delivery guarantee. A crash before dispatch can
leave an attempt unknown even if nothing was sent; a crash after dispatch can
leave the same state even if a send happened. There are no automatic retries.
Unknown/partial outcomes need a future reconciliation and review path. Revocation
cannot recall an action after its dispatch claim has been committed.

## Structured send outcomes

All current outcomes are simulated, never evidence of a real delivery.

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
process replacing files. SQLite foreign keys are enabled and schema version 1 is
checked; unrelated nonempty version-0 databases are refused.

| Table | Purpose |
| --- | --- |
| messages | Exact synthetic/source records with GUID deduplication and direction. |
| cursors | Per-source generation and last atomically committed sequence. |
| contacts | Canonical addresses, display names, and default-deny permission. |
| approvals | Exact payload hash, expiration, and the consuming attempt. |
| schedules | Canonical recipient, exact text, UTC epoch window/chosen time, IANA timezone, approval, pending/cancelled state. |
| send_attempts | Idempotency ID, recipient, payload hash, approval, explicit outcome and provider identifier. |

Schedule insertion checks the contact, payload approval, timezone, and window.
It stores a preselected timestamp; random selection, scheduling workers,
execution leases, approval reservation for scheduled jobs, sleep/wake behavior,
and delivery reconciliation are intentionally deferred. A schedule does not
send anything or consume its approval. Multiple plans can reference one approval;
only a future dispatch claim could consume it, and all others must then fail.
There is no scheduler enabled merely by storing a pending row.

## Privacy and permissions

No macOS permissions are needed for this phase. No Apple databases, AppleScript,
Contacts automation, credential stores, model providers, or external services
are used. The mock never performs real sends, regardless of supplied addresses.
No logging or telemetry is configured by this package.

Agent state contains plaintext message bodies, addresses, schedule bodies, and
metadata. SQLite is not encrypted. Hashes bind approvals; they do not anonymize
short predictable messages. Future real data can leak through backups, shared
directories, crash dumps, or an external client that receives it. Define retention,
backup exclusions, audit-log redaction, and model-context minimization before
ingesting personal data. Deletion/secure erasure is not implemented.

Any future live read adapter will require separately authorized testing of macOS
TCC/Full Disk Access behavior for a dedicated host. Full Disk Access is broader
than Messages. Sending would additionally require verified Messages automation
and account behavior. Contacts access should be omitted unless explicitly needed;
canonical contacts can instead be entered in the trusted local control plane.
No existing permissions have been inspected, requested, or changed.

## Unresolved compatibility and deployment blockers

- No real macOS compatibility has been established: chat.db schema, timestamps,
  GUID stability, typedstream variants, iCloud hydration, group IDs, and source
  replacement behavior remain unverified.
- No live sending code, AppleScript, delivery correlation, group support, file
  transfer, fallback, or real transport outcome validation exists.
- No authenticated approval UI, AI integration, automatic reply policy, rate
  limits, reply-loop prevention, manual-reply suppression, or kill switch exists.
- No scheduling executor, randomized-window selection, or crash reconciliation
  worker exists. Schema models alone do not provide those capabilities.
- No retention/encryption policy, packaging installation test, daemon service,
  or deployment configuration is included.

Next work should remain fixture-driven: define the live reader's exact schema
contract and database-incarnation strategy; expand synthetic schema/restore
tests; build the trusted approval channel and durable scheduling semantics.
Only after separate authorization should test-account macOS checks begin. Keep
all real sending disabled until policy, limits, reconciliation, and the kill
switch have been validated. Do not enable autonomous replies during that work.

## Roadmap

1. Define and test the reader schema and source-incarnation contract using only
   synthetic SQLite fixtures, including restoration and late-materialized rows.
2. Build an authenticated human approval channel and durable scheduling claims,
   then implement randomized-window selection and reconciliation offline.
3. Add rate limits, loop prevention, manual-reply suppression, retention controls,
   and a kill switch before any separately authorized test-account integration.
4. Evaluate a narrowly scoped AI drafting interface and explicit context consent;
   autonomous replies and live transport require their own validation gates.

See [SECURITY.md](SECURITY.md) for security limitations and private vulnerability
reporting, and [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance.

## Attribution and reuse

See `THIRD_PARTY_NOTICES.md` for the upstream commit, retained MIT notice,
selected code, and review of the other upstream modules. The original checkout
is not a runtime dependency and is not modified by this project.

Distributed under the [MIT license](LICENSE), including the retained upstream
copyright and permission notice.
