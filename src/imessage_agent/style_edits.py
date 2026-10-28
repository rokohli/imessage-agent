"""Local human-edit evidence in existing private preferences; never a sending authorization."""
from collections import defaultdict
from difflib import SequenceMatcher
import json
import time
import uuid

PREFIX = 'style:edit:'


def record(db, row, draft, edited, *, now=None):
    original = draft.get('original_reply', draft.get('reply', ''))
    if original == edited:
        return None
    identifier = uuid.uuid4().hex
    context = draft.get('context', [])
    value = dict(id=identifier, draft_id=row['id'], source_id=row['source_id'], generation=row['generation'],
                 recipient_key=row['recipient_key'], source_guid=row['source_guid'],
                 source_sequence=row['source_sequence'], original=original, edited=edited,
                 context=context, occurred_at=context[-1].get('occurred_at', row['created_at']) if context else row['created_at'],
                 created_at=time.time() if now is None else now, accepted_at=None)
    db.execute('INSERT INTO dashboard_preferences VALUES (?,?)', (PREFIX + identifier, json.dumps(value)))
    return identifier


def accept(db, identifier, body, *, now=None):
    row = db.execute('SELECT value FROM dashboard_preferences WHERE key=?', (PREFIX + identifier,)).fetchone()
    if row:
        value = json.loads(row[0])
        if value['edited'] == body:
            value['accepted_at'] = time.time() if now is None else now
            db.execute('UPDATE dashboard_preferences SET value=? WHERE key=?', (json.dumps(value), PREFIX + identifier))


def entries(db, *, source=None, generation=None, before=None):
    values = [json.loads(row[0]) for row in db.execute(
        'SELECT value FROM dashboard_preferences WHERE key LIKE ? ORDER BY key', (PREFIX + '%',))]
    return [v for v in values if (source is None or v['source_id'] == source)
            and (generation is None or v['generation'] == generation)
            and (before is None or v['accepted_at'] is not None and v['accepted_at'] <= before)]


def recurring(edits):
    from .drafting import safe_text
    from .style import intent, current_turn
    changes = defaultdict(set)
    for edit in edits:
        if edit.get('accepted_at') is None or not safe_text(edit['original']) or not safe_text(edit['edited']):
            continue
        left, right = edit['original'].split(), edit['edited'].split()
        for op, a, b, c, d in SequenceMatcher(None, left, right).get_opcodes():
            if op == 'equal':
                continue
            key = (' '.join(left[a:b]), ' '.join(right[c:d]), intent(*current_turn(edit['context'])))
            if max(map(len, key[:2])) > 120:
                continue
            changes[key].add((edit['source_id'], edit['source_guid']))

    return [dict(original=a, edited=b, support=len(sources), intents={situation: len(sources)})
            for (a, b, situation), sources in sorted(changes.items(), key=lambda item: len(item[1]), reverse=True)
            if len(sources) >= 2][:12]


def delete(db, identifier=None):
    if identifier is None:
        db.execute('DELETE FROM dashboard_preferences WHERE key LIKE ?', (PREFIX + '%',))
    else:
        db.execute('DELETE FROM dashboard_preferences WHERE key=?', (PREFIX + identifier,))
