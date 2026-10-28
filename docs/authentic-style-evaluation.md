# Authentic wording evaluation — 2026-10-27

Implementation and offline validation are complete. The 60-trial blind study is
prepared; **human review of the new outputs is pending**. No new style, relevance,
naturalness, slang-authenticity, formality or invention result is claimed, and no
winner is selected from lexical proxies or acceptance rates.

## Baseline and frozen cases

The existing `reply-test-1.sqlite3` review contains exactly twenty rated cases,
with verified human means matching the operator's report:

| Prior human result | Value |
| --- | ---: |
| Style similarity | 2.05/5 |
| Contextual relevance | 3.05/5 |
| Naturalness | 2.45/5 |
| Invented personal facts observed | 0/20 |

These are prior ratings, not copied ratings for regenerated variant A. The source
review ID is `2f0248b430004df6a372bbf3e4dfaeee`. Its twenty incoming/held-out references
were frozen and checked against source identity, generation, direction, timestamps
and unchanged text. The source review and Messages were opened read-only. Future
messages, future timestamps, held-out replies and later human edits do not enter
the corresponding generation prompt or claim-validation context.

The study generated twenty fresh drafts per variant with the same already-installed
local `qwen2.5:3b`, temperature 0.3, seed 42, `num_predict=160` and 8192 context size:

- **A:** existing Phase 3B prompt/retrieval single pass, including its old brevity instruction.
- **B:** observed style fingerprint and situation-matched retrieval single pass;
  the brevity target is removed, with punctuation/session rules preserved.
- **C:** the same B first pass plus experimental wording-only revision and a local
  meaning-preservation check, followed by the existing final claim validation.

This compares the combined fingerprint/prompt/retrieval change, then the additional
revision step. It does not isolate every feature as a separate causal experiment.
Live accepted-edit state is deliberately absent from historical comparison;
accepted-edit learning is tested offline and must not leak future corrections into
old cases. All three currently compute profile metadata, including fingerprint
metadata unused by A's prompt; latency is not a pre-change runtime benchmark.

## Prepared study and latency

All **60 trials completed with zero generation errors**. All drafts have
`auto_sending=false`. Variant ordering is shuffled, and labels/configuration,
validation outcomes and latency stay hidden during human review. Human reviewers
see the same timestamped evidence window for each variant of a source case.

| Per-draft wall time | A | B | C |
| --- | ---: | ---: | ---: |
| Median | 3.30 s | 5.84 s | 6.12 s |
| Mean | 3.86 s | 6.12 s | 5.99 s |
| p95, 20 samples | 7.08 s | 8.16 s | 7.86 s |

These times include profile construction, retrieval, local health checks,
generation and any copy/wording/meaning/claim review, but exclude initial database
loading and human rating. Warm model state, prompt composition and small samples
can change timing; no performance or wording-quality conclusion follows from C's
slightly lower mean than B. Latency is separate from quality scoring.

B and C each found at least three authentic examples for all twenty cases in this
study; A had one case with fewer than three. This is retrieval coverage, not proof
that the examples or resulting slang were appropriate. C applied thirteen revisions,
withheld six and skipped an empty first-pass result. These are control-flow counts,
not semantic-preservation accuracy or human quality results.

Private artifacts, not committed:

- `/private/tmp/imessage-authentic-style-comparison.sqlite3`: sixty private blind
  snapshots, mode 0600, review ID `2df7d998d7854c528eac49fa67df50e2`.
- `/private/tmp/imessage-authentic-style-profiles.json`: bounded history analysis,
  mode 0600, 6,044 eligible outgoing samples, 120 contact-specific profiles plus
  one general profile, twelve observed intent categories. Stored lists are bounded
  summaries, not a complete vocabulary census. No raw wording or recipients are
  reproduced in this report.

Temporary artifacts can be removed by the OS. They are plaintext private data and
should remain outside cloud sync and Git. No Contacts/address-book query or import
was used, and no live agent-state schema, authorization or process was changed.

## Complete the human review

From the checkout:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent review-drafts \
  --database "$HOME/Library/Messages/chat.db" \
  --ratings /private/tmp/imessage-authentic-style-comparison.sqlite3 \
  --resume --show-text
```

Resume reads only saved snapshots; it does not access Messages, run Ollama or send
anything. Rate style/relevance/naturalness 1–5 and invented facts yes/no, then
slang authenticity (1=forced/unlike owner, 5=authentic or appropriately absent) and
formality (1=very informal, 5=very formal). Formality is descriptive; making every
reply maximally informal is not the goal. Quit/resume preserves completed ratings.
The final summary reveals the variants only after all available trials are rated,
with each human dimension and coverage separate from latency and lexical proxies.

Human ratings cannot be fabricated by the implementation agent or substituted
with a same-model judge. Zero previously observed inventions is not proof of zero
new inventions. These cases were selected from actual owner replies, so no-response
situations are underrepresented; ending suppression and timing are also tested
synthetically. Real-world style quality and meaning-guard accuracy remain unmeasured.

## Verification

- Full Python suite: **397 tests passed** with IPv4/IPv6 socket creation blocked;
  local Unix socketpairs remain available to in-process dashboard tests.
- Dashboard: **24 tests passed**, TypeScript checking and production build passed.
- Generated API contracts match FastAPI OpenAPI; `git diff --check` passed.
- New regressions cover frequency/common-vs-rare evidence, owner/contact isolation,
  intent-before-contact retrieval, system/topic/session boundaries, future/held-out
  exclusion, edit acceptance/deletion, regeneration exclusion, repeated-correction
  support, unchanged punctuation authorization, meaning-review fallback and final
  claim validation, plus same-model blind A/B/C setup and expanded human ratings.

No real messages were sent, no outbound policy was relaxed, no UNKNOWN was retried,
and no new live or automated reply loop was started. The local meaning check can
err, so two-pass mode remains experimental and autonomous sending stays disabled.

## Targeted feedback follow-up — 2026-10-28

An owner-provided failure showed a contact answering an earlier owner question,
while the generated draft repeated the explanation and asked for an opinion back.
Inspection confirmed the earlier intent rule classified a trailing self-hedge as
an opinion request without considering the owner's preceding question.

The follow-up distinguishes an informational answer from new questions and social
check-ins, preserves the preceding owner question in relevant style examples, and
supplies a reaction goal for this turn type. Synthetic regressions use a fictional
calendar-tool exchange, not copied private messages. No suggested reaction from one
owner correction is installed as a universal response, and no live learned-edit
record or outbound approval is created from this feedback.

Two separate local no-send diagnostic previews were generated for the feedback
case. Both stopped asking the same opinion question back, but both were held by
the existing activity-claim reviewer. That is **not a verified wording success**;
the classifier can also be wrong, and no new human quality rating is inferred.
The later private diagnostic is `/private/tmp/imessage-answer-reaction-preview-v2.json`
(mode 0600), separate from the blind study. Neither private reply is reproduced here.
The blind-review file was checked unchanged during both diagnostics; its snapshots
and the owner's ongoing ratings were not rewritten. No model variant was revealed.

Validation for the follow-up: **403 offline Python tests passed**, including six
new answer/reaction regressions. They cover hedged answers, genuine opinion and
factual questions, social turns/check-ins, session/system boundaries, question
provenance, reaction-goal delivery, edited-example intent and unsupported prior
experience still requiring user input. No real messages were sent and no live
process, authorization or Messages data was modified. This PR remains experimental.
