"""Freshness vetoes reused by previews, approvals and the Phase 6 worker."""
import json
import time
from .models import Direction
from .sessions import SessionConfig


def fresh_reason(reader, target, *, config=SessionConfig(), now=None):
    batch = reader.read_before(None, 1, chat_id=target.chat_id)
    if batch.source_id != reader.source_id or batch.generation != reader.generation:
        return 'draft_source_changed'
    if not batch.messages:
        return 'draft_source_missing'
    latest = batch.messages[-1]
    if latest.sequence > target.sequence:
        return 'manual_reply_after_source' if latest.direction == Direction.OUTBOUND else 'newer_message_stale_draft'
    if latest.guid != target.guid:
        return 'draft_source_changed'
    if (time.time() if now is None else now) - latest.occurred_at < config.burst_seconds:
        return 'incoming_burst_settling'
    return None


def invalidate_stale_drafts(store, *, database=None):
    """Stored ingestion evidence vetoes pending drafts; never schedules or retries sends."""
    db = store.connection
    for row in db.execute('SELECT * FROM reply_drafts').fetchall():
        value = json.loads(row['draft_json'])
        if value.get('dashboard_status') in ('discarded', 'rejected', 'cancelled', 'expired_or_used'):
            continue
        source = db.execute('SELECT chat_id FROM messages WHERE source_id=? AND guid=?',
                            (row['source_id'], row['source_guid'])).fetchone()
        newer = []
        if source:
            newer = db.execute('SELECT direction FROM messages WHERE source_id=? AND chat_id=? AND sequence>? '
                           'ORDER BY sequence DESC', (row['source_id'], source['chat_id'], row['source_sequence'])).fetchall()
        reason = None
        if newer:
            reason = 'manual_reply_after_source' if any(m['direction'] == 'outbound' for m in newer) else 'newer_message_stale_draft'
        if database is not None:
            from .macos import MacOSMessagesReader
            with MacOSMessagesReader(database) as reader:
                if reader.source_id != row['source_id']:
                    pass  # Other sources can still be invalidated by ingested evidence.
                elif reader.generation != row['generation']:
                    reason = 'draft_source_changed'
                else:
                    batch = reader.read_after(row['source_sequence'] - 1, 1)
                    if not batch.messages or batch.messages[0].guid != row['source_guid']:
                        reason = 'draft_source_changed'
                    else:
                        live_reason = fresh_reason(reader, batch.messages[0])
                        if live_reason != 'incoming_burst_settling':
                            reason = live_reason or reason
        if not reason:
            continue
        value.update(reply='',  classification='NO_REPLY', reason=reason, auto_sending=False, dashboard_status='cancelled')
        with db:
            if value.get('dashboard_approval_id'):
                db.execute('UPDATE approvals SET expires_at=0 WHERE id=? AND consumed_by IS NULL',
                           (value['dashboard_approval_id'],))
            db.execute('UPDATE reply_drafts SET draft_json=? WHERE id=?', (json.dumps(value), row['id']))
