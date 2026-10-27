"""Local dashboard orchestration; no transport or worker entry points."""
import hashlib
import json
import time
import uuid
from .account_selection import load_account_id
from .dashboard_models import Settings
from .drafting import generate, history
from .evaluation import case_history
from .contacts import Contacts
from .sessions import config_from_store
from .reply_lifecycle import fresh_reason, invalidate_stale_drafts
from .macos import MacOSMessagesReader
from .models import Recipient
from .ollama import DraftError, ModelConfig, Ollama
from .scheduling import Scheduler, make_spec


class DashboardError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class DashboardService:
    def __init__(self, store, database=None):
        self.store, self.db, self.database = store, store.connection, database
        self.scheduler = Scheduler(store)
        self.names = Contacts(store)

    def preference(self, key, default):
        row = self.db.execute('SELECT value FROM dashboard_preferences WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_preference(self, key, value):
        with self.db:
            self.db.execute('INSERT INTO dashboard_preferences VALUES (?,?) ON CONFLICT(key) '
                            'DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    def settings(self):
        return Settings(**self.preference('settings', {}))

    def schedule(self, data, replaces=None):
        if data.transport == 'live' and data.account_id != load_account_id():
            raise DashboardError('selected_account_changed')
        spec = make_spec(Recipient.parse(data.recipient), data.body,
                         start_date=data.start_date, start_time=data.start_time,
                         timezone_name=data.timezone, end_time=data.end_time,
                         recurrence=data.recurrence, weekdays=data.weekdays, until=data.until,
                         fold=data.fold, grace=data.grace, transport=data.transport,
                         account_id=data.account_id)
        plan = self.scheduler.create(spec, authorized=data.confirmed, replaces=replaces,
                                     authorize_recipient=data.authorize_recipient)
        if data.recipient_name:
            with self.db:
                self.db.execute('UPDATE contacts SET display_name=? WHERE recipient_key=?',
                                (data.recipient_name, Recipient.parse(data.recipient).key))
        return self.schedule_view(plan)

    def schedule_view(self, plan_id):
        plan = self.scheduler.details(plan_id)
        return {**plan, 'display_name': self.names.resolve(plan['spec']['recipient'])['display_name']}

    def occurrences(self, plan_id=None):
        plans = {p['id']: self.scheduler.details(p['id']) for p in self.scheduler.plans()}
        return [{**row, 'recipient': plans[row['plan_id']]['spec']['recipient'],
                 'display_name': self.names.resolve(plans[row['plan_id']]['spec']['recipient'])['display_name']}
                for row in self.scheduler.history(plan_id)]

    def draft(self, draft_id):
        row = self.db.execute('SELECT * FROM reply_drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise DashboardError('draft_not_found', 404)
        value = json.loads(row['draft_json'])
        return row, value

    def draft_view(self, row, value):
        recipient = Recipient.parse(row['recipient_key'].split(':', 1)[1])
        if recipient.key != row['recipient_key']:
            raise DashboardError('invalid_draft_recipient')
        context = value.get('historical_context', []) + value.get('context', [])
        if not context:
            guids = list(dict.fromkeys(value.get('context_guids', []) + [row['source_guid']]))
            for guid in guids:
                message = self.db.execute('SELECT direction,body,occurred_at FROM messages WHERE source_id=? AND guid=?',
                                          (row['source_id'], guid)).fetchone()
                if message:
                    context.append(dict(message))
        approval_id = value.get('dashboard_approval_id')
        status = value.get('dashboard_status', 'draft')
        if status == 'approved':
            approval = self.store.approval(approval_id)
            if not approval or approval.expires_at <= time.time() or approval.consumed_by:
                status = 'expired_or_used'
        return dict(id=row['id'], recipient=recipient.address, recipient_key=recipient.key,
                    display_name=self.names.resolve(recipient.address)['display_name'],
                    conversation_state=value.get('conversation_state', 'UNCERTAIN'),
                    body=value.get('reply', ''), reason=value.get('reason', 'manual_review_required'),
                    status=status, revision=hashlib.sha256(row['draft_json'].encode()).hexdigest(),
                    created_at=row['created_at'], context=context, approval_id=approval_id)

    def drafts(self):
        invalidate_stale_drafts(self.store, database=self.database)
        return [self.draft_view(row, json.loads(row['draft_json'])) for row in
                self.db.execute('SELECT * FROM reply_drafts ORDER BY created_at DESC')]

    def change_draft(self, draft_id, revision, action, *, body=None, recipient=None, generated=None):
        invalidate_stale_drafts(self.store)
        if action in ('approve', 'submit'):
            self.check_fresh(draft_id)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row, value = self.draft(draft_id)
            view = self.draft_view(row, value)
            if action not in ('discard', 'reject') and view['status'] == 'cancelled':
                raise DashboardError('stale_draft_requires_new_source')
            if revision != view['revision']:
                raise DashboardError('draft_changed_refresh_before_review')
            if action == 'approve':
                if view['status'] != 'pending' or body != view['body'] or recipient != view['recipient']:
                    raise DashboardError('exact_pending_payload_required')
                if not body.strip():
                    raise DashboardError('empty_draft')
                contact = self.store.contact(Recipient.parse(recipient))
                if not contact or not contact.outbound_allowed:
                    raise DashboardError('recipient_not_allowlisted')
            elif action == 'submit' and (not view['body'].strip() or view['status'] == 'discarded'):
                raise DashboardError('nonempty_active_draft_required')
            previous = value.pop('dashboard_approval_id', None)
            if previous:
                self.db.execute('UPDATE approvals SET expires_at=0 WHERE id=? AND consumed_by IS NULL', (previous,))
            if generated is not None:
                for key in ('context', 'historical_context', 'context_guids', 'historical_guids', 'example_guids',
                            'style_guids', 'conversation_state', 'conversation_reason', 'starts_new_session',
                            'references_earlier', 'session_config'):
                    if key in generated:
                        value[key] = generated[key]
            if action == 'edit':
                value.update(reply=body, dashboard_status='draft', reason='edited_requires_fresh_approval',
                             validation_status='approval_required', classification='APPROVAL_REQUIRED', auto_sending=False)
            else:
                value['dashboard_status'] = {'submit': 'pending', 'approve': 'approved',
                                             'reject': 'rejected', 'discard': 'discarded'}[action]
            if action == 'approve':
                approval_id = uuid.uuid4().hex
                value['dashboard_approval_id'] = approval_id
                # Save the review state before Store.approve commits the enclosing transaction.
                self.db.execute('UPDATE reply_drafts SET draft_json=? WHERE id=?', (json.dumps(value), draft_id))
                self.store.approve(approval_id, Recipient.parse(recipient), body, time.time() + 86400)
            else:
                self.db.execute('UPDATE reply_drafts SET draft_json=? WHERE id=?', (json.dumps(value), draft_id))
        row, value = self.draft(draft_id)
        return self.draft_view(row, value)

    def session_config(self):
        return config_from_store(self.store)

    def check_fresh(self, draft_id):
        row, value = self.draft(draft_id)
        if value.get('dashboard_status') == 'cancelled':
            raise DashboardError('stale_draft_requires_new_source')
        if self.database is None:
            # Legacy synthetic/manual drafts remain reviewable; generated drafts require source evidence.
            if value.get('session_config'):
                raise DashboardError('explicit_messages_database_required_for_freshness')
            return
        with MacOSMessagesReader(self.database) as reader:
            if reader.source_id != row['source_id'] or reader.generation != row['generation']:
                raise DashboardError('draft_source_changed')
            batch = reader.read_after(row['source_sequence'] - 1, 1)
            if not batch.messages or batch.messages[0].guid != row['source_guid']:
                raise DashboardError('draft_source_changed')
            reason = fresh_reason(reader, batch.messages[0], config=self.session_config())
            if reason:
                raise DashboardError(reason)

    def regenerate(self, draft_id, revision):
        self.check_fresh(draft_id)
        row, value = self.draft(draft_id)
        if self.draft_view(row, value)['revision'] != revision:
            raise DashboardError('draft_changed_refresh_before_review')
        if self.database is None:
            raise DashboardError('explicit_messages_database_required_for_regeneration')
        # Reuse the verified individual-chat resolver; never infer membership from sender names.
        with MacOSMessagesReader(self.database) as reader:
            if reader.source_id != row['source_id'] or reader.generation != row['generation']:
                raise DashboardError('draft_source_changed')
            chats = reader.individual_chats()
            batch = reader.read_after(row['source_sequence'] - 1, 1)
            if not batch.messages or batch.messages[0].guid != row['source_guid']:
                raise DashboardError('draft_source_changed')
            target = batch.messages[0]
            if target.chat_id not in chats or chats[target.chat_id].key != row['recipient_key']:
                raise DashboardError('individual_recipient_not_verified')
            rows, _ = history(reader, chats, before=target.sequence, context_chat=target.chat_id)
            rows = case_history(rows, target, reader)
            generated = generate(rows, chats, target, Ollama(ModelConfig(model=self.settings().model)),
                                 session_config=self.session_config(), now=time.time())
            reason = fresh_reason(reader, target, config=self.session_config())
            if reason:
                raise DashboardError(reason)
        if not generated['reply'].strip():
            raise DashboardError('model_returned_no_draft')
        # Compare again after a slow model call; never overwrite a concurrent human edit.
        return self.change_draft(draft_id, revision, 'edit', body=generated['reply'], generated=generated)

    def contacts(self, query=''):
        known = {row['recipient_key']: dict(row) for row in self.db.execute('SELECT * FROM contacts')}
        keys = self.db.execute("SELECT sender_key FROM messages WHERE sender_key IS NOT NULL UNION "
                               "SELECT recipient_key FROM reply_drafts UNION "
                               "SELECT profile_key FROM style_profiles WHERE profile_key!='general'")
        for row in keys:
            key = row[0]
            recipient = Recipient.parse(key.split(':', 1)[1])
            if key != recipient.key:
                raise DashboardError('invalid_contact_identity')
            known.setdefault(key, dict(recipient_key=key, kind=recipient.kind, address=recipient.address,
                                       display_name=recipient.address, outbound_allowed=False))
        for row in known.values():
            resolved = self.names.resolve(row['address'])
            if resolved['resolution'] == 'resolved':
                row.update(display_name=resolved['display_name'])
        results = []
        for row in sorted(known.values(), key=lambda c: (c['display_name'], c['recipient_key'])):
            if query.casefold() not in (row['display_name'] + ' ' + row['address']).casefold():
                continue
            profiles = [json.loads(p[0]) for p in self.db.execute(
                'SELECT profile_json FROM style_profiles WHERE profile_key=?', (row['recipient_key'],))]
            results.append({**row, 'outbound_allowed': bool(row['outbound_allowed']),
                            'handles': self.names.resolve(row['address'])['handles'], 'profiles': profiles,
                            'future_auto_reply': self.preference('contact:' + row['recipient_key'], 'disabled')})
        return results

    def attempts(self):
        return [dict(id=row['id'], recipient=row['address'], display_name=self.names.resolve(row['address'])['display_name'],
                     status=row['status'], created_at=row['created_at'])
                for row in self.db.execute('SELECT id,address,status,created_at FROM send_attempts ORDER BY created_at DESC')]
