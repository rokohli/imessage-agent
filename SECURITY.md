# Security and privacy

## Current scope

The foundation has explicit-path read-only ingestion and a disabled-by-default
text-only AppleScript adapter. There is no AI client, automatic reply engine,
background worker. The only sending CLI is the interactive supervised test,
which requires existing UNKNOWN history, NEW text and explicit one-shot confirmation. The factory remains mock-only. Only trusted
local code may explicitly enable the real adapter through `Outbound`; neither
environment variables nor development mode enable it. No real sends or permission
changes were executed during implementation. Offline subprocesses are mocked.

## Trust model

Incoming content is untrusted. Store administration, exact-payload approval and
recipient opt-in must stay in a trusted local control plane, never AI tools.
Atomic claims consume approval and persist unknown reservations before dispatch.
Live claims also enforce durable per-recipient/global limits, repeated-payload
suppression and optional reply-context/echo-loop checks. Replies must provide
stored inbound context. There are no automatic retries for any claimed outcome.

There is no authenticated approval UI or same-user process isolation. Raw adapter
and Store methods are implementation APIs; trusted Python can bypass them or
invoke the OS itself. Do not expose these APIs to untrusted code or a model.
Process/capability isolation is required before introducing untrusted execution.
See [architecture](docs/architecture.md) for exact limits, conservative duplicate
suppression and clock/reply-context limitations, and the
[later single-message procedure](docs/macos-outbound-validation.md) for Automation
permission and delivery evidence requirements. No SMS/group/attachment path is
provided. Transport acceptance is never delivery confirmation.

The operator explicitly selects a positively verified iMessage account using
`setup-imessage-account`. Only that saved stable ID is checked for iMessage,
enabled/connected status and exact recipient ownership before dispatch. Unrelated
service-type errors are not account-selection evidence. The one private selection
file is separate from approval/attempt state, never overwritten by setup, and
never enables sending. Missing, stale or unsafe selections fail closed.

## Data handling

The store opens an explicit agent-state path, never discovers Apple databases,
and creates new database files with mode 0600. It rejects file symlinks and
permissive database files. Use a trusted, private parent directory; this does not
defend against another process with the same user's privileges.

SQLite state is plaintext and can contain message bodies, recipient addresses,
contacts, approvals, and schedules. Payload hashes are not anonymization.
Encryption, retention, secure deletion, backup exclusions, and log redaction are
not implemented. Personal-data ingestion requires explicit authorization and an agreed private
state location; offline validation never opens personal databases. The package
configures no telemetry, but a caller can still expose data through its own logs
or backups.

Tests use synthetic messages, fictional phone fixtures, and example-domain email
addresses. Keep real conversations, contacts, credentials, databases, local
configuration, and crash dumps out of commits and issue reports. `.gitignore`
reduces accidental inclusion but is not a security boundary; inspect staged files
before every commit. The attributed-body decoder is best effort and has only
synthetic fixture coverage, not real macOS compatibility proof.

## Reporting a vulnerability

Do not post credentials, personal conversations, or exploit details in a public
issue. If GitHub's private vulnerability reporting is available, use the
repository's **Security → Advisories → Report a vulnerability** control. Otherwise,
open a public issue containing only a request for a private reporting channel,
without sensitive details, and wait for a maintainer to arrange one.

Include the affected commit/version, a minimal synthetic reproduction, expected
and observed behavior, and impact in the private report. This early project has
no guaranteed response time or supported production deployment; fixes target the
current development version.

## Read-only adapter boundary

`MacOSMessagesReader` opens only an explicit path using SQLite URI `mode=ro`,
sets `PRAGMA query_only=ON`, disables trusted schema behavior, and denies writes,
ATTACH, and configuration changes with a SQLite authorizer. It never performs
checkpointing, journal changes, recovery writes, or attachment/Contacts access.
Normal SQLite WAL reading uses shared-memory coordination; this is not a promise
that OS lock or shared-memory bookkeeping is byte-for-byte unchanged.

Diagnostics read schema metadata only and print fixed error codes, compatibility,
permission guidance and the Python executable, never message rows, addresses,
chat identifiers, database paths or SQLite exception text. The reader does not
log. Outbound records cannot trigger replies because ingestion has no reply
worker, and the real reader cannot be passed as an outbound transport.

File replacement and sequence rollback fail closed. In-place restores with reused
ROWIDs and changes below the cursor require future reconciliation. Read-only
manual validation and exact permission/executable guidance are documented in
[docs/macos-validation.md](docs/macos-validation.md). No broad access is requested
by the package; do not grant it to an AI agent as part of validation.
