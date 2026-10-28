"""Observed owner wording and situation-matched examples; no demographic style presets."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
import unicodedata
from .models import Direction
from .quality import message_artifact
from .sessions import SessionConfig, segment, topic_switch

# shortcut: English intent rules miss paraphrases; expand from reviewed cases before other languages.
_INTENTS = (
    ('check_in', r"\b(how (?:are|r) (?:you|u)|how(?:'s| is) it going|what(?:'s| is) up|hbu|wbu)\b"),
    ('gratitude', r'\b(thank\w*|thx|ty|appreciate)\b'),
    ('availability', r'\b(?:are you|you|u) (?:free|busy|available)\b|\bwhat (?:are you|r u) doing\b|\bwyd\b'),
    ('invitation', r'\b(wanna|want to|come over|join us|hang out|dinner|lunch|meet up)\b'),
    ('support', r'\b(sad|upset|stressed|sorry|rough day|failed|hurt|miss you)\b'),
    ('opinion', r'\b(what (?:do|did|would) (?:you|u) think|thoughts|opinion|ending|recommend)\b'),
    ('humor', r'\b(lol\w*|lmao\w*|haha\w*|funny|hilarious)\b|😂|🤣|💀'),
    ('greeting', r'^(hey|hi|hello|yo|sup)[!.,\s]*$'),
    ('acknowledgment', r'^(ok(?:ay)?|got it|sounds good|cool|nice|bet|yep|yeah|nah|no worries)[!.,\s]*$'),
    ('logistics', r'\b(where|when|what time|which day|how long|what place)\b'),
    ('request', r'\b(can|could|would|will) (you|u)\b|\bplease\b'),
)
_SHORT_FORMS = set('u ur r rn idk ik ikr tbh btw ngl imo imho lol lmao lmfao brb ttyl ty thx np nvm wdym wyd wya fr smh omg bc cuz cause tho pls plz yea yeah nah yep yup gonna wanna gotta dunno im thats dont cant wont its'.split())
_STOP = set('a an and are as at be but do for from how i in is it me my of on or that the this to was we what with you your'.split())


# A trailing self-hedge is not a request for the owner's opinion.
_HEDGE = re.compile(r"\b(?:i think|i guess|i(?:'m| am) not sure)\s*\?+\s*$")
_QUESTION = re.compile(r"(?:^|\n)\s*(?:what(?! a\b)|who|where|when|why|how|which)\b|"
                       r"(?:^|\n)\s*(?:do|does|did|is|are|can|could|would|will|should|have|has)\s+"
                       r"(?:you|u|i|we|they|it|that|this)\b")


def _question(text):
    text = _HEDGE.sub('', (text or '').strip().casefold().replace('’', "'"))
    return '?' in text or bool(_QUESTION.search(text))


def intent(text, previous_owner=None):
    text = _HEDGE.sub('', (text or '').strip().casefold().replace('’', "'"))
    label = next((label for label, pattern in _INTENTS if re.search(pattern, text)),
                 'question' if _question(text) else 'statement')
    if (previous_owner and _question(previous_owner) and text and not _question(text)
            and intent(previous_owner) not in ('availability', 'check_in', 'invitation', 'support')
            and label in ('statement', 'opinion', 'question', 'logistics')):
        return 'information_answer'
    return label


def current_turn(context):
    incoming, owner = [], None
    for row in reversed(context):
        if row.get('historical'):
            break
        if row.get('direction') == 'outbound':
            owner = row.get('body')
            break
        if row.get('direction') != 'inbound':
            break
        if len(incoming) < 3:
            incoming.insert(0, row.get('body') or '')
        if row.get('session_start'):
            break
    return '\n'.join(incoming), owner


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def pairs(rows, chats, *, config=SessionConfig()):
    """Authored turns only, with session/topic/time boundaries applied before pairing."""
    from .drafting import safe_text, usable
    turns, previous, owners, turn_owners, result = {}, {}, {}, {}, []
    for message in sorted(rows, key=lambda m: m.sequence):
        if not usable([message], chats):
            turns.pop(message.chat_id, None)
            previous.pop(message.chat_id, None)
            owners.pop(message.chat_id, None)
            turn_owners.pop(message.chat_id, None)
            continue
        last = previous.get(message.chat_id)
        previous[message.chat_id] = message
        if last:
            gap = message.occurred_at - last.occurred_at
            if gap < 0 or gap > config.gap_seconds or topic_switch(last.body, message.body):
                turns.pop(message.chat_id, None)
                owners.pop(message.chat_id, None)
                turn_owners.pop(message.chat_id, None)
        if not safe_text(message.body):
            turns.pop(message.chat_id, None)
            owners.pop(message.chat_id, None)
            turn_owners.pop(message.chat_id, None)
            continue
        if message.direction == Direction.INBOUND:
            if message.chat_id not in turns:
                turn_owners[message.chat_id] = owners.get(message.chat_id)
            turns.setdefault(message.chat_id, []).append(message)
            continue
        previous_owner = turn_owners.pop(message.chat_id, None)
        owners[message.chat_id] = message
        incoming = turns.pop(message.chat_id, [])[-3:]
        if not incoming:
            continue
        # Calendar-day and continuity decisions reuse the shared segmenter.
        session = segment(incoming, message, config)['current']
        if any(m.guid not in {s.guid for s in session} for m in incoming):
            continue
        owner_context = [previous_owner] if previous_owner else []
        if previous_owner and previous_owner.guid not in {m.guid for m in segment(owner_context + incoming, message, config)['current']}:
            owner_context = []
        text = '\n'.join(m.body for m in incoming)
        if not safe_text(text):
            continue
        situation = intent(text, owner_context[0].body if owner_context else None)
        if situation != 'information_answer':
            owner_context = []
        result.append(dict(incoming=text, reply=message.body, intent=situation,
            recipient_key=chats[message.chat_id].key, occurred_at=message.occurred_at,
            source_guids=[m.guid for m in owner_context + incoming] + [message.guid],
            conversation=[dict(speaker='user', text=m.body, timestamp=timestamp(m.occurred_at)) for m in owner_context]
                + [dict(speaker='contact', text=m.body, timestamp=timestamp(m.occurred_at)) for m in incoming]
                + [dict(speaker='user', text=message.body, timestamp=timestamp(message.occurred_at))],
            origin='historical_owner', session_start=timestamp((owner_context or session)[0].occurred_at)))
    return result


def _counts(counter, total, limit=25):
    return [dict(text=text, count=count, fraction=round(count / max(1, total), 4),
                 frequency='common' if count >= 3 and count / max(1, total) >= .02
                 else 'occasional' if count >= 2 else 'rare')
            for text, count in counter.most_common(limit)]


def fingerprint(rows, chats):
    from .drafting import safe_text
    outgoing = [m for m in rows if m.direction == Direction.OUTBOUND and not message_artifact(m) and safe_text(m.body)]
    forms, phrases, emoji, punctuation, responses = Counter(), Counter(), Counter(), Counter(), Counter()
    for m in outgoing:
        words = re.findall(r"[\w]+(?:['’][\w]+)*", m.body.casefold())
        forms.update(set(words))
        phrases.update({' '.join(words[i:i + n]) for n in (2, 3, 4) for i in range(len(words) - n + 1)})
        punctuation.update({c for c in m.body if unicodedata.category(c).startswith('P')})
        emoji.update({c for c in m.body if ord(c) >= 0x1F000 or c in '❤♥'})
        responses[m.body] += 1
    by_intent = defaultdict(list)
    for pair in pairs(rows, chats):
        by_intent[pair['intent']].append(pair)
    size = len(outgoing)
    return dict(sample_count=size,
        observed_forms=_counts(Counter({w: n for w, n in forms.items() if w not in _STOP}), size),
        abbreviations=_counts(Counter({w: n for w, n in forms.items() if w in _SHORT_FORMS}), size),
        expressions=_counts(Counter({p: n for p, n in phrases.items() if n >= 2}), size),
        responses=_counts(responses, size, 15), emoji=_counts(emoji, size), punctuation=_counts(punctuation, size),
        capitalization=dict(lowercase=sum(m.body == m.body.lower() for m in outgoing),
                            all_caps=sum(m.body.isupper() for m in outgoing)),
        intents={label: dict(samples=len(items),
            responses=_counts(Counter(p['reply'] for p in items), len(items), 8),
            examples=[dict(incoming=p['incoming'], reply=p['reply'], timestamp=timestamp(p['occurred_at'])) for p in items[-2:]])
            for label, items in sorted(by_intent.items())})


def examples_for(rows, target, chats, *, exclude_guids=(), accepted_edits=(), config=SessionConfig()):
    from .drafting import _words, safe_text
    prefix = [m for m in rows if m.sequence < target.sequence and m.occurred_at <= target.occurred_at]
    current = segment([m for m in prefix if m.chat_id == target.chat_id], target, config)['current']
    incoming = []
    for m in reversed(current):
        if m.direction != Direction.INBOUND or len(incoming) == 3:
            break
        incoming.insert(0, m.body or '')
    query = '\n'.join(incoming)
    prior_owner = next((m.body for m in reversed(current) if m.direction == Direction.OUTBOUND), None)
    situation, words = intent(query, prior_owner), _words(query)
    candidates = pairs(prefix, chats, config=config)
    latest_edits = {}
    for edit in accepted_edits:
        if edit.get('accepted_at') is not None and edit['accepted_at'] <= target.occurred_at:
            key = (edit['source_id'], edit['source_guid'])
            if key not in latest_edits or latest_edits[key]['accepted_at'] < edit['accepted_at']:
                latest_edits[key] = edit
    for edit in latest_edits.values():
        if (edit.get('accepted_at') is None or edit['accepted_at'] > target.occurred_at
                or edit['occurred_at'] > target.occurred_at
                or edit['source_guid'] == target.guid or not safe_text(edit['edited'])
                or edit['source_sequence'] >= target.sequence):
            continue
        context = edit.get('context', [])
        text, prior_owner = current_turn(context)
        if not safe_text(text):
            continue
        candidates.append(dict(incoming=text, reply=edit['edited'], intent=intent(text, prior_owner),
            recipient_key=edit['recipient_key'], occurred_at=edit['accepted_at'], source_guids=[],
            conversation=[dict(speaker='contact', text=text, timestamp=timestamp(edit['occurred_at'])),
                          dict(speaker='user', text=edit['edited'], timestamp=timestamp(edit['accepted_at']))],
            origin='accepted_edit', session_start=timestamp(edit['occurred_at'])))
    frequencies = Counter((p['intent'], p['reply'].casefold()) for p in candidates)
    ranked = []
    for pair in candidates:
        if set(pair['source_guids']) & set(exclude_guids):
            continue
        other = _words(pair['incoming'])
        overlap = len(words & other) / max(1, len(words | other))
        matched_intent = pair['intent'] == situation
        if not matched_intent and overlap < .25:
            continue
        if situation in ('statement', 'question') and not overlap:
            continue
        # Situation precedes contact/frequency/recency; an accepted edit wins within the same contact/situation scope.
        score = (int(matched_intent), int(pair['recipient_key'] == chats[target.chat_id].key),
                 int(pair['origin'] == 'accepted_edit'),
                 frequencies[pair['intent'], pair['reply'].casefold()], overlap, pair['occurred_at'])
        ranked.append((score, pair))
    selected, seen = [], set()
    for _, pair in sorted(ranked, key=lambda item: item[0], reverse=True):
        key = pair['reply'].casefold().strip()
        if key in seen:
            continue
        selected.append(pair)
        seen.add(key)
        if len(selected) == 5:
            break
    return selected


STYLE_INSTRUCTIONS = """Use the owner's demonstrated word choices, contractions, abbreviations,
expressions, capitalization and emoji for this conversational situation. The
fingerprint contains observed frequencies, not demographic assumptions. Rare
expressions and a single correction are examples, not universal rules. Never
invent slang or insert it where matching examples do not show it. Accepted edits
are preferred wording evidence, not evidence of current personal facts. The original
side of a correction is AI wording to replace, never evidence of owner style. Match
wording and intent; do not optimize for shorter replies. Do not copy an old
activity, promise or availability claim into the current reply.
"""


def for_prompt(data, situation):
    """Limit intent-specific wording to this situation; retain global mechanical habits."""
    return {key: data[key] for key in ('sample_count', 'capitalization', 'punctuation', 'emoji')} | {
        'situation': situation, 'observed_responses': data['intents'].get(situation, {}),
        'abbreviations': [p for p in data['abbreviations'] if p['count'] >= 2 and any(
            p['text'] in re.findall(r'\w+', response['text'].casefold())
            for response in data['intents'].get(situation, {}).get('responses', []))]}
