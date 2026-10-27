# Phase 3A implementation evaluation report

Date: 2026-10-26. No real messages were sent or personal Messages/Contacts
history accessed. The supplied workspace has no `.git` directory; updating local
`main` and verifying its remote revision were therefore unavailable.

## Current default: Qwen2.5 3B

The default is now `qwen2.5:3b`, selected using the owner's reported local checks:
Qwen3 4B repeatedly produced reasoning-like output and exhausted its budget even
with thinking disabled. Qwen2.5 3B returned a short completion with stop reason,
8 output tokens and 2.16-second total latency including model loading. Its reply,
"Checking emails, how about you?", invented an activity absent from context.
These are owner-reported observations, not a new benchmark run by this change.

The prompt now explicitly prohibits invented activities, availability, plans,
commitments and personal facts. The current generation contract requests an
empty reply with `needs_personal_facts: true` when facts are unknown; local code
assigns `APPROVAL_REQUIRED`. Regression tests verify that this exact claim
and other unsupported personal claims are withheld, irrespective of the model's
proposed eligibility. The deterministic output gate remains in place. Provider
checks distinguish successful completion, empty response, intentional empty
JSON draft, and truncation; no reasoning fallback is used.

Model overrides and comparison configs remain supported. Existing style profiles,
contact retrieval, bounded history selection, and outbound controls are unchanged.
All **216 offline tests pass**, including personal-claim, completion, schema,
claim-review and partial-evaluation regressions. Existing supervised-test SQLite ResourceWarnings remain.
The owner supplied a 20-case baseline described below; the revised historical
claim-review run is still unmeasured. Run the default-model evaluation
command in [the runbook](local-drafting.md#historical-evaluation).

## Original model check (historical)

**No held-out model winner was measured in the original implementation.** The local `model-health` command
returned `ollama_unavailable_start_loopback_server_and_install_model` for the
default model/endpoint. No model was downloaded, server configuration changed, or
cloud fallback attempted. Mocked outputs establish correctness of evaluation
logic, not model performance or resemblance to the owner's texting.

| Configuration | Intended comparison | Measured local model result |
| --- | --- | --- |
| qwen3:4b, temperature 0.3, 160 tokens | Original default baseline | Unavailable |
| qwen3:4b, temperature 0.1, 160 tokens | Lower-temperature comparison | Not measured |
| qwen3:4b, temperature 0.5, 160 tokens | Higher-temperature comparison | Not measured |

The original default was `qwen3:4b` at temperature 0.3. It has now been replaced
by `qwen2.5:3b` at the same temperature, as described above.
Run the comparison command in [the runbook](local-drafting.md#historical-evaluation)
with the local model running and an explicitly authorized database path. It
prints the best configuration for that sample, or reports a tie/no usable output.
All configurations use identical held-out cases, fixed seed, no thinking, no
streaming and short generation limits. Compare another installed model by adding
a local config with a different `model` value.

## Offline evidence

Offline verification: **190 tests passed**, including 21 new drafting/provider/CLI
tests. Python 3.13 also reports unclosed SQLite-connection ResourceWarnings from
existing supervised-test helpers; the new drafting tests pass without warnings.

The synthetic suite covers individual/group membership, sensitive-example
exclusion, per-recipient retrieval, general style fallback, timestamp/sequence
cutoffs, source references, private persistence, unchanged outbound tables,
no-text default previews, local config validation, remote-model rejection,
provider errors, bounded/truncated responses and held-out profile leakage.

A deterministic mocked comparison verifies that the configuration returning a
closer replacement receives the higher score and that the actual held-out reply
never enters its own generation prompt. This is an evaluation self-check, not an
experiment establishing that one temperature or model actually performs better.
The full suite also exercises existing ingestion, approval and mocked transport
behavior after the v3 schema migration.

## Limits of the quality measures

Style measures casing, terminal punctuation and emoji; length compares word
counts; tone compares gratitude/laughter/question/exclamation markers; relevance
uses lexical F1 against the actual reply. The mean gives every metric equal
weight. Withheld/empty outputs score zero. Ties and all-zero outputs have no
winner. Individual invalid responses now score zero with explicit failure rules
and do not abort other cases; global provider/locality failures remain fatal.

These are transparent regression proxies. They do not understand conversational
intent, sarcasm, affection, or semantically equivalent alternatives. Human review
of local draft previews remains necessary. The original deterministic safety gate limited expressive replies to a small
vocabulary. It has now been replaced by categorized claim review; unverified
personal claims require human confirmation rather than automatic acceptance. Profile examples are
filtered with conservative English heuristics, not comprehensive secret detection.
Personal-history compatibility, real model latency and actual stylistic quality
remain unmeasured.


## History-limit correction

The original loader scanned from ROWID zero and raised on the first row beyond
`--max-messages`, before group or sensitive-content filtering. The option is now a
selection budget, shared by profile building, previews and evaluation. Read-only
reverse ROWID pages skip groups in SQL and select safe outgoing examples with
same-chat preceding context. Reports expose scanned, selected, filtered and
profile-use counts. The synthetic sampling fixture has 21,010 individual records
plus 30,000 newer group records; repeated samples are identical, and 5,000,
10,000 and 20,000-row budgets succeed. The full offline suite after this fix
passes **197 tests**; existing supervised-test SQLite ResourceWarnings remain. No personal-data or model-quality claims
follow from these regression checks. The earlier local health result above is
historical, not a statement about current Ollama availability.


## Draft-contract failure investigation

The original `ollama_invalid_draft_contract` is raised **after** the provider has
parsed a JSON object. Its only branches were:

- absent/non-text/invalid-enum `classification`,
- absent/non-text `reply`, or
- `classification: NO_REPLY` combined with nonempty reply text.

Fictional reproductions verified all these branches produce the same generic
error. An invented activity instead produced an empty `APPROVAL_REQUIRED` draft;
length/content gating also did not raise this contract error. Malformed JSON,
reasoning outside JSON and truncation were provider-parser failures with different
codes. Thus the observed code identifies a field/semantic-contract mismatch,
not a length limit or unsupported-personal-claim rejection.

The request used `format: "json"`, which constrained JSON syntax but did not
require the application's fields, enum values or cross-field rules. The prompt
also combined reply wording with three-way autonomy classification. A standalone
`done_reason: stop` completion establishes neither field-contract compliance nor
correctness of the proposed text.

The local fictional live attempt in this runtime returned
`ollama_unavailable_start_loopback_server_and_install_model`. No personal history
was accessed. The original failing response was not available, so **which exact
old field/value failed remains unverified**; no model-output contents or guessed
classification are presented as observed evidence. These checks establish the
validator's failure paths and the request/validator design mismatch, not a fresh
successful Qwen2.5 benchmark.

The fix constrains `format` with a two-field JSON schema: `reply` (string) and
`needs_personal_facts` (boolean), with both fields required and no extra keys.
Sending eligibility is computed in local code. Missing facts, fabricated activity
claims, sensitive output, over-length text and reasoning cannot become usable
sendable text. Model selection, stored profile formats, contact-specific retrieval,
source selection, approvals and outbound state remain unchanged.

Parser/field failures now identify their exact rule, without printing generated
text, arbitrary field names or message references. Individual evaluation failures
are retained as case-ordinal diagnostics and zero scores; valid cases continue.
Complete, partial and entirely failed results are distinguished. An entirely
invalid run has no best configuration and exits 1; service/locality failures
remain fatal without a fallback. The runbook documents all counters and rules. The full offline suite after this
change passes **205 tests**; existing supervised-test SQLite ResourceWarnings
remain.

Reproduce the contract regressions offline:

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -p test_drafting.py -v
```

Rerun 20 held-out cases on the owner's explicitly authorized database, using the
unchanged default `qwen2.5:3b`:

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent evaluate-drafts \
  --database "$MESSAGES_DB" --max-messages 10000 --cases 20
```

This evaluation writes no profile, draft or outbound state and never sends.


## Claim-based content review

The owner's baseline completed 20 historical cases across 20 conversations with
zero parser/schema errors. Nineteen replies were discarded as
`unsupported_reply_content`; one passed. The reported heuristic mean was 0.026.
The `unsupported_reply_content` reason identifies the exact old rule for all 19:
the proposals had parsed successfully, had not flagged missing personal facts,
were nonempty, were at most 300 characters, passed the broad sensitive-content
check, but fully matched neither the reaction regex nor the fixed-question regex. Consequently even harmless slang, jokes and
new clarifications could not pass that rule.

**The original 19 texts were not retained by evaluation.** The aggregate JSON
cannot distinguish each reply's intent or personal claims. No per-case
retroactive judgment is invented here. The new `case_validations` field records
case ordinals and fixed content categories, without generated text, so a fresh
run can identify whether failures are claims, credentials, context risks, model
review failures or something else.

The new `content.py` gate uses clear risk rules, followed by a separate local
schema-constrained claim-review request for ambiguous wording. Clear personal
activities, availability, locations, promises, payments and consequential choices
are held for approval with their proposed text retained as **unverified**.
Credentials/codes, reasoning/instructions, invalid controls and excessive length
are rejected and discarded. Ordinary novel reactions, slang, emoji, jokes and
clarifying questions do not need exact phrase matches. An unavailable or invalid
reviewer holds the candidate for approval; it does not silently accept it.

The second step uses the same configured local `qwen2.5:3b`, temperature 0 and
seed 42, without tools or cloud access. It sees only preceding sanitized context
and the proposed reply, never the hidden actual response. Its labels are checked
against a fixed enum. The reviewer is not a factual oracle and cannot establish
unprovided personal facts. Natural wording accepted as content may still have a
sending classification of `APPROVAL_REQUIRED`; the prior narrow advisory
eligibility predicate is not expanded. Auto-sending remains disabled.

Private drafts add `validation_status` and `claim_category` as JSON fields. No
profile/recipient schema, existing profile records, ingestion cursor, approval,
duplicate-send reservation or outbound transport code is changed. Held approval
candidates can contain unverified personal claims and must be reviewed/corrected.
Only content-accepted responses earn heuristic evaluation credit; retained
approval candidates are not rewarded as factual answers. Reports expose separate
accepted, approval-required, rejected and no-reply counts plus per-case categories.
Invalid response failures count as rejected and retain their existing error codes.

Offline evidence: **216 tests pass** with network sockets blocked, including
natural wording, slang/emoji, invented activities, payments/promises, affirmative
replies to consequential requests, sensitive inputs, review-schema validation,
failure-to-approval fallback, and hidden-reply exclusion from the second step.
Existing supervised-test ResourceWarnings remain unrelated to this change.

A 20-case **fictional, mocked** held-out pipeline fixture reports:

| Content outcome | Cases |
| --- | ---: |
| Accepted | 10 |
| Approval required | 7 |
| Rejected/discarded | 2 |
| No reply | 1 |
| Parser/schema failures | 0 |

These counts verify pipeline classification/reporting, **not measured model
performance or improvement on the owner's historical cases**. Real examples and
message contents are never logged by this fixture.

The historical rerun is intentionally left to the owner. The owner confirmed that
this runtime cannot access their local Messages database or Ollama server and
explicitly requested synthetic fixtures/offline tests only. No personal Messages
database was discovered or opened, and no real evaluation is attempted here. The
original 19 texts cannot be reconstructed from aggregate counts alone, and a
fresh selection can change if source history changed.

On the owner's Mac, with the explicit authorized path in `MESSAGES_DB`, run:

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent evaluate-drafts \
  --database "$MESSAGES_DB" --max-messages 10000 --cases 20
```

Inspect `validation_counts`, `content_categories` and `case_validations`; none
contain raw draft text or source-message identities. Actual revised historical
acceptance/approval/rejection counts remain **unmeasured** until that run.


## Human quality-validation sprint

The owner subsequently reported a complete 20-case Qwen2.5 3B historical run:
**2 content-accepted, 18 approval-required, 0 rejected, 0 parser errors**, heuristic
mean **0.0604**, with 6,052 sampled outgoing examples and 121 previously generated
contact/general profiles. This is owner-reported historical evidence, not a run
performed in Codex. Because approval-required cases earn zero heuristic credit,
the low aggregate score alone does not tell whether those candidates sound like
the owner or are conversationally useful. Acceptance rate is not human quality.

`review-drafts` now lets the owner inspect a fresh held-out comparison in a local
interactive terminal and rate style similarity, contextual relevance and
naturalness (1–5 each), plus an invented-personal-information yes/no label. It
requires explicit `--show-text`, refuses terminal redirection, saves snapshots
before any feedback, and commits each valid rating to a dedicated private review
SQLite file. Resume rates saved candidates without Messages/model access. The
actual reference reply is revealed to the human only after generation; it is not
input to generation or claim review for its own case. Old aggregate runs cannot
be reconstructed into their exact original generated candidates.

Human-review counts/means and invention labels are reported under `human_review`,
separately from `heuristics` and with requested/prepared/pending/failure coverage.
No human score is fabricated, imputed for skipped cases, combined with heuristic
scores or used to enable sending. An accepted candidate humans flag as inventing
facts is separately counted for investigation. There is no automatic readiness
claim. **Actual owner ratings remain unmeasured until the owner runs the review.**

Retrieval/grounding changes prioritize relevant diverse examples for the same
canonical recipient across verified individual chats, combine safe consecutive
incoming messages into reply turns, skip stale/ambiguous/sensitive pairs, and
hydrate the actual preceding chat context before each evaluation/review case.
Established contact examples take priority over raw mixed-contact general
examples. Saved style profile schemas/records and the claim-based validator are
unchanged; neither ratings nor review snapshots become training data or approvals.

Offline evidence: **230 tests pass** with network sockets blocked. New regressions
cover a 20-snapshot scripted-rating workflow, immediate rating persistence,
resume/interruption/skip behavior, opt-in/TTY privacy gates, private file modes,
source/outbound-file protection, invalid ratings, human/heuristic separation,
recipient-isolated retrieval, multi-message context, diverse examples, and
hidden-reply/timestamp/boundary exclusion in generation and claim review. Scripted
fixture ratings are test inputs, not empirical owner-quality ratings. No personal
Messages/Contacts history or real Ollama model was accessed for this sprint.

See [the human review runbook](human-review.md) for the exact 20-case command,
rating rubric, private artifact contents, resume command and report interpretation.

## Artifact/copy cleanup and light-punctuation follow-up

Owner-shared private review examples revealed known reaction-notification forms
being treated as authored replies, explicit agent-test probes entering context,
and literal copying of unrelated older text. The provided examples are evidence
of those defects, not a full human-rated quality result. No raw owner conversation
or names are copied into regression fixtures or this report.

The follow-up adds a shared Phase 3A non-authored-content filter using source
service metadata plus known English/Spanish text fallbacks and test markers. Raw
ingestion still retains original direction/body/sequence; there is no inbox or
outbound migration. Profiles, paired examples, contexts and held-out targets now
exclude artifacts, and candidate pairs do not bridge removed artifact boundaries.
PAT credential-topic text is excluded by the sensitive filter. Artifact counts are
reported without source text or identities.

Generation now highlights the latest incoming turn, removes duplicate raw context
examples and zero-keyword-overlap pair filler, and permits one bounded rewrite
of an exact older outgoing statement. If that statement persists, a quality flag
keeps it under approval. A rewrite cannot bypass the unchanged claim validator,
force a fabricated activity/commitment, or grant sending rights. Known generic
acknowledgments and emoji can repeat. Copy flags do not prove irrelevance and do
not detect all paraphrases.

The owner's low-punctuation preference is explicit in the prompt and supported by
separate period/question/comma profile fractions. There is no blanket character
stripping that would remove apostrophes, emoji or meaningful question marks.

Verification: **245 offline tests pass** with network sockets blocked, including
service metadata without raw-ingestion reclassification, English/Spanish reaction
forms, agent probes, profile/retrieval/context/target exclusion, reply-boundary
integrity, one-retry/failure behavior, unchanged claim validation after revision,
credential protection, repeatable benign reactions, punctuation signals and frozen
legacy-review compatibility. Tests use fictional fixtures and mocked local models;
no personal history, actual review files or live Ollama service is accessed.

Existing saved reviews are not overwritten or re-rated. The owner should run a
fresh 20-case review with a new filename using the command in
[the review runbook](human-review.md#clean-follow-up-review-after-artifactcopy-fixes).
Actual post-cleanup human style/relevance/naturalness and invention rates remain
**unmeasured** here. The full old review JSON summary has not been supplied; no
unseen ratings or readiness claim is inferred.

## Owner clarification: punctuation is the main style issue

The owner identified overuse of punctuation as the main defect in the rated
outputs. The generation and copy-revision instructions now default to **no
punctuation in reply text**, with per-mark exceptions only for strong evidence in
the owner's own safe outgoing messages in the visible current-chat context:
at least three messages using that mark and at least a two-thirds message share.
Contact messages, other chats, general examples and held-out/future replies do
not supply that evidence. JSON syntax is unaffected; emoji remain allowed.

A `punctuation_policy` payload makes those exceptions explicit and overrides broad
style statistics. This changes instructions rather than mechanically stripping
characters from generated text. Regression tests cover sparse/weak evidence,
strong owner evidence, per-mark permissions and excluded incoming/other-chat/
held-out evidence. The full offline suite passes **247 tests** with network
sockets blocked. Actual compliance and improved owner ratings still require a
fresh local generation/review; no personal history or live model is used here.
Saved review snapshots and auto-sending remain unchanged.

## Fixed-case retrieval/prompt comparison (owner run pending)

The owner-reported human baseline is **20 rated cases**, style **2.0/5**,
relevance **2.1/5**, naturalness **1.9/5**, invention **0/20 observed**. Zero
observed inventions in this small review is not evidence of general factual
reliability. No new local-model quality measurements were made in Codex.

The improved default pipeline uses chronological incoming-turn/reply examples
with speaker identity instead of isolated outgoing snippets. Up to five relevant
pairs favor the same contact; three distinct relevant same-contact responses are
enough to suppress general fallback. It includes 12 real preceding messages plus
the latest incoming turn, a shorter generation-only prompt and the unchanged
separate claim validator. Missing facts/decisions are explicitly
`NEEDS_USER_INPUT`. The recently implemented punctuation policy is unchanged.

`compare-drafts` freezes exact references from the same existing 20-case review:
A is the punctuation-fixed 3B baseline, B improved 3B, C improved 7B only if already
installed. It saves shuffled trials for blind ratings, with labels/classification/
timing hidden until all available cases are rated. Reports separate human quality,
invention labels, coverage, heuristic proxies, validation outcomes and latency
(mean/median/p95). It never picks a winner by acceptance rate. See
[the exact owner commands and limitations](human-review.md#blind-comparison-on-the-same-20-saved-cases).

The complete **256-test offline suite passed with sockets blocked**. Synthetic
checks cover contact priority/general fallback, five-pair limits, chronological
speaker/context retrieval, exclusion of each held-out/future reply from generation
and claim review, identical B/C inputs, unchanged punctuation instructions,
private read-only source artifacts, no case reselection, optional installed 7B,
per-case generation errors, blind resume and separate human/heuristic/latency
reports. No personal Messages history or live Ollama was accessed; no model was
downloaded, no profiles/outbound approvals were modified and no message was sent.
Historical improvement and an A/B/C preference remain unmeasured until the owner
runs the comparison and completes ratings.

## Publication preparation, 2026-10-27

Re-ran the complete 256-test suite with sockets blocked: all passed on Python
3.12.7. Confirmed local Ollama, general/contact profiles, conversational retrieval,
Qwen2.5 3B default, claim review, punctuation policy, human review and the latest
fixed-case comparison are present. This is offline correctness evidence only;
post-improvement owner ratings and real model quality remain unmeasured.
Autonomous replies remain disabled. No personal history, local databases, model
weights or local configuration are included in the publication tree.

The source workspace lacks Git metadata. Reconciled into the existing publication
checkout without overwriting unrelated work. Remote revision and publication
remain unverified until GitHub connectivity and CLI authentication work.
