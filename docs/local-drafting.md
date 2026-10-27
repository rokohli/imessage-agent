# Phase 3A: private Ollama draft previews

This phase generates and stores drafts, never sends them. Existing recipient
allowlists, exact-payload approvals, reservations, and supervised sending remain
independent. There is no automatic reply worker or path from a draft classification
to an outbound call. Do not expose Store or transport administration to a model.

Use Python >=3.10. These commands use the compatible interpreter available on this
Mac. Run them from `/path/to/imessage-agent`.

## Start and check a local model

Install Ollama separately if needed. Model downloads fetch weights, not message
history. Download the desired model explicitly before processing history:

```sh
ollama pull qwen2.5:3b
```

Quit any existing Ollama app/server first, then run this in a separate terminal:

```sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve
```

Do not expose this unauthenticated server through a tunnel or proxy. For the macOS
Ollama app instead, configure it and restart it:

```sh
launchctl setenv OLLAMA_HOST 127.0.0.1:11434
launchctl setenv OLLAMA_NO_CLOUD 1
```

Check the listener and model without accessing Messages:

```sh
lsof -nP -iTCP:11434 -sTCP:LISTEN
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent model-health
curl --noproxy '*' --fail --silent --show-error http://127.0.0.1:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:3b","stream":false,"think":false,"messages":[{"role":"user","content":"Say hello briefly."}],"options":{"num_predict":64,"temperature":0.3}}'
```

The listener must be `127.0.0.1:11434`, not `*:11434` or `0.0.0.0:11434`.
The client cannot configure or prove the server's listening interfaces; the
operator must start it as above. `model-health` checks `/api/show` for an installed
local model and rejects remote-model metadata. Cloud-tagged models are refused.
The adapter uses direct HTTP to the fixed IPv4 loopback address, ignores proxy
environment settings, follows no redirects, checks response size and completion,
and has a 60-second timeout. It never pulls a model or attempts another provider.
Run the trusted Ollama installation with cloud features disabled; a malicious or
misconfigured local daemon is outside this client's trust boundary.

Configuration is optional and local. Default: `qwen2.5:3b`, temperature `0.3`, seed
`42`, 160 generated tokens, 8192 context tokens, thinking off and streaming off.
Create comparison files without putting histories or secrets in them:

```sh
mkdir -p local
cat > local/qwen2.5-3b-cool.json <<'JSON'
{"model":"qwen2.5:3b","temperature":0.1,"num_predict":160}
JSON
cat > local/qwen2.5-3b-warm.json <<'JSON'
{"model":"qwen2.5:3b","temperature":0.5,"num_predict":160}
JSON
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent model-health \
  --config local/qwen2.5-3b-cool.json
```

Existing local config files that explicitly name an older model still override
the default; they are not rewritten. To compare another already-installed local
model, change `model` in another file
and pass it with `--compare-config`. Endpoint overrides are accepted only when
equal to `http://127.0.0.1:11434/api/chat`; non-loopback destinations are refused.
Temperature is bounded to 0–1 and generation to 32–256 tokens. Unknown config keys
are errors. Models must support non-thinking schema-constrained JSON chat; incompatibility fails
explicitly, without changing models or silently relaxing the request.

## Build profiles and preview drafts

No command discovers or defaults to your personal Messages database. Specify an
explicit authorized path. The examples below deliberately use a placeholder;
replace it with your chosen path before running. State must be outside this
checkout, in a trusted private directory, and separate from the source and its
SQLite sidecars. The existing safe state-path checks apply.

```sh
export MESSAGES_DB='/absolute/path/to/authorized/chat.db'
export DRAFT_STATE="$HOME/.local/state/imessage-agent/drafting.sqlite3"
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent build-profiles \
  --database "$MESSAGES_DB" --state "$DRAFT_STATE"
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent draft-preview \
  --database "$MESSAGES_DB" --state "$DRAFT_STATE" --source-sequence 12345 \
  --config local/qwen2.5-3b-cool.json --show-text
```

`12345` is an explicitly selected incoming Messages ROWID, not a contact name or
an instruction to send. Without `--show-text`, preview prints only the draft ID,
classification, fixed reason, and disabled sending status. `--show-text` exposes
the proposed reply in your terminal; do not redirect private previews into logs.
There is intentionally no automatic “latest unread” selection or sender discovery.

The read-only adapter must verify exactly one chat membership via
`chat_handle_join` and one canonical E.164/email address. Missing membership
schema fails closed. Groups, unresolved chats, ambiguous identities, system rows,
and mismatched senders are excluded. If chat `style` exists, only individual style
45 is admitted. Names and Contacts are never consulted.

Profiles summarize outgoing message length, casing, terminal punctuation and emoji
usage, retaining up to 12 filtered recent examples each for general style and each
exact recipient. Contact profiles combine that recipient's verified individual
chats; recent context stays in the target chat. Prior incoming/outgoing reply turns
are ranked by normalized keyword similarity, question shape and recency; duplicate
reply wording is removed. The improved pipeline supplies up to five historical
conversation/reply pairs with explicit contact/user speaker order and 12 preceding
context rows plus the current incoming message. When at least three distinct
relevant same-contact replies exist, examples come only from that contact's
verified chats. Otherwise relevant general pairs fill the remaining slots.
Zero-overlap examples are omitted rather than padded to a quota. Isolated outgoing
snippets no longer enter the improved prompt. Metrics use all
safe outgoing samples in the selected history. Profiles can be empty; the model must then use
neutral short wording instead of claiming to know your style.

Preview rebuilds profiles from a bounded sample before its selected message,
rather than trusting a saved profile that may contain later history. `build-profiles`
persists an inspectable style snapshot; preview does not require that preliminary
command or reuse a potentially leaking snapshot. References include source ID,
generation, target GUID/ROWID, preceding context GUIDs and selected example/style
GUIDs in the local draft record. New tables are `style_profiles` and `reply_drafts`.
No incoming history is copied into agent `messages` by these commands. Existing
ingestion state and cursors are untouched. Schema v1/v2 upgrades to v3 retain all
existing data. Smoke dry runs continue migrating only disposable copies.

`--max-messages` now bounds **selected history rows**, not database size. The
shared loader for `build-profiles`, `draft-preview` and `evaluate-drafts` pages
backward by ROWID in read-only SQLite queries of at most 1,000 rows. Groups are
excluded in SQL before decoding; verified identities are rechecked after decoding.
It selects recent safe outgoing messages and reserves slots for their nearest
preceding message in the same chat. Selected rows are deduplicated and returned
in ascending ROWID order. Sensitive preceding context is retained only as a
redacted placeholder, never as a style example. Unneeded incoming messages and
unsafe outgoing examples do not consume the selection budget. There is no random
sampling; the same unchanged source and options produce the same sample.

The default selection budget is 20,000; valid values remain 1–200,000. Databases
with more records no longer fail or require their full history in memory. Pages
may scan more candidates than the selection budget when useful examples are
sparse, but retained history stays within the budget plus one read page. A profile
may have fewer samples when insufficient safe outgoing messages exist. This is a
recent sample, not a complete lifetime profile or a guarantee that every contact
has examples. No background scan or database write is started.

For a recent 5,000–10,000-row sample, use either budget below (with the explicit
path variables set as above). The budget includes outgoing examples **and**
preceding context; for alternating incoming/outgoing pairs, 10,000 rows yields
5,000 outgoing style samples. Check `messages_used_for_profiles` for the actual
number used rather than assuming every selected row is an outgoing example.

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent build-profiles \
  --database "$MESSAGES_DB" --state "$DRAFT_STATE" --max-messages 10000
# Use --max-messages 5000 for a smaller sample.
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent evaluate-drafts \
  --database "$MESSAGES_DB" --max-messages 10000 --cases 20
```

Sampling counters are printed without message text or contact identifiers:

- `messages_scanned`: candidate rows fetched/decoded from individual-chat SQL
  pages, including unused rows in the last page. This is not SQLite's internal
  row-visit count or the total number of messages in the database.
- `messages_selected`: retained outgoing/context rows, at most `--max-messages`.
- `messages_filtered`: scanned rows not retained due to identity, sensitive
  outgoing content, unneeded context, or the selection budget. It equals scanned
  minus selected. Groups skipped in SQL are outside these counters.
- `messages_used_for_profiles`: safe selected outgoing examples used for the
  general profile; contact profiles partition those examples by exact recipient.
  In evaluation/preview these are the sampled examples available before each
  generation's additional chronological cutoff, not a count of prompt examples.
- `context_messages_redacted`: retained context rows with their bodies removed;
  these count as selected context, not outgoing style samples.

An explicitly selected preview target is fetched independently, so it can be older
than the newest sample. Sampling ends strictly before that target and reserves up
to 12 preceding rows in its chat for recent context, within the same budget.
Evaluation uses the same sampler and then hides each reference reply and all
subsequent rows before rebuilding profiles/retrieval; sampling does not bypass
that leakage guard. Very small budgets can leave too few safe reply pairs, which
still returns `no_safe_historical_reply_pairs`, not a database-size error.

Read order remains ROWID; drafting also excludes rows timestamped after the target.
Edits, unsends, in-place restores and late hydration retain the reader's existing
limitations. A historical preview is not evidence that a message is still unread
or unanswered.

## Privacy and factual boundaries

Sensitive filtering excludes entire examples containing digits, URLs, emails,
currency symbols, or conservative English credential, financial, health, address
and private-information markers. Sensitive context bodies become `[content
excluded]`; sensitive targets produce an empty `APPROVAL_REQUIRED` draft without
calling the model. Attachment-only targets are withheld. Excluded historical
outgoing content is never retained as a style example.

This heuristic deliberately drops many ordinary texts and cannot recognize every
secret, name, address or non-English sensitive phrase. It is not a complete data
loss prevention system. Everything stays local, but safe-looking examples and
recipient/source identifiers are still private plaintext. Keep state out of cloud
backup/sync, protect the parent directory, and review local profile contents.
There is no encryption, automatic retention, or secure deletion.

History and examples are untrusted data, never instructions. The model gets no
tools, paths, recipient identifiers, approval controls, or hidden reference reply.
Examples teach style, not current facts. The prompt asks for one short, natural
reply matching historical casing, punctuation, abbreviations and tone, including
slang, jokes, emoji and clarifying questions. It still forbids inventing activities,
availability, plans, commitments and personal facts. Missing facts should produce
`needs_personal_facts: true` and an empty reply, not a fabricated answer.
Generation does not choose a sending classification. The unchanged claim validator
runs afterwards. The improved pipeline maps missing personal facts and clear
personal-claim categories to `NEEDS_USER_INPUT` / `needs_user_input`, preserving a
useful candidate for correction without treating it as verified. Ambiguous claims
remain `APPROVAL_REQUIRED`. Rejections and sensitive-topic guards remain intact.

`content.py` reviews claims instead of requiring exact phrase matches:

- Credentials/verification codes, reasoning/instruction output, invalid controls,
  and replies longer than 300 characters are rejected and their text discarded.
- Clear availability, location, activity, commitment, payment, personal-fact and
  consequential-decision claims are retained as **unverified approval drafts**.
  A true missing-facts flag also requires approval, retaining any otherwise valid
  text. Retention is not confirmation that an activity or promise is true.
- Sensitive output, agreement to a consequential request, or assent with redacted
  context also requires approval. Sensitive/unreadable source messages continue
  to produce an empty approval record without submitting their bodies to a model.
- Remaining wording goes through a separate local claim-classification request.
  There is no exact-vocabulary requirement. The same configured Ollama model
  labels it `harmless`, `availability`, `location`, `activity`, `commitment`,
  `payment`, `decision`, `personal_fact`, `sensitive`, or `uncertain` using a minimal
  enum JSON schema. Claim review uses temperature 0, seed 42, no thinking or tools,
  bounded tokens and the same loopback-only checks as generation. Only sanitized
  preceding context and the proposed draft enter this second request, never the
  hidden actual reply or historical style-example payload. Pure emoji/punctuation
  can pass deterministically after context risks are checked.
- Only `harmless` is accepted by this review. Every other category or a failed,
  malformed or unavailable claim check holds the candidate for approval. There is
  no cloud fallback and no automatic acceptance when the reviewer fails.

This adds up to one local model request per draft. The rules are conservative
English heuristics, and the local reviewer can misclassify implications, sarcasm
or disguised personal facts. It is not a factual oracle. Clear rules take priority
over a model's favorable label; no regex absence alone authorizes a textual reply.
Review every draft. Human confirmation of personal facts and sending approval
remain outside the model.

The model now proposes only reply text and whether personal facts are missing.
It does not select any autonomy classification. Ollama receives this minimal JSON
schema through `format`, also included in the prompt, following its
[structured-output interface](https://docs.ollama.com/capabilities/structured-outputs):

```json
{
  "type": "object",
  "properties": {
    "reply": {"type": "string"},
    "needs_personal_facts": {"type": "boolean"}
  },
  "required": ["reply", "needs_personal_facts"],
  "additionalProperties": false
}
```

Local validation still checks the fields and exact types rather than trusting
decoding alone. Local draft JSON adds `validation_status` and `claim_category`;
these are also included in default content-free CLI previews. Existing profile
snapshots, contact retrieval and outbound tables are unchanged. No schema migration
is required. Only `--show-text` displays the retained candidate in the terminal;
an approval draft must be checked/corrected before any independent sending workflow.

Classification is local and advisory:

- `AUTO_SEND_ELIGIBLE`: a recognized reaction to a recognized
  incoming reaction, with no question or redacted context. This existing narrow
  advisory predicate was not expanded to cover new natural-language wording. It
  is stored only; never sent or approved.
- `APPROVAL_REQUIRED`: other permitted wording, missing facts, or sensitive input.
  Empty drafts require authoring new text, not approving an empty payload.
- `NO_REPLY`: outgoing/system/mismatched source, or empty generated text with
  `needs_personal_facts: false`.

The provider accepts completed JSON chat responses (`done: true`, with
`done_reason: "stop"` or an omitted reason for compatibility). Truncation
(`done_reason: "length"`), incomplete responses, unexpected completion reasons
and malformed content are errors, even if a truncated payload happens to parse
as JSON. Empty/whitespace content returns `ollama_empty_response`; reasoning
fields are never substituted for missing content. Valid JSON with an empty
`reply` remains usable: a true missing-facts flag requires approval, while a false
flag selects `NO_REPLY`. These fields follow
the [Ollama chat API](https://docs.ollama.com/api/chat).

Diagnostics use fixed rule names without model text, arbitrary field names,
recipient identifiers, source GUIDs, paths or server error bodies. For example:

| Rule | Meaning |
| --- | --- |
| `ollama_missing_reply` / `ollama_reply_not_text` | Missing or non-text reply field |
| `ollama_missing_needs_personal_facts` / `ollama_needs_personal_facts_not_boolean` | Missing or incorrectly typed factual-status field |
| `ollama_unexpected_fields` | Extra fields; their names/values are withheld |
| `ollama_malformed_json` / `ollama_response_not_object` | Invalid JSON, commentary around JSON, or non-object JSON |
| `ollama_incomplete_response` / `ollama_truncated_response` | Unfinished response or exhausted token budget |
| `ollama_empty_response` | Empty provider content; no reasoning fallback |

Content validation has separate outcomes, independent of send authorization:

| Outcome | Behavior |
| --- | --- |
| `accepted` | Text passed content review; this never creates an approval or sends |
| `approval_required` | Candidate retained for factual/context confirmation, or empty when the source/model supplies no draft |
| `rejected` | Candidate discarded for credentials, reasoning, length, invalid controls or response-contract failures |
| `no_reply` | No reply proposed/appropriate |

`claim_category` is a fixed code such as `activity`, `availability`, `commitment`,
`payment`, `sensitive`, `consequential_context`, `unknown_personal_facts`,
`claim_check_unavailable`, `claim_check_invalid_response`, `credentials_or_code`, `reasoning_or_instructions`, or
`harmless`. Categories do not contain snippets, amounts, locations, names or
arbitrary model labels. A harmless joke may have `validation_status: accepted`
while its **sending** classification remains `APPROVAL_REQUIRED`; content acceptance
is not human approval. Auto-sending remains disabled in every case.

Malformed contracts and provider failures return fixed errors. No raw server
errors, prompts, conversations or generated text are logged. Private state can
now retain unverified approval candidates as well as accepted text; protect it
as plaintext personal data. Credentials rejected by this check are not retained.

Fictional regression examples (not real model measurements):

| Candidate | Content outcome |
| --- | --- |
| `bruh 💀` / `lmaooo stop 😂` / `I'm dead 😂` | Accepted when the local reviewer labels the reaction/joke harmless |
| `what'd you mean by that?` | Accepted as a genuine clarification |
| `Checking emails, how about you?` | Retained for approval: activity |
| `im free tonight` / `omw` | Retained for approval: availability/location |
| `I'll bring it tomorrow` / `I'll pay you tomorrow` | Retained for approval: commitment/payment |
| `sure` in response to `can you cover my shift?` | Retained for approval: consequential context |
| `verification code 829104` / `<think>analysis</think>` | Rejected and discarded |

## Historical evaluation

Run 20 held-out cases with the default `qwen2.5:3b` (no `--config` override):

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent evaluate-drafts \
  --database "$MESSAGES_DB" --max-messages 10000 --cases 20
```

For a comparison between explicit local configurations:

```sh
umask 077
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent evaluate-drafts \
  --database "$MESSAGES_DB" --cases 20 \
  --config local/qwen2.5-3b-cool.json \
  --compare-config local/qwen2.5-3b-warm.json > local/evaluation-report.json
```

This uses the same bounded recent-history sampler as profile building, then
selects safe historical incoming/outgoing pairs, rotating across individual
conversations, newest pairs first. Actual replies must follow within 24 hours.
For each case it hides the outgoing reply and all subsequent rows, rebuilds both
profiles and retrieval from the earlier prefix, then generates a replacement.
Every configuration gets identical cases and seed. No generated replies are sent
or stored as training examples. Evaluation writes no draft/profile state.

The content-free report compares generated and actual replies for style
(casing/punctuation/emoji), length (word-count ratio), tone (gratitude/laughter/
question/exclamation markers) and relevance (lexical F1 against the actual reply).
Only content-accepted replies earn heuristic credit; approval candidates,
rejected/invalid output and no-reply cases score zero. It reports aggregate scores, withheld
counts, number of conversations, and the highest-scoring configuration. Ties and
all-zero results have no winner. Individual invalid responses do not abort the
remaining cases. Each invalid case contributes zero to every metric, so dropping
bad responses cannot inflate a model's score. Per-configuration reports include
`cases`, `completed`, `failed`, `withheld`, `errors` (case ordinal and fixed rule),
and `draft_reasons` counts. `validation_counts` reports accepted,
approval-required, rejected and no-reply counts separately, summing to all selected
cases. Invalid response cases count as rejected and also appear in `failed`.
`content_categories` aggregates the reasons, and `case_validations` maps each case
ordinal to its status/category without message identifiers or text. `withheld`
continues to mean completed cases with an empty candidate; retained approval
candidates are not empty, so use `validation_counts` for the approval rate.
No raw replies or source-message identifiers appear.

Overall `status` is `complete`, `partial`, or `failed`. Partial runs keep their
usable results and return exit code 0 with explicit failure counts; if every case
is invalid, the report has no winner and the CLI exits 1. A completed but withheld
response is counted separately from an invalid response. Global service failures
(unavailable server/missing model/HTTP request failure) and rejected remote models
remain fatal with fixed errors, without retrying another provider. Configuration,
source access, and state failures also remain fatal.

These transparent heuristics are useful regression measures, not semantic
judgments. Lexical overlap penalizes alternate valid responses and cannot prove
relevance. Manually review local previews for appropriateness before selecting a
configuration. Do not interpret a small sample's winner as a universal best model.
See [the implementation report](phase3a-evaluation-report.md) for current evidence.

## Offline checks

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
```

Tests create synthetic Messages databases and mock Ollama/AppleScript. No personal
Messages or Contacts data, live sends, external models or downloaded dependencies
are needed.


## Opt-in human quality review

`review-drafts` creates a private, resumable snapshot/rating file and intentionally
displays context, generated and actual held-out replies only with `--show-text`
in an interactive terminal. It collects style, relevance and naturalness scores
(1–5) plus a personal-information-invention label. Human means and coverage are
reported separately from heuristic metrics; ratings do not enable sending or
update profiles. See [the human review workflow](human-review.md) for commands,
privacy controls, the rubric, resume behavior and retrieval/grounding changes.

Fresh profiles and drafts exclude native service metadata, known English/Spanish
reaction notifications and explicit agent-test markers. Literal older-message
copies get at most one local revision, then remain flagged for human approval if
still repeated. Profiles/prompts now account for light punctuation. See
[clean follow-up review](human-review.md#clean-follow-up-review-after-artifactcopy-fixes)
for the filters, fixed quality flags, limitations and a new-file rerun command.

For a controlled comparison, use `compare-drafts` with the **existing** 20-case
review as `--source-review` and a new `--comparison` file. Do not generate fresh
cases to replace the baseline. The [blind-comparison workflow](human-review.md#blind-comparison-on-the-same-20-saved-cases)
documents exact commands, optional installed 7B detection, frozen source checks,
rating/resume and separate latency reporting. The punctuation policy is unchanged.
