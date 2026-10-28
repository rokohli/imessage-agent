# Security and privacy

## Current scope

The foundation has explicit-path read-only ingestion and a disabled-by-default
text-only AppleScript adapter. Phase 3A adds a local-only Ollama draft client,
with no automatic AI replies. Phase 4 adds a trusted scheduling CLI with mock
transport and read-only execution as defaults. Explicit live schedule authorization
binds recipient, content, account, recurrence, windows and grace; only the separate
`--execute-live` worker mode can dispatch these plans. The interactive supervised
test retains its UNKNOWN-history, NEW-text and one-shot confirmation requirements. The factory remains mock-only. Only trusted
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


## Local drafting boundary

Phase 3A uses only direct HTTP to `127.0.0.1:11434`, without proxies, redirects,
cloud fallback, tools, model pulls, or external API calls. Remote-model metadata
and cloud-tagged names are rejected before history is submitted. Run a trusted
Ollama daemon with `OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1`; the client cannot
prove a local daemon's implementation or listening interfaces. See the listener
checks in [the runbook](docs/local-drafting.md).

Style profiles and drafts contain filtered plaintext examples, exact recipient
keys, and source references in mode-0600 agent state. Draft commands reuse source,
sidecar, symlink, ownership and outside-checkout state-path checks. They do not
copy raw history into the inbox or alter cursors. English regex filtering drops
sensitive-looking examples and redacts context but can miss novel secrets and
non-English content. This is data minimization, not comprehensive sanitization.

Model output is untrusted. Claim review rejects credentials and malformed/meta
output, holds clear personal claims/commitments for approval, and uses a separate
schema-constrained local model check for ambiguous wording. Unknown/failed checks
require approval; they do not discard otherwise reviewable text or grant sending
rights. Natural-language acceptance does not broaden the existing narrow advisory
eligibility predicate. Both rules and the local model can misclassify claims.
Private draft JSON can now retain unverified personal claims for human correction;
these must not be treated as established facts. No model can create an approval.
Every
classification is preview-only, including `AUTO_SEND_ELIGIBLE`; no classification
creates an approval or reaches `Outbound`. There is no draft-to-send CLI. Existing
supervised send rules remain mandatory for independently authored, authorized text.

Held-out evaluation rebuilds profiles from preceding messages only and returns
aggregate heuristic scores without raw history, reference replies or identifiers.
Terminal draft content requires `--show-text`. No raw provider exceptions are
printed. Ollama or other same-user processes can log locally; protect their
configuration and keep private state out of cloud synchronization and backups.


## Opt-in human evaluation review

`review-drafts` requires `--show-text` and terminal stdin/stdout before source
access. It intentionally reveals private context, the candidate and actual
held-out text to the local owner, and stores snapshots/ratings in a dedicated
mode-0600 SQLite file outside the checkout. This is plaintext personal data;
keep it outside sync/backups and never attach it to issue reports. Terminal
controls/bidi overrides are escaped. Resume opens no Messages content or model.

The review file is distinct from core/profile/outbound state and is schema-checked
before writes. Review paths reuse source/sidecar, symlink, hardlink, ownership and
private-file checks. Human ratings cannot change contacts, approvals, attempts,
profiles, model instructions or auto-sending. Context hydration and retrieval use
only verified recipient/chat identities and precede each target; held-out replies
are disclosed to the human only after model calls and are never fed into their
own generation or claim review. Human/heuristic aggregates remain separate.


## Scheduling boundary

The [scheduling runbook](docs/scheduling.md) defines the authorization, DST,
grace, claim and crash semantics. Schedule creation/editing requires exact terminal
confirmation; no model or message can authorize it. Specifications and occurrence
windows are revalidated inside the outbound claim. Live dispatch pins the selected
account and checks account/recipient identity in the existing transport. A
cancelled plan cannot authorize a new outbound claim, but an already claimed
transport may finish. Trusted Python APIs remain trusted administration, not
capability isolation or proof of a human confirmation.

A durable occurrence reservation is UNKNOWN before any transport attempt. Crashed
reservations are never reclaimed. Repeated resolved occurrences of the same
schedule can repeat identical content; prior UNKNOWN/PARTIAL or unrelated payload
claims still suppress it. All attempts and suppression records are retained.
The CLI installs no daemon and wakes no Mac. Grace deadlines prevent stale sends
when a stopped/asleep worker resumes. Dry-run is read-only and never dispatches.

## Phase 5 local dashboard boundary

The local web control plane binds only 127.0.0.1:8000, validates the exact Host
and browser origins, and requires JSON plus a per-process session token for every
mutation. The frontend uses the local Vite proxy or same-origin production assets.
State upgrades to v5 retain existing claims/approvals and add preferences only.
Schedule CRUD delegates to Phase 4, and draft review delegates to Store.approve;
editing revokes the old unused permit. No API calls Outbound, runs a worker, retries
UNKNOWN outcomes, exposes arbitrary paths/SQL or enables autonomous replies.
Diagnostics omit raw exception text and private request values; access logging is
disabled. An explicitly configured Messages source is read-only and used for
verified regeneration/diagnostics. Private content is returned at runtime only,
not built into JavaScript. Local processes and browser users remain trusted.
See [the runbook](docs/local-dashboard.md) for the full boundary and limitations.

## Phase 6 background boundary

The background worker adds no outbound authority: it executes only matching
previously authorized Phase 4 schedules through the existing scheduler and
transport. An OS lock serializes background processes per canonical state path;
atomic occurrence/attempt claims remain the send safety boundary. UNKNOWN is
never reclaimed. SQLite/transient recovery retries polls, not reserved dispatches.
SIGTERM stops new occurrences and allows the bounded current dispatch to finish.
The dashboard exposes read-only worker health and the existing notification
preference, with no shell or start/stop endpoint. Fresh health requires both a held
lock and recent heartbeat/successful poll. User-owned bounded logs and notifications
contain fixed categories and opaque IDs, never message/recipient/account data.
Native notification permission is requested only by the explicit management
command. Install/start/uninstall are local CLI operations, never import side effects.
The per-user Aqua LaunchAgent defaults to mock, needs no sudo, and starts neither
Ollama nor dashboard. Operational macOS/TCC/notification proof remains supervised.

## Phase 3B local names and conversation state

The opt-in native Contacts helper uses `CNContactStore` with a purpose string and
explicit authorization action; it never accesses Contacts SQLite or modifies
contacts. Name searches use a matching-name predicate and return bounded choices;
queries and results are not persisted. Only an explicitly selected recipient label
is saved with a confirmed schedule, without changing canonical identifiers or
bypassing outbound allowlist/approval gates. OS authorization itself fetches no
contacts. The obsolete bulk cache is deleted when state opens; export/refresh paths
are removed. Names never enter model prompts. Enabled native notifications may
show saved recipient labels; worker logs contain fixed categories.

Session classification and timestamped context cannot authorize sends. Ending,
burst and stale-source checks only veto drafts. The existing worker can cancel
stale drafts and invalidate their unused approvals using ingested evidence or an
explicit read-only Messages source. No auto-reply loop or UNKNOWN retry is added.
Saved-case comparisons use a separate private database and local-only Ollama;
model self-judgments are not human ratings or dispatch permission. See
[Phase 3B behavior](docs/phase3b-conversations.md) for privacy and evidence limits.

## Owner style and edit evidence

Style fingerprints count only verified authored outgoing history. Approved human
edits are stored locally with original AI text, exact edited text, recipient/source
and timestamped context. Unapproved drafts, submissions, regeneration and unchanged
AI approvals never become human style evidence. Original AI text in a correction
is explicitly marked as wording to replace. Two independent source messages are
required for recurring, intent-scoped corrections; one correction is not a rule.
Owner-only state remains plaintext. Dashboard deletion removes edit-derived memory
without changing Messages, actual outgoing history, authorizations or prior reviews.

Historical generation excludes future sequence/timestamp evidence, including later
edit acceptance. The optional wording pass requires a local meaning-preservation
check and final existing claim validation. Meaning checks can err; autonomous sending
remains disabled and punctuation permission still comes only from visible owner
outgoing messages. Evaluation uses a separate private file and the same local model
for every variant; human ratings and latency are reported independently.
