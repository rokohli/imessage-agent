"""Local, explainable conversation heuristics; never a sending authorization."""
from dataclasses import dataclass
from datetime import datetime
import math
import json
import re
from zoneinfo import ZoneInfo
from .models import Direction


@dataclass(frozen=True)
class SessionConfig:
    timezone: str = 'America/Chicago'
    gap_seconds: float = 14400
    day_gap_seconds: float = 1800
    burst_seconds: float = 15

    def __post_init__(self):
        ZoneInfo(self.timezone)
        if any(not math.isfinite(v) or v <= 0 for v in
               (self.gap_seconds, self.day_gap_seconds, self.burst_seconds)):
            raise ValueError('invalid_session_threshold')


_END = re.compile(r"(?:ok(?:ay)?|k|got it|thanks(?: so much)?|thank you|ty|thx|"
                  r"sounds good|perfect|no worries|good ?night|bye|see you|👍|❤️|🙏)[!.,\s]*", re.I)
_REFERENCE = re.compile(r'\b(?:remember|last time|yesterday|earlier|last (?:week|night)|following up|about that)\b', re.I)
_SWITCH = re.compile(r'\b(?:anyway|by the way|btw|unrelated|different topic|speaking of)\b', re.I)
_STOP = set('a an and are as at be but do for from how i in is it me my of on or that the this to was we what with you your can did about today tomorrow tonight yesterday earlier last week night remember again still'.split())
_TOPICS = [set(s.split()) for s in ('homework assignment exam class study school',
          'dinner lunch restaurant food eat pizza', 'movie film show episode',
          'work meeting project office', 'game playoffs team match')]


def words(text):
    return set(re.findall(r'\w+', (text or '').casefold())) - _STOP


def topic_switch(previous, current):
    if _SWITCH.search(current or ''):
        return True
    left, right = words(previous), words(current)
    a = {i for i, topic in enumerate(_TOPICS) if left & topic}
    b = {i for i, topic in enumerate(_TOPICS) if right & topic}
    return bool(a and b and not a & b and not _REFERENCE.search(current or ''))


def segment(rows, target, config=SessionConfig()):
    """Sequence AND timestamp cutoffs prevent held-out/future leakage."""
    visible = {m.sequence: m for m in rows if m.chat_id == target.chat_id
               and m.sequence < target.sequence and m.occurred_at <= target.occurred_at}
    visible[target.sequence] = target
    ordered = sorted(visible.values(), key=lambda m: m.sequence)
    sessions, boundaries = [], {}
    zone = ZoneInfo(config.timezone)
    for m in ordered:
        previous = sessions[-1][-1] if sessions else None
        reason, new = 'first_observed', previous is None
        if previous:
            gap = m.occurred_at - previous.occurred_at
            day = datetime.fromtimestamp(m.occurred_at, zone).date() != datetime.fromtimestamp(previous.occurred_at, zone).date()
            continuity = bool(words(m.body) & words(previous.body))
            # Long silence outweighs lexical overlap; explicit references retrieve history separately.
            new = gap < 0 or gap >= config.gap_seconds * 6 or (
                (gap >= config.gap_seconds or day and gap >= config.day_gap_seconds)
                and (not continuity or bool(_REFERENCE.search(m.body or ''))))
            reason = 'timestamp_order_uncertain' if gap < 0 else 'elapsed_time_or_day_boundary'
        if new:
            sessions.append([])
            boundaries[m.guid] = reason
        elif topic_switch(' '.join(x.body or '' for x in sessions[-1][-4:]), m.body):
            boundaries[m.guid] = 'topic_switch'
        sessions[-1].append(m)
    current, previous = sessions[-1], sessions[:-1]
    historical = [m for session in previous for m in session]
    query = words(target.body)
    reference = bool(_REFERENCE.search(target.body or ''))
    ranked = sorted(((len(query & words(m.body)), m.sequence, m) for m in historical), reverse=True)
    relevant = [m for score, _, m in ranked if score > 0][:4]
    if reference and not relevant:
        relevant = historical[-4:]
    relevant.sort(key=lambda m: m.sequence)
    reason = boundaries.get(target.guid, 'continuing_turn')
    state = 'NEW_SESSION' if target.guid in boundaries and reason != 'topic_switch' else 'ACTIVE_CONVERSATION'
    if reason == 'topic_switch':
        state = 'TOPIC_SWITCH'
    if reason == 'timestamp_order_uncertain' or not (target.body or '').strip():
        state = 'UNCERTAIN'
    # An acknowledgment after an owner turn is likely terminal. A bare first message is ambiguous.
    if _END.fullmatch((target.body or '').strip()):
        owner_turn = any(m.direction == Direction.OUTBOUND for m in current[:-1])
        state, reason = ('LIKELY_ENDED', 'ending_acknowledgment') if owner_turn else ('UNCERTAIN', 'standalone_acknowledgment')
    return dict(current=current, previous=previous, historical=relevant, boundaries=boundaries,
                state=state, reason=reason, starts_new_session=current[0].guid == target.guid,
                references_earlier=reference, timezone=config.timezone)


def timing_reason(rows, target, *, now=None, config=SessionConfig()):
    newer = [m for m in rows if m.chat_id == target.chat_id and m.sequence > target.sequence]
    if any(m.direction == Direction.OUTBOUND for m in newer):
        return 'manual_reply_after_source'
    if newer:
        return 'newer_message_stale_draft'
    if now is not None and now - target.occurred_at < config.burst_seconds:
        return 'incoming_burst_settling'
    return None


def config_from_store(store):
    def preference(key):
        row = store.connection.execute('SELECT value FROM dashboard_preferences WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}
    return SessionConfig(timezone=preference('settings').get('timezone', 'America/Chicago'),
                         **preference('conversation:thresholds'))
