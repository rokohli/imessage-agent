# Phase 3B: local names, sessions and reply timing

Auto replies remain disabled. Classification and conversation state are advisory;
neither changes recipient allowlists, account selection, outbound limits, exact
payload approvals, or UNKNOWN reservations. There is no new scheduler, transport,
AI follow-up loop, cloud dependency, or Messages database writer.

## Contacts

The separate native helper uses Apple's supported `CNContactStore` API. It fetches
only contact identifiers, given/family/organization names, phone numbers and email
addresses. It never opens the Contacts SQLite database or saves/modifies contacts.
The app bundle has its own identity and `NSContactsUsageDescription`. Permission
is requested only by an explicit **Request Contacts access** action; normal
refreshes, dashboard reads and worker polls never request authorization.

Install once from this checkout, without starting the worker:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts install
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts authorize --state "$STATE"
```

Installation compiles and ad-hoc signs `Contacts.app` under
`~/Library/Application Support/iMessage Agent`. Xcode command line tools are
required. The helper is launched locally with `open -W`; its result is written
mode 0600 inside a private temporary directory and removed after reading.
Supported API references: [accessing the contact store](https://developer.apple.com/documentation/contacts/accessing-the-contact-store)
and [CNContactStore](https://developer.apple.com/documentation/contacts/cncontactstore).

Names and handles are cached in the existing private agent-state database, not
in the outbound contacts/authorization table. The cache lasts one hour; a fresh
resolver checks OS permission before using persisted names. Denial, restrictions,
missing helper and read failures fall back to handles and invalidate cached names.
Refresh after editing contacts or clear the cache from dashboard Settings:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts refresh --state "$STATE"
PYTHONPATH=src .venv/bin/python -m imessage_agent contacts invalidate --state "$STATE"
```

International `+` and `00` phone formats normalize spacing and punctuation.
National numbers match only with an explicit country calling code, e.g.
`contacts refresh --state "$STATE" --country-code 1`. This is a conservative
normalizer, not a global dial-plan parser: trunk prefixes and extensions are not
inferred. Email domains normalize case; local-part case remains exact, consistent
with canonical recipient identity. There is no last-seven/ten-digit matching.

Multiple handles on one saved contact share a label. Shared handles across
contacts are ambiguous and retain the original handle. Duplicate saved names
include the selected handle in the display label. Names are never send selectors;
all approvals and transports still bind the original canonical recipient.

Labels appear in contacts, drafts, approvals, schedules, overview and activity.
Enabled notifications can include the label; worker logs retain fixed categories
and never include names/handles. Existing installed Notifier apps need rebuilding
through the existing explicit LaunchAgent uninstall/install procedure to support
labels. Preserve the existing state and selected transport flags; this change does
not replace, restart or activate installed helpers automatically.

Names/contacts do not enter model prompts. Cache/state and private evaluation
files contain plaintext personal data; keep their parent directories private and
out of cloud sync. Clearing the cache does not revoke OS permission; use macOS
Privacy & Security > Contacts for that.

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
