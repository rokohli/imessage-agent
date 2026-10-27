# Phase 3B: local names, sessions and reply timing

Auto replies remain disabled. Classification and conversation state are advisory;
neither changes recipient allowlists, account selection, outbound limits, exact
payload approvals, or UNKNOWN reservations. There is no new scheduler, transport,
AI follow-up loop, cloud dependency, or Messages database writer.

## Recipient name lookup

Type a saved name in **Schedule a message → Recipient**, click **Find contact**,
and choose a specific number or email. Search results always show both the name
and handle; duplicate names and multiple handles require an explicit choice.
The review screen shows the chosen name and exact canonical destination before
schedule authorization. Entering a number or email directly still works.

This is an on-demand lookup, not an address-book import. It uses Apple's
`CNContact.predicateForContacts(matchingName:)` to fetch only name matches, capped
at ten contacts and forty handle choices. Only names, phone numbers and emails
are requested. Searches require 2–100 characters; empty searches are rejected.
No search results or queries are saved in agent state, style profiles or logs.
The browser holds results only while choosing a recipient. The chosen name/handle
are saved only when the operator confirms a valid schedule, alongside the
recipient data the scheduler already needs. Choosing a match does not authorize
outbound messages. Existing allowlist and exact-payload/account gates still apply.

The native helper never exports the whole address book, opens Contacts SQLite,
modifies contacts or sends contact data to a cloud service/model. Names displayed
elsewhere in the dashboard and enabled notifications come from saved recipient
labels; background workers do not query Contacts or refresh an address-book cache.
When a saved name changes, search and select the recipient again to update it.

Install the helper once, without starting a worker or requesting permission:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts install
```

If an older helper is installed, explicitly rebuild it with:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts install --replace
```

The new app is compiled and signed before replacing the old one; a failed build
leaves the previous helper in place. Xcode command line tools are required.
The app remains under `~/Library/Application Support/iMessage Agent/Contacts.app`.
No installation, OS authorization or real Contacts search is performed by tests.

Use **Settings → macOS Contacts → Request Contacts access**, or:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts authorize --state "$STATE"
```

Authorization requests OS read permission but fetches no contacts. Only clicking
Find contact performs a name query. Permission denial or a missing helper leaves
direct number/email entry available. Revoke OS permission in macOS Privacy &
Security > Contacts. Install/start/replacement and permission changes remain
explicit operator actions; restart the dashboard to load updated code.

International `+` and `00` formats normalize spacing and punctuation. National
numbers match only with an explicit country calling code, set in Settings when
requesting access or via `contacts authorize --state "$STATE" --country-code 1`.
Trunk prefixes and extensions are not inferred. Email domain case normalizes;
local-part case stays exact. No suffix matching or contact-name send selector is
introduced; all transports use the canonical handle the operator chose.

Opening agent state with this version deletes the superseded `contacts:cache`
preference without reading its contents. The bulk export/refresh API and CLI are
removed. This cleanup preserves recipient authorizations, approvals, schedules,
attempts and UNKNOWN reservations. The only Contacts preference retained is the
optional country calling code. The selected recipients and existing agent state
remain private plaintext data; they are not an imported address book.

The native bridge uses private temporary files for a query and its matching
results; those are removed when the request finishes. Contact names do not appear
in process arguments or worker logs. Existing installed Notifier apps still need
an explicit rebuild through the LaunchAgent runbook to support display labels.

Supported API: [name-matching predicate](https://developer.apple.com/documentation/contacts/cncontact/predicateforcontacts%28matchingname%3A%29).

## Conversation context

`segment()` considers only the verified individual chat and messages preceding
the target in both ROWID and timestamp. It returns current/previous sessions,
likely topic boundaries, whether the target starts a session, explicit historical
references and an advisory state. Consecutive database records are never assumed
to be one exchange. Speaker identity is retained and owner turns help distinguish
terminal acknowledgments from ambiguous standalone acknowledgments.

Defaults are a four-hour gap, a thirty-minute gap across calendar days, and a
fifteen-second incoming burst interval. Shared topic words can bridge the soft
gap; silence of six times the configured gap starts a new session even when words
overlap. Midnight alone does not end an active exchange. Explicit references
such as “remember”, “yesterday”, “last week”, or “following up” can retrieve older
context without making old plans current. Topic markers and a small English topic
vocabulary recognize transitions such as homework to dinner. Unrecognized semantic
changes remain a heuristic limitation; no extra model/cloud classifier is required.

Dashboard Settings exposes the thresholds, which previews and regeneration reuse.
Evaluation uses frozen default thresholds for reproducibility. UTC epoch timestamps
remain unchanged; ISO timestamps carry the configured IANA zone's UTC offset,
including DST changes. Messages stores absolute instants, not the sender's original
timezone; the configured display timezone must not be mistaken for sender metadata.

Context hydration reads up to 1,000 exact preceding same-chat rows independently of
the sparse style sample. Current-session messages come first logically, with a
100-message / 12,000-character prompt budget; truncation makes the state UNCERTAIN.
Up to four older same-chat messages are retrieved by topical word overlap or an
explicit reference and labeled HISTORICAL CONTEXT. Missing history, paraphrases,
sensitive-body redaction and more than 1,000 preceding rows can limit recall.
Historical style pairs remain separate from factual conversation context.

The Ollama payload has CURRENT_SESSION, HISTORICAL_CONTEXT and STYLE_EXAMPLES.
It retains the Phase 3A punctuation policy: no punctuation by default, with each
mark requiring at least three owner-authored occurrences and two-thirds support
in visible current-session outgoing messages. Historical/contact examples cannot
authorize punctuation or establish current activities, plans or availability.
Unknown facts still require user input and independent claim validation remains.

## Reply states and cancellation

- ACTIVE_CONVERSATION: an ongoing exchange; ordinary content/approval gates apply.
- NEW_SESSION: a new observed session; never inherits old plans automatically.
- TOPIC_SWITCH: an explicit or likely subject change inside the session.
- LIKELY_ENDED: a short ending acknowledgment after an owner turn; no model call
  and no generated reply. This avoids unnecessary acknowledgment ping-pong.
- UNCERTAIN: ambiguous standalone acknowledgment, missing text, inconsistent
  timestamps or truncated current context; never advisory auto-send eligible.

Live previews check the latest source row before and after generation. Incoming
bursts are suppressed with `incoming_burst_settling`; request a fresh preview once
they settle. A newer incoming/system row makes the draft stale; a newer owner row
cancels it as a manual reply. This is postponement, not an automatic retry queue.
Historical evaluation deliberately omits these live freshness vetoes and excludes
future/held-out rows from every model-generation and claim-validation prompt.

Submission, approval and regeneration check freshness again when the dashboard
has an explicit `--database`. New generated drafts cannot be approved without a
configured source. The existing Phase 6 worker cancels stale drafts and expires
unused draft approvals based on ingested evidence. For live source monitoring,
start/install that same worker with its optional `--database "$MESSAGES_DB"`;
it performs read-only freshness checks, without a second ingestion/sending loop.
An existing worker without that option only sees changes already ingested into
agent state. Existing authorized schedules remain governed by the original
scheduler, not conversation classification. There is no atomic lock shared with
Messages; these vetoes do not establish safe autonomous dispatch.

Notification name resolution and native presentation run on the existing worker's
presentation executor, outside schedule polling. There is no automatic retry of
UNKNOWN or of an AI reply, and no repeated AI follow-up generation.

## Same-case evaluation

Run both approaches on the exact twenty saved review cases, using the same local
Qwen2.5 3B model/configuration and preserving the punctuation rule:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent compare-sessions \
  --database "$MESSAGES_DB" --source-review "$SOURCE_REVIEW" \
  --comparison "$NEW_PRIVATE_COMPARISON" --max-messages 10000
PYTHONPATH=src .venv/bin/python -m imessage_agent review-drafts \
  --database "$MESSAGES_DB" --ratings "$NEW_PRIVATE_COMPARISON" \
  --resume --show-text
```

A freezes the Phase 3A prompt and last-twelve-row factual context; B uses the new
session context, prompt and ending suppression. Both use the same style-example
implementation, so this compares the context/prompt change, not a model upgrade.
The comparison is a new private mode-0600 SQLite file; source reviews, live state,
profiles and outbound authorizations are not changed. Model locality is checked
before any private content is submitted. Resume requires no model or Messages read.

New session comparisons save one shared, timestamped same-chat evidence window
for human reviewers, independently of the variants' different model contexts.
Older saved comparison files retain their original evidence; prepare a new file
before using session/topic correctness ratings from those older snapshots.

Human review records relevance, naturalness and invented facts separately from
style, plus nullable session-boundary correctness, topic-switch correctness and
unnecessary replies. A null label means insufficient evidence, not a success.
The optional local scorer provides uncalibrated judgments without writing human
ratings or authorizing anything:

```sh
PYTHONPATH=src .venv/bin/python scripts/score_session_comparison.py \
  --database "$MESSAGES_DB" --source-review "$SOURCE_REVIEW" \
  --comparison "$NEW_PRIVATE_COMPARISON"
```

Only aggregate scores reach stdout. The same local model generates and judges,
so these are weak regression signals, not validated accuracy or human-quality proof.
See [evaluation results](phase3b-evaluation-report.md). No real messages are sent.
