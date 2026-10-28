# Authentic texting style

The goal is authentic owner wording, not shorter replies or demographic slang.
The original Phase 3B generator remains comparison A. Normal draft previews,
new human-review preparation, evaluation and dashboard regeneration use the new
single-pass fingerprint mode. Two-pass wording revision is experimental and opt-in.
All results retain `auto_sending: false`; this adds no incoming-message trigger,
transport, scheduler, or automatic follow-up loop.

## Evidence and profiles

`build-profiles` now saves a `fingerprint` alongside the existing profile fields,
for the general owner profile and each verified individual recipient. Only safe,
authored outgoing messages are counted; incoming wording, group chats, ambiguous
identities, system/reaction artifacts and unapproved AI drafts are excluded.

Profiles include observed word/form document frequencies, recognized abbreviations,
repeated two-to-four-word phrases, whole responses, capitalization counts,
punctuation and emoji/codepoint counts. Recognized short-form labels describe
forms that actually occurred; the vocabulary is never supplied as suggested slang.
Other slang and expressions remain in observed forms, phrases and exact examples.
Every frequency includes support and sample share. At least three occurrences and
2% of the sample make a pattern common; two occurrences are occasional and a single
occurrence is rare. Repetition within one message does not inflate form counts.

Profiles also retain intent-specific response frequencies and timestamped examples.
The lightweight English situations include gratitude, availability, invitations,
support, opinions, humor, greetings, acknowledgments, logistics, requests, questions
and statements. These are heuristics; ambiguous or paraphrased situations can be
misclassified. No age, demographic or personality inference is made.

Historical sampling remains bounded, with the existing sensitive-content/artifact
filters. The saved state/profile is private plaintext, not anonymized data. No
Contacts queries, address-book import or cloud processing are involved.

## Retrieval and prompting

Style examples are genuine incoming-turn/owner-response pairs. Incoming bursts
are paired only with an actual outgoing response; system, sensitive, topic and
session boundaries clear pending turns. The shared segmenter handles calendar
boundaries and configured gaps. Each example retains speaker order, timestamps
and source references; identifiers stay local and are omitted from model prompts.

Selection ranks situation first, then same contact, accepted owner edits within
that scope, frequency support, topical overlap and recency. Three-to-five diverse
pairs are supplied when available, up to five. Broad question/statement matches
need lexical support; unrelated filler is not added simply to meet a quota.
Sparse evidence is explicitly reported. Historical examples remain STYLE_EXAMPLES,
separate from CURRENT_SESSION and HISTORICAL_CONTEXT.

The prompt uses observed intent-specific response patterns plus mechanical habits,
not generic instructions to sound youthful, friendly or slangy. Rare forms and
one-off edits are evidence, not universal rules. Slang is not forced. The new
modes remove the old fewer-than-fifteen-words instruction and median-word target.
The existing validation length limit remains a safety/format check, not a style goal.
Repeated owner wording demonstrated at least three times may be reused without a
copy-rewrite merely for being familiar; factual/claim validation still runs.

The punctuation policy is unchanged: no punctuation by default. Each permitted
mark requires at least three owner-authored occurrences and two-thirds support in
visible current-session outgoing messages. General profiles, incoming messages,
style examples and approved edits cannot grant punctuation permission.

## Experimental two pass

Pass one is the fingerprint single-pass draft. Pass two changes wording using the
same evidence while preserving meaning, speaker, negation, uncertainty and claims.
It is skipped for empty or missing-facts drafts. A separate local boolean meaning
review must affirm preservation; invalid responses, unavailable review, a
missing-facts flag or a meaning change keep the first-pass text instead. The final
text then goes through the existing artifact/secret/deterministic and local claim
validation. A rewrite never authorizes sending, and new promises, availability or
personal claims still require user input/approval.

The meaning reviewer is a local model guard, not proof of semantic equivalence.
False approvals remain possible, so two pass stays experimental. Quality flags
record an applied, withheld or failed revision; the first-pass text is retained
locally for inspection. Latency includes revisions and meaning/claim checks.

Dashboard **Settings → Application preferences → Draft wording mode** selects
single pass or experimental two pass. The CLI equivalents are:

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent draft-preview \
  --database "$MESSAGES_DB" --state "$STATE" --source-sequence "$ROWID" \
  --style-mode single_pass --show-text
```

`two_pass` is also accepted by draft-preview, review-drafts and evaluate-drafts.
Existing live freshness, manual-reply, ending and burst vetoes still apply.

## Learning from edits

A manual dashboard edit saves the original AI draft, edited text, canonical
recipient, source identity/sequence, timestamped current context and edit time in
private agent-state preferences. Subsequent edits retain the original AI wording.
Regeneration is distinguished from a human edit and never becomes owner evidence.
Approving an unchanged AI draft does not make it a human style example either.

Only an explicitly approved edited payload is accepted as writing evidence.
Saving/submitting an edit is not acceptance. Multiple versions of the same source
message do not count as independent support. The original side of an accepted
correction is AI wording to replace, never owner-style evidence. Recurring
corrections need at least two independent source messages in the same intent;
contact-specific corrections take precedence. They remain situation-scoped,
with bounded fragments, rather than becoming universal substitutions.

Preview/regeneration loads only accepted edits from the matching source/generation
that precede the target timestamp and sequence. Edits to the evaluation target,
future source timestamps and later approvals are excluded. Historical A/B/C
preparation uses only the frozen historical prefix and no live edit-state file,
so future edits cannot teach a model the held-out answer.

**Settings → Learn from my draft edits** shows originals, edits and acceptance
status. Delete one entry or all entries there. Deletion removes edit-derived
preferences immediately for subsequent drafts; it does not delete Messages
history, actual outgoing texts, prior generated drafts, approvals or separate
review snapshots. There is no new schema migration or sending authorization.

## Fixed-case A/B/C review

```sh
PYTHONPATH=src .venv/bin/python -m imessage_agent compare-styles \
  --database "$MESSAGES_DB" --source-review "$SOURCE_REVIEW" \
  --comparison "$NEW_PRIVATE_COMPARISON" --max-messages 10000
PYTHONPATH=src .venv/bin/python -m imessage_agent review-drafts \
  --database "$MESSAGES_DB" --ratings "$NEW_PRIVATE_COMPARISON" \
  --resume --show-text
```

The same twenty incoming/actual references are checked against the saved source
review. A is existing Phase 3B generation, B is fingerprint/retrieval single pass,
and C adds wording revision to B. All three use exactly the same local model,
seed, temperature and generation limits. No model is downloaded or upgraded.
The sixty trials are shuffled in a separate mode-0600 file outside the checkout;
source reviews, live state and Messages are not rewritten.

Human reviewers see the same timestamped evidence for all three variants.
Pipeline/model identifiers, validation outcomes and latency stay hidden until all
available trials are rated. Review includes style, relevance and naturalness
(1–5), invented facts (yes/no), slang authenticity (1=forced/unlike owner,
5=authentic or appropriately absent) and descriptive formality (1=very informal,
5=very formal). Lower formality alone is not a win. Scores and coverage are reported
separately from latency. Lexical/style proxies are diagnostics, never the selection
criterion. No winner is reported without human review.

See [the evaluation record](authentic-style-evaluation.md) for prepared cases,
latency and remaining human work. Restart the dashboard when ready to load these
new settings/endpoints; this implementation does not restart live processes.
