# Messaging and Phase 3A drafting architecture

Ingestion remains independent of sending:

```text
explicit read-only source -> durable messages + cursor
owner-selected fictional manifest -> read-only classification check
trusted local approval + opt-in -> atomic dispatch claim -> enabled text transport
```

Phase 3A adds a preview-only local Ollama generator, independent of this sending
path. There is no automatic send path, scheduler executor or background worker. The supervised send CLI requires interactive NEW text, fresh
approval, explicit one-shot confirmation and existing outbound UNKNOWN history. The factory still returns only the synthetic mock.
Only an explicit `AppleScriptMessagesAdapter(enabled=True)` passed to `Outbound`
can reach the macOS transport. No environment or development mode enables it.
The raw adapter and Store are trusted implementation APIs, not tools for models
or untrusted callers; this is not isolation from other same-user Python code.

## Authorization and reservation

`Outbound.submit` uses `Store.claim_send` to recheck opt-in and an unexpired,
unused approval hashing the exact canonical recipient and unchanged text. Before
transport execution, one transaction consumes that approval and records an
unknown attempt. For the live adapter it also inserts an `outbound_claims` row.

Live claims enforce fixed rolling limits: one per recipient and three globally
per 60 seconds, across connections and restarts. Reservations count even when
AppleScript later denies the target or the process crashes. Policy denials do
not consume approval or rate capacity. Future-dated reservations still count if
the wall clock goes backward; forward clock jumps can shorten the effective
window. These are local state-backed limits, not a provider-enforced quota.
Synthetic mock sends keep their original test behavior; they do not impose the
live transport rate/payload limits.

An already reserved recipient/payload cannot be submitted again under another
attempt or approval, even outside the rate window. This intentionally blocks
legitimate repetitions too. There is no retry/reset/unblock API in this MVP.
Same-attempt submissions return their saved result; changed payloads fail.
Crashes before or after dispatch leave an unknown record and consumed approval.
A failure persisting the final outcome also leaves the reservation unknown.
There is no claim of exactly-once delivery and no automatic retry.

## Reply context and loop prevention

A trusted caller proposing a reply must pass `reply_to=(source_id, guid)`.
The stored message must be inbound, have text, and have exactly the same sender
as the proposed recipient. Outbound/system/missing messages are refused. Each
source message can reserve at most one reply, including unknown outcomes.
Incoming text identical to any prior agent-reserved text is blocked as a loop.
The body hash is deliberately recipient-independent so forwarded echoes are also
blocked. Paraphrased echoes and external agents cannot be identified reliably.

Direct, independently approved messages have no reply context. Callers must not
misrepresent replies as direct sends. There is no automatic response engine to
do this implicitly. Group destinations are unrepresentable as `Recipient`;
transport targets only one exact individual participant, never a chat. A human
must decide whether replying privately to a sender from a group is appropriate.

## Transport and privacy

The adapter uses explicit Apple event codes from the installed Messages scripting dictionary's account,
participant, handle, connection status and text-send interface. It requires an explicitly selected stable account ID, exactly one ID match,
and positive iMessage/enabled/connected checks on that account alone. Setup lists
only positively identified iMessage accounts and ignores unrelated service-type
errors. It stores the operator's choice privately without touching attempt state.
The participant is requested directly by the dictionary's account-ID:handle ID
example, then its returned ID, exact handle and owning account ID are checked.
Unsupported ID formats fail closed; no filtered participant-list/name lookup is
used and no participant is created.
There is no global service-type uniqueness requirement or automatic account swap.
Literal escaping follows the [AppleScript text rules](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_classes.html).
No display-name lookup, contact-card matching, invented participant,
chat/group destination, SMS/RCS selection, attachment or file coercion exists.
Unknown and ambiguous targets fail before send. There is no fallback branch.

Payloads are escaped AppleScript string literals, sent to `/usr/bin/osascript -`
over stdin without a shell, payload argv, environment values or script files.
Only nonempty text up to 4,000 characters is supported; unsupported control
characters are refused. stdout/stderr are captured and discarded after mapping
fixed result tokens; exception details and identifiers are not persisted.

`ACCEPTED` means only that the AppleScript send command returned. It is never
`SENT` or `DELIVERED`, and has no provider ID. Timeouts (15 seconds), nonzero
exit, script errors, decode failures and unexpected output become `UNKNOWN`.
Only recognized structured pre-dispatch denials/errors become `FAILED`; their
fixed stage and sanitized code are retained. Nonzero process exits always remain
`UNKNOWN`, even when their numeric error suggests a syntax problem. All live
reservations remain consumed, including these denials. AppleScript does not
provide verified network routing, recipient registration or delivery evidence.
Actual Messages behavior must be evaluated in the separate single-send test.

## State compatibility

Schema 2 adds only `outbound_claims`: attempt ID, generated-body hash and optional
source/GUID reply context. Existing contacts, messages, cursors, approvals and
attempts are retained. Opening a v1 Store for writing upgrades it transactionally.
Schema 3 adds only `style_profiles` and `reply_drafts`, retaining the outbound
schema and all existing rows. Smoke dry runs/replay accept exact v1, v2 or v3
schemas and migrate only a temporary copy. They never upgrade persistent state unless explicitly committed.

Plaintext state, source read/WAL bookkeeping, temporary plaintext and trusted
parent-directory limitations remain. Hashes bind payloads; they are not privacy
anonymization. No encryption, secure erasure or human authentication UI exists.
See [the later outbound procedure](macos-outbound-validation.md) and
[ingestion validation](macos-validation.md).

The no-send `diagnose-applescript` CLI uses the same account/optional recipient
checks without including a send event. Recipient checks require an explicit
flag and hidden operator input. See the outbound procedure for verified local
compatibility evidence and the limits of diagnosing the original UNKNOWN.

One private `~/.local/state/imessage-agent/imessage-account.json` file stores the
selected account ID. Missing/unsafe/stale selections fail closed. Both live
transport and default no-send account/recipient validation use that selection.
The public adapter constructor and dispatch/approval interfaces are unchanged.
Historical aggregate probes are optional troubleshooting, not production policy.

`supervised.py` wraps the same Outbound gate with read-only state/fresh-body
validation and no-send account/recipient preflight before confirmation. Persistent
changes start only after confirmation. It calls submit at most once and reports
ACCEPTED/FAILED/UNKNOWN without content or IDs. The prior attempt/approval/claim
rows are never reset or reclassified. All subprocess tests remain mocked.


## Local draft generation and evaluation

`drafting.py` owns filtering, general/per-recipient profiles, prior reply-pair
retrieval and the preview output gate. `ollama.py` is a standard-library HTTP
provider with a fixed loopback endpoint, installed-local-model verification,
bounded non-streaming JSON responses, no tools, no redirects/proxies and no
fallback. `draft_cli.py` exposes explicit-path commands and content-free errors.
`evaluation.py` holds out historical outgoing replies, rebuilds retrieval and
profiles from each preceding prefix, and returns only aggregate metrics.

```text
explicit read-only Messages source + verified individual membership
    -> filtered outgoing profiles + preceding chat context + safe reply examples
    -> trusted local Ollama daemon -> untrusted JSON proposal
    -> deterministic risk checks + local ambiguous-claim review
    -> accepted / held-for-approval / rejected draft + source references
```

This graph has no outgoing transport edge. The provider gets textual context,
not paths, recipient identifiers or callable capabilities. `AUTO_SEND_ELIGIBLE`
is advisory stored metadata. A model cannot administer contacts, make approvals,
write schedules or reserve attempts. Saved profile snapshots are inspectable;
preview and evaluation rebuild prefix-specific profiles to avoid stale/future
example leakage. Bounded recent-history sampling, scan counters and heuristic privacy/content limits are in
[the local runbook](local-drafting.md).


History selection uses `MacOSMessagesReader.read_before` for read-only reverse
ROWID pagination, filtering groups in SQL without changing append-only ingestion.
`history` reserves preceding same-chat context for useful outgoing examples and
returns at most the requested selection budget in ascending order. All three
history-consuming draft commands use this sampler; preview fetches its explicit
target independently and samples only before it. Database size is not an error.
No outbound table, approval, attempt, or ingestion cursor is written by sampling.


Ollama now receives a minimal JSON schema with `reply` and
`needs_personal_facts`, not an autonomy-classification enum. `reply_proposal`
checks exact field types and rejects unknown fields with fixed diagnostics; local
policy derives the existing classifications after claim review. Credentials and
broken output are discarded; valid personal-claim text is retained for approval. No profile/storage migration or
new send path is added. Evaluation records invalid case ordinals and fixed rules,
keeps valid cases, scores failures as zero, and labels partial/all-failed reports.
Global provider/locality failures still fail closed without a fallback.


`content.py` replaces the exact-phrase content whitelist with categorized risk
rules. Ambiguous wording uses `Ollama.classify_claims` in a separate loopback chat
request, with a minimal enum schema and temperature 0. Its only input is sanitized
preceding context plus the candidate, not the hidden reference reply or style
examples. Unknown/invalid reviewer output fails closed to approval. This does
not expand the existing automatic-eligibility metadata predicate or introduce a
send path. Evaluation reports per-case content categories and separate outcome
counts without text/identifiers; only accepted content earns heuristic credit.


## Private human quality review

`review.py` owns the opt-in terminal reviewer and a separate two-table private
SQLite artifact. It reuses case selection, generation and claim validation,
freezes snapshots before asking for feedback, and commits each valid human rating.
Resume reads snapshots only; it never opens Messages content or calls a model.
Ratings are neither profile training data nor outbound approvals. Core schema v3
and its exact-schema outbound/smoke validators are unchanged.

`case_history` hydrates up to 12 real preceding messages with the reader's bounded
chat-specific query; the same helper now grounds automated and human evaluation.
`prepare_context` defines exactly the sanitized context seen by generation and
the reviewer. Reply examples combine preceding incoming turns with the owner's
response and explicit speaker order, rank keyword/question similarity and recency,
and skip duplicate wording and unsafe/stale/boundary-crossing pairs. Three or more
distinct relevant same-contact replies suppress general fallback; otherwise the
five-pair budget can include other verified individual contacts. No isolated
outgoing examples enter the improved generation prompt.
No hidden reply or human feedback enters a model request for that case. See
[the review runbook](human-review.md) for the independent score scales and privacy.

`quality.py` filters known non-authored text and the optional `Message.is_service`
source flag at the Phase 3A boundary. Raw ingestion retains direction/body and
core schema v3; no source or saved-review migration occurs. Target selection resets
on excluded artifacts. Generation deduplicates context/example payloads, omits
irrelevant pair filler and explicitly targets the latest incoming turn. A copied
safe outgoing statement can get one schema-constrained revision; all final text
still passes the existing claim validator. Quality flags remain content-free and
remaining copies require approval. Punctuation metrics and the prompt favor the
owner's light-punctuation style without modifying raw source text.

`comparison.py` reads an existing 20-case review read-only, verifies exact source
references, freezes per-case preceding inputs, and writes a new private review
artifact. `baseline.py` retains the prior punctuation-fixed prompt/retrieval for
variant A; B uses improved retrieval/prompting, C uses the same improved pipeline
with locally installed 7B. There is no model pull or fallback. Shuffled trial order
is persisted before rating; labels, classification and timing are hidden until
all available trials are rated. Failed cases remain visible in coverage. Both
variants use the unchanged punctuation and claim checks. Only improved outcomes
distinguish `NEEDS_USER_INPUT`; none can authorize a send.
