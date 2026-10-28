# Phase 3B evaluation — 2026-10-27

The features are implemented and offline regressions pass. The saved-case study
does **not** demonstrate reliable autonomous replies or a human-quality win.
No real message was sent, no Contacts permission was requested, and no live
allowlist, account selection, approval, scheduler configuration or UNKNOWN
reservation was changed during this evaluation.

## Study and evidence

The same twenty frozen cases from the existing clean Phase 3A review were used
for both variants, without reselection. Source identity/generation, incoming and
held-out references and saved content were checked. Each generation saw only
preceding sequence numbers and timestamps. The Messages reader used its existing
read-only URI, query-only setting and write-denying authorizer.

- A: Phase 3A prompt and last-twelve-message context, punctuation policy retained.
- B: session-aware context, explicit historical/style separation, new prompt and
  ending suppression, with the same punctuation policy and style retrieval code.
- Both: already-installed local `qwen2.5:3b`, temperature 0.3, seed 42 and the
  existing independent deterministic/local claim review. No model download or
  cloud provider was used. Auto sending was false for every draft.

Forty drafts completed with **zero generation errors**. A classified all twenty
cases as active; B identified **5 new sessions, 1 topic switch, 13 active exchanges
and 1 uncertain case**. B retrieved labeled historical context for 16 cases. Both
retained nonempty wording in 19 cases and returned no wording in one. None of the
saved B targets was classified LIKELY_ENDED, so terminal-acknowledgment behavior
is covered by synthetic tests, not demonstrated by these twenty saved cases.

Median generation/retrieval/claim-review wall time was approximately **1.90 s for
A** and **1.99 s for B** on this machine. This is a local snapshot, not a benchmark
or evidence of background/live-send latency.

Private comparison artifacts remain outside the checkout:
`/private/tmp/phase3b-session-final.sqlite3` (mode 0600), with aggregate local-judge
results in `/private/tmp/phase3b-final-judge.json`. Temporary files may be removed
by the OS. Original reviews and live agent state were not updated. No private
message text, contact names or handles are included in this report.

## Conversation quality — uncalibrated local judge

A separate scoring pass used the same local Qwen model, with timestamped same-chat
evidence ending at each incoming message. The held-out reply and future messages
were excluded from judging evidence as well as generation. Wording was scored
even when held for approval. These are **model judgments, not human ratings or
validated accuracy**; forty of forty judgments completed without errors.

| Metric | A: last twelve | B: sessions |
| --- | ---: | ---: |
| Contextual relevance, 1–5 | 4.95 | 4.80 |
| Naturalness, 1–5 | 4.95 | 4.65 |
| Judge says session boundary correct | 1/20 (5%) | 3/20 (15%) |
| Judge says topic decision correct | 2/20 (10%) | 6/20 (30%) |
| Judge flags unnecessary reply | 0/20 | 0/20 |
| Judge flags invented personal facts | 0/20 | 0/20 |

The low boundary/topic agreement and very high wording scores are reasons to
**distrust this small model as an accuracy judge**, not evidence that either
pipeline is ready for autonomy. There are no independently labeled real session
or topic boundaries, so true boundary/topic accuracy remains unmeasured. The
saved review was selected from incoming messages with actual owner replies;
it underrepresents conversations where silence was appropriate. Zero unnecessary
replies here does not establish a real-world unnecessary-reply rate. Likewise,
zero self-judged inventions does not establish that no personal facts were invented.

Human review is pending. `review-drafts --resume --show-text` can rate relevance,
naturalness, invented information, session/topic correctness and unnecessary replies
without rerunning a model or rewriting prior ratings. Null correctness labels
mean insufficient evidence. No prior Phase 3A human rating is reused as a new score.

## Style similarity — reported separately

The existing casing/terminal-punctuation/emoji agreement proxy, calculated over
all generated wording independently of approval outcome, was **0.583 for A** and
**0.517 for B**. This is not human style similarity and does not understand tone,
relationship, humor or meaning. No style improvement or overall winner is claimed.
The Phase 3A per-mark punctuation instruction and owner-evidence thresholds remain
in place; session boundaries change which outgoing turns count as current evidence.

## Offline and native validation

- Full Python suite: **375 tests passed**, with IPv4/IPv6 socket creation blocked
  and local Unix socketpairs allowed for the in-process dashboard test client.
- Dashboard: **20 tests passed**, TypeScript checking and production build passed.
- Generated API types match FastAPI OpenAPI; `git diff --check` passed.
- Native Contacts helper compiled and linked against AppKit/Contacts; updated
  Notifier compiled and linked against AppKit/UserNotifications. Neither executable
  was launched, and no Contacts/notification permission or real send was tested.

Synthetic regressions cover several-day gaps, midnight and DST offsets, same-day
subject changes, historical references, terminal acknowledgments, burst settling,
manual replies during generation, stale drafts after new messages, cancellation
and unused-approval revocation, Contacts normalization/multiple handles/duplicate
names/ambiguity/permission denial/cache refresh, dashboard metadata, unchanged
canonical identities, and held-out/future leakage prevention. Native helper
installation and notification name handling are tested with mocked subprocesses.

Contacts behavior on this desktop, OS permission presentation/revocation and
native notification appearance still need an explicit supervised no-send check.
The English topic vocabulary and lexical retrieval can miss paraphrases; regional
phone trunk prefixes are not inferred. Existing installed workers/helpers were
not replaced or activated. These limitations and the weak quality evidence keep
autonomous replies disabled.

## Recipient lookup scope correction

The original Contacts cache implementation was replaced after the operator
clarified that the goal is typing a recipient name, not importing an address book.
The revised helper queries only names matching explicit searches; results are
transient. Only the chosen name/handle is saved with a confirmed schedule. Opening
state removes the legacy bulk cache without altering outbound authorizations.

Follow-up validation: 378 synthetic Python tests and 23 dashboard tests passed;
the production build, API type check and updated native Contacts build passed.
Tests cover no-fetch authorization, no-cache searches, ambiguity, explicit handle
selection, stale search responses, denied access, legacy-cache removal and
preservation of canonical recipients/authorization gates. No real Contacts data
was accessed or imported and no real messages were sent. The conversation-context
study above remains unchanged; this follow-up does not change model prompts or
session detection. Native permission presentation and live name queries still
require operator verification.

The human-review preparation was also corrected to show both variants the same
timestamped evidence window, separately from their model contexts. The existing
private snapshots above were not rewritten; regenerate a comparison into a new
file before human session/topic accuracy review. Local judge results already used
the same timestamped source evidence for both variants and remain as reported.
