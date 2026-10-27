# Phase 3A human quality review

The historical content-acceptance rate and heuristic score do not establish that
replies sound like the owner or fit a conversation. The owner-reported baseline
was 20 completed cases, 2 content-accepted and 18 approval-required, with no rejected
responses or parser errors and a heuristic mean of 0.0604. Human-reviewed style,
relevance, naturalness and invention labels are now collected separately.

Auto-sending remains disabled. Ratings never create approvals, update style
profiles, train the model, change a claim-validation rule, or call a transport.
There is no automatic readiness decision or threshold that enables autonomous
replies. This workflow collects evidence for the owner to judge.

## Review 20 historical drafts locally

Run in a private interactive Terminal on the owner's Mac, with the loopback Ollama
server already running. The default model remains `qwen2.5:3b`:

```sh
cd /path/to/imessage-agent
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent review-drafts \
  --database "$MESSAGES_DB" \
  --max-messages 10000 --cases 20 \
  --ratings "$HOME/.local/state/imessage-agent/phase3a-qwen25-review.sqlite3" \
  --show-text
```

Set `MESSAGES_DB` to the explicit authorized Messages database path first. No
history or model is accessed in Codex for this task; the owner runs this command
locally. An alternative local model can be selected with the existing `--config`
JSON option. Use a different review filename for each comparison/run.

Both `--show-text` and interactive stdin/stdout are required **before** the command
opens a source or creates a review file. Piping/redirecting this command is refused
because it intentionally reveals private context, generated replies and actual
held-out replies. Text is quoted with terminal control/bidi escapes; ordinary
Unicode and emoji remain visible. Do not screen-record or otherwise log this
private terminal session. Default `evaluate-drafts` remains content-free.

The command selects up to 20 safe held-out pairs using the same bounded sampler
and conversation rotation as evaluation. It generates a **new** set of drafts;
previous aggregate evaluation JSON did not retain the old generated text. It
saves snapshots before showing them or asking for ratings. It shows:

- Up to 12 exact preceding messages in that chat plus the selected incoming message.
- The generated reply, including a candidate held for approval.
- The owner's actual held-out reply, revealed only after generation is complete.
- The sending classification, content status/category, and fixed reason.

Sensitive historical context remains `[content excluded]`; credentials rejected
by the validator are not restored. The actual held-out replies are selected using
the existing sensitive-content filter. Sources are opened by the unchanged
read-only adapter; generation and claim review see only preceding messages.
No actual reference reply is sent to either model step for its own case.

## Rating each draft

At the prompt, enter three scores and a yes/no label, for example:

```text
4 5 3 n
```

The fields are, in order:

| Field | 1 | 3 | 5 |
| --- | --- | --- | --- |
| Writing-style similarity | Does not sound like me | Partly matches | Strong match to my wording/tone |
| Contextual relevance | Off topic or inappropriate | Partly addresses the conversation | Directly fits the latest turn |
| Naturalness | Stilted or artificial | Usable but awkward | Something I could naturally text |

The final `y`/`n` asks whether the draft invents personal information. Judge whether
personal claims are supported by the preceding context, not just whether they
sound plausible or happen to resemble facts in the hidden actual reply. A high
style score does not override an invention label or sending approval requirement.
Rate the wording even if the content validator held it for approval. Use `s` to
skip a case you cannot judge; it stays pending for a later review. Use `q` to stop.
Invalid ratings are reprompted, never saved. Each valid rating commits immediately.

A generation/parser failure is listed with its fixed error code and is not given
a human rating. It remains represented by a zero heuristic score. Empty/rejected
usable-contract drafts can be scored poorly or skipped; skipped cases do not
silently become high human-quality ratings.

## Resume saved snapshots

Use the same command with `--resume`:

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent review-drafts \
  --database "$MESSAGES_DB" \
  --ratings "$HOME/.local/state/imessage-agent/phase3a-qwen25-review.sqlite3" \
  --resume --show-text
```

Resume reads the private review file and rates only pending cases. It does not
open Messages, call Ollama, regenerate a candidate, or overwrite an existing
rating. The database argument is still required for source/sidecar path-safety
checks, not source content access. Model/case/sampling settings come from the saved
session. Starting a new run over an existing filename is refused.

Ctrl-C/EOF preserves committed ratings and saved snapshots and exits 130. A
partial preparation interrupted by an unavailable model/source can be resumed to
review the snapshots already saved; resume does not generate the missing cases.
Start a new file once the source/model is available to prepare a complete run.
The summary reports requested versus prepared cases and review coverage. A run
with fewer than the requested cases, pending ratings or incomplete preparation is
`partial`; an all-unavailable run is `failed`. No missing human rating is imputed.

## Saved data and reports

The `--ratings` file is a dedicated SQLite database, mode 0600, outside the checkout
and separate from Messages, profile state and outbound state. New final parent
directories are created private. Existing permissive files, symlinks, hardlinks,
source/sidecar aliases and unrelated databases are refused. Use a trusted private
parent directory; these controls do not isolate another same-user process.

This is a **plaintext private artifact**, not just an anonymous score log. It
contains context excerpts, candidate/actual reply text, source references,
model/sampling metadata, classifications and human ratings. Protect it from cloud
sync, backups and sharing. There is no encryption or automatic retention/erasure.
It is never uploaded or used as a style profile. The separate file has only
`review_session` and `review_cases` tables; no core/outbound schema is migrated.

The final JSON summary contains no case text and separates:

- `human_review`: rated/pending counts, mean style/relevance/naturalness on the
  1–5 scale, invention count/rate, and content-accepted cases humans flagged as
  inventing information. Means/rates are null until someone has rated a case.
- `heuristics`: the existing style/length/tone/lexical-relevance proxies across
  **all prepared cases**, including zero scores for non-accepted/error cases.
- Requested/prepared counts and generation failures, so favorable ratings on a
  small or selectively reviewed subset cannot be presented as a full-run result.

Human scores and heuristic scores use different scales and coverage; no combined
quality score or acceptance-only ranking is produced. Report both with coverage,
and investigate every invention label, especially content-accepted inventions.

## Retrieval and grounding changes

Candidate examples now come from all verified individual chats with the **same
exact canonical recipient**, without merging names, phone/email identities, or
groups. Examples combine up to three safe incoming messages in a turn with the
immediately following outgoing reply. Wrong/system senders and sensitive or stale
(>24-hour reply gap) pairs are excluded. Retrieval ranks normalized keyword
similarity, question shape and recency, then removes duplicate reply wording.
This is a transparent lexical heuristic, not proof of semantic relevance.

When at least three contact-specific style samples exist, generation no longer
mixes raw general-contact examples into that contact's examples; general style
metrics remain available, and sparse contacts keep the general fallback. Existing
saved profile schemas/records are unchanged. Before each evaluation/review case,
a bounded read-only chat query hydrates the actual preceding 12 messages omitted
by the global style sample. Only earlier ROWIDs/timestamps enter the generation
prefix. The prompt explicitly targets the latest incoming turn and treats style
examples as wording examples, never current personal facts.

The existing claim-based validator and duplicate-send/recipient protections are
unchanged. No acceptance-rate improvement or resemblance to the owner is claimed
until the owner completes a representative human review.

## Offline verification

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
```

Tests use synthetic Messages databases and mocked local generation/claim review.
They cover opt-in/TTY gating, private persistence, rating validation, interruption,
resume without source/model calls, held-out leakage, contact identity, retrieval
diversity, context hydration, separate aggregates, and unchanged outbound state.

## Clean follow-up review after artifact/copy fixes

The owner's first displayed cases exposed reaction notifications used as actual
replies, explicit agent-test messages in context, and literal reuse of unrelated
older outgoing statements. These are data/generation defects, not evidence that
the owner should accept more drafts or weaken claim validation.

New Phase 3A runs now exclude nonzero service/reaction metadata, known quoted
English/Spanish reaction-notification text, and explicit `AGENT TEST` / `Testing
agent` markers. They are excluded from style profiles, reply retrieval, model
context and held-out targets. Missing metadata uses an NFC-normalized text
fallback; unrecognized locale forms may still need fixtures. Ordinary authored
text such as `I loved the movie` or `the math test was hard` is retained. Removed
artifacts reset candidate reply-turn boundaries rather than stitching a new pair
across the gap. PAT/personal-access-token topics are also redacted from examples
and context by the existing sensitive-content boundary.

The reader preserves raw direction/body and source sequencing for ingestion.
An optional `is_service` message flag carries source metadata into Phase 3A; it
does not change the core inbox schema or rewrite stored records. No Messages
record is modified or deleted. Fresh profile builds and new draft runs apply the
filter; existing profile snapshots and review files are not silently rewritten.
`artifact_messages_filtered` reports content-free counts for `service_metadata`,
`reaction_notification` and `agent_test` in fetched sample pages.

Retrieval no longer fills reply-pair examples with zero keyword-overlap matches.
Style and pair examples already visible in the current context are not repeated
again as raw examples. The prompt explicitly identifies `latest_incoming_turn` and
tells the model to answer it rather than an older topic. An exact normalized copy
of a prior safe outgoing statement gets **at most one** revision attempt. The
revision must not invent a fact/commitment to avoid repetition. Both versions use
the same local model/schema and generation limits; the final candidate still goes
through the unchanged claim-based validator. Generic acknowledgments/emoji can
repeat without a retry. A remaining non-generic copy is kept for human approval
and flagged, not automatically declared relevant. Failed optional revisions keep
the original candidate held for review. No uncontrolled retry loop is introduced.

`quality_flags` in per-case/evaluation reports can include `copy_rewrite`,
`copy_rewrite_failed` and `historical_copy`. `generation_attempts` counts generation
calls (not the separate claim check). Non-authored model output is discarded as
`rejected_non_authored_output`. Copy detection is an exact-text signal, not a
semantic relevance judgment: it can flag a genuinely appropriate reused phrase
and miss a bad paraphrase. Human quality review remains necessary.

The owner's punctuation preference is now stricter: **no reply-text punctuation
by default**, including periods, commas, question marks and apostrophes. Use
unpunctuated texting spellings; emoji remain allowed. The prompt receives a
`punctuation_policy` calculated only from the owner's safe authored outgoing
messages in the visible preceding context for this chat. A mark is allowed only
when at least three of those messages contain it and at least two thirds of those
messages use it. Incoming/contact messages, other chats, general examples and the
hidden actual reply cannot justify adding punctuation. Permission is per mark:
frequent question marks do not authorize periods or commas. Weak/sparse evidence
means no punctuation. This policy overrides general/contact profile statistics,
and the bounded copy-rewrite request follows the same rule.

The instruction applies to reply text, not JSON syntax. It is a model instruction,
not mechanical character stripping or a factual/sending approval. Profiles retain
the separate period/question/comma fractions for analysis; source messages,
approval checks, emoji and existing saved review snapshots are unchanged.

Use a **new review filename** to generate clean cases. `--resume` intentionally
shows the original frozen snapshots and ratings, including old artifacts; it does
not apply a cleanup to an existing study.

```sh
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent review-drafts \
  --database "$MESSAGES_DB" --max-messages 10000 --cases 20 \
  --ratings "$HOME/.local/state/imessage-agent/phase3a-qwen25-clean-review.sqlite3" \
  --show-text
```

Do not compare a partial old human-review mean with a fresh full-run mean as if
coverage/cases were identical. No improved owner-style score is claimed until the
owner rates fresh cases. Auto-sending and all outbound/recipient/duplicate-send
protections remain unchanged.

## Blind comparison on the same 20 saved cases

This comparison regenerates A/B/C on the **same 20 source-message and held-out
reply references already saved in your review file**. It does not select new
cases, reuse old generated outputs as A, copy ratings, update profiles or touch
outbound state. The owner's reported starting scores are style **2.0/5**, relevance
**2.1/5**, naturalness **1.9/5**, and observed invention **0/20**. These are prior
human results, not measurements of this new pipeline.

- **A**: Qwen2.5 3B with the preceding punctuation-fixed prompt/retrieval pipeline.
- **B**: Qwen2.5 3B with conversation/reply examples and the simplified prompt.
- **C**: Qwen2.5 7B with the same improved pipeline, only if that exact local tag
  is already installed. Otherwise C is reported as skipped; no download occurs.

The improved prompt replaces isolated outgoing snippets with up to five relevant
historical incoming-turn/reply pairs, each preserving chronological speaker order.
Same-contact examples take priority; three distinct relevant replies are enough
to avoid other-contact fallback. With sparse contact history, relevant general
pairs can fill the budget. Fewer than three is explicitly reported in local draft
`retrieval_summary` when the history does not contain enough distinct safe matches.
Ranking uses lexical overlap, question shape and recency, not semantic embeddings;
paraphrases with no shared keywords may still be missed.

Generation receives up to 12 exact preceding same-chat messages plus the latest
incoming turn. The unchanged punctuation instruction/policy overrides examples.
Reply generation produces only `reply` and `needs_personal_facts`; it does not
choose autonomy. Afterwards the existing deterministic/local claim validator runs.
Unknown personal facts, activities, location, availability, commitments, payments
or decisions become `NEEDS_USER_INPUT` in the improved pipeline. Useful unverified
wording can remain available to the owner; it is never treated as a factual answer
or sending permission. Uncertainty remains approval-required; sensitive and invalid
output protections remain unchanged.

Run these in your private Terminal. **Set `SOURCE_REVIEW` to the file containing
the same 20 cases you already rated.** The filename below matches the earlier
20-case review command; if you used the clean-review filename instead, use that
exact path. Do not create a fresh source review just for this comparison.

```sh
cd /path/to/imessage-agent
SOURCE_REVIEW="$HOME/.local/state/imessage-agent/phase3a-qwen25-review.sqlite3"
COMPARISON="$HOME/.local/state/imessage-agent/phase3a-abc-comparison.sqlite3"
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent compare-drafts \
  --database "/explicitly/authorized/path/chat.db" \
  --source-review "$SOURCE_REVIEW" \
  --comparison "$COMPARISON" \
  --max-messages 10000

PYTHONPATH=src /opt/homebrew/bin/python3.13 -m imessage_agent review-drafts \
  --database "/explicitly/authorized/path/chat.db" \
  --ratings "$COMPARISON" --resume --show-text
```

The preparation command uses the default 3B configuration. An optional existing
`--config` can change generation limits/temperature, but must keep `qwen2.5:3b` as
the base; C inherits those settings with only the model changed. Local installed
models are enumerated using Ollama's [GET /api/tags](https://docs.ollama.com/api/tags)
and checked for local-only inference before private inputs are submitted.

There are **40 trials** for A/B or **60** with C: 20 cases per variant. Trial
presentation is shuffled and saved. During rating, model labels, classifications,
quality flags and latency are hidden. Rate the same style/relevance/naturalness
rubric plus invented personal information (`4 5 3 n`); `s` leaves a trial pending,
`q` quits. Resume with the second command; it reads saved trials without opening
Messages or calling a model. Do not inspect the private SQLite labels before
finishing if you want to preserve blindness.

Once every available trial is rated, the final JSON reveals A/B/C with:

- Human means, rated/pending coverage, invention count/rate.
- Heuristics separately, with nonaccepted outputs still scoring zero under the
  existing conservative heuristic convention.
- Validation outcome counts, including `needs_user_input`, and generation failures.
- Wall-clock latency in milliseconds: sample count, mean, median and p95.

Latency includes retrieval/profile construction, local health checks, reply
and optional revision/claim calls, plus loading when it occurs. Variants generate
in model batches to avoid excessive switching. This is not a controlled warm-model
benchmark; loading and cache/order effects can influence timings. Failed attempts
also contribute latency samples. No automated model winner is selected. Compare
human relevance and naturalness alongside style, invention and failure coverage;
a higher acceptance rate alone is not evidence of better replies.

Inputs are frozen once for all variants. The bounded sample stops at the newest
saved target; newly appended messages cannot consume its budget. Each case then
sees only messages preceding its own target and the target itself, never its
held-out reply or future messages. Previously held-out replies from chronologically
earlier cases can be legitimate past context for later cases. Exact source
identity/generation and target/reference GUIDs, sequence numbers and texts must
still match. Changed/missing/ineligible targets fail explicitly, without replacing
cases. A reaction notification already saved as an actual reference is retained
only for display/comparison, never used as that case's generation input; such
references can make lexical heuristics especially misleading.

Both source files are read-only; output is a separate private mode-0600 SQLite
file containing plaintext context, candidates, references and ratings. Keep it
outside the repository and cloud sync. Existing comparison filenames are refused;
use a new filename to regenerate. Interrupted preparation saves completed trials;
resume can rate those, but cannot generate missing ones or declare a full study
complete. Individual parser failures are recorded without stopping other cases;
service/locality failures abort further generation. No cloud service or message
transport is involved. No quality improvement is claimed until you complete this
local comparison.
