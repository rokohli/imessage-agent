"""Synthetic endpoint and authorization checks; no Messages, model or transport access."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from imessage_agent.dashboard import create_app
from imessage_agent.drafting import save_draft
from imessage_agent.models import Contact, Recipient, payload_hash
from imessage_agent.storage import Store, _SCHEMA, _OUTBOUND_SCHEMA, _DRAFT_SCHEMA, _SCHEDULER_SCHEMA
from imessage_agent.account_selection import load_account_id, save_account_id


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.recipient = Recipient.parse('+12025550101')
        self.store.put_contact(Contact(self.recipient, 'Synthetic Person', True))
        self.draft_id = save_draft(self.store, 'synthetic', 'generation', dict(
            source_guid='incoming', source_sequence=1, recipient_key=self.recipient.key,
            reply='Synthetic draft', reason='preview_only', auto_sending=False))
        self.client = TestClient(create_app(self.path), base_url='http://127.0.0.1:8000')
        token = self.client.get('/api/session').json()['token']
        self.headers = {'Origin': 'http://127.0.0.1:5173', 'X-Dashboard-Token': token}
        self.no_transport = patch('imessage_agent.applescript._execute', side_effect=AssertionError('real transport forbidden'))
        self.no_transport.start()

    def tearDown(self):
        self.no_transport.stop()
        self.client.close()
        self.store.close()
        self.directory.cleanup()

    def write(self, path, data=None, method='post'):
        return self.client.request(method.upper(), '/api' + path, json=data or {}, headers=self.headers)

    def draft(self):
        return self.client.get('/api/drafts').json()[0]

    def spec(self, **changes):
        data = dict(recipient=self.recipient.address, body='Synthetic schedule', start_date='2099-10-27',
                    start_time='13:00', timezone='UTC', confirmed=True)
        data.update(changes)
        return data

    def test_overview_contacts_settings_empty_history(self):
        self.assertEqual(self.client.get('/api/summary').json()['saved_drafts'], 1)
        contact = self.client.get('/api/contacts?q=synthetic').json()[0]
        self.assertEqual(contact['handles'], [self.recipient.address])
        self.assertEqual(self.client.get('/api/contacts?q=absent').json(), [])
        self.assertEqual(self.client.get('/api/attempts').json(), [])
        self.assertEqual(self.client.get('/api/approvals').json(), [])
        response = self.client.get('/api/settings')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn(str(self.path), response.text)

    def test_security_denies_host_origin_missing_token_and_form_posts(self):
        self.assertEqual(self.client.get('/api/drafts', headers={'Host': 'evil.test'}).status_code, 403)
        for origin in ('http://evil.test', 'null', 'http://127.0.0.1:5174'):
            self.assertEqual(self.client.get('/api/drafts', headers={'Origin': origin}).status_code, 403)
        self.assertEqual(self.client.post('/api/schedules', json=self.spec()).status_code, 403)
        self.assertEqual(self.client.post('/api/schedules', json=self.spec(), headers={'Origin': self.headers['Origin']}).status_code, 403)
        self.assertEqual(self.client.post('/api/schedules', data={'body': 'text'}, headers=self.headers).status_code, 403)
        self.assertEqual(self.client.get('/api/drafts', headers={'Origin': self.headers['Origin']}).headers['access-control-allow-origin'], self.headers['Origin'])
        self.assertEqual(self.client.options('/api/settings', headers={'Origin': self.headers['Origin'], 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'X-Dashboard-Token,Content-Type'}).status_code, 200)

    def test_schedule_crud_and_history_uses_existing_scheduler(self):
        response = self.write('/schedules', self.spec(end_time='14:00', recurrence='daily'))
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()
        self.assertEqual(plan['status'], 'active')
        history = self.client.get(f"/api/schedules/{plan['id']}/occurrences").json()
        # Far future recurring plans materialize only when the worker reaches the horizon.
        self.assertEqual(history, [])
        replacement = self.write('/schedules/' + plan['id'], self.spec(), method='put').json()
        self.assertEqual(replacement['replaces'], plan['id'])
        self.assertEqual(self.client.get('/api/schedules/' + plan['id']).json()['status'], 'cancelled')
        history = self.client.get(f"/api/schedules/{replacement['id']}/occurrences").json()
        self.assertEqual(history[0]['send_at'], history[0]['window_start'])
        self.assertEqual(self.write('/schedules/' + replacement['id'], method='delete').status_code, 200)
        self.assertEqual(self.client.get('/api/occurrences').json()[0]['status'], 'cancelled')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_schedule_validation_and_no_second_approval(self):
        invalid = [dict(confirmed=False), dict(confirmed=1), dict(confirmed='true'), dict(recipient='Person'), dict(body=' '),
                   dict(start_date='bad'), dict(start_date='2001-01-01'), dict(timezone='Fake/Zone'),
                   dict(recurrence='weekdays'), dict(weekdays=[True], recurrence='weekdays'),
                   dict(end_time='12:00'), dict(grace=-1), dict(transport='live'), dict(sql='DELETE')]
        for changes in invalid:
            with self.subTest(changes=changes):
                response = self.write('/schedules', self.spec(**changes))
                self.assertGreaterEqual(response.status_code, 400, response.text)
                self.assertNotIn('Synthetic schedule', response.text)
        self.assertEqual(self.write('/schedules', self.spec()).status_code, 200)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)

    def test_allowlist_explicit_authorization(self):
        other = '+12025550102'
        self.assertEqual(self.write('/schedules', self.spec(recipient=other)).status_code, 422)
        self.assertEqual(self.write('/schedules', self.spec(recipient=other, authorize_recipient=True)).status_code, 200)
        self.assertTrue(self.store.contact(Recipient.parse(other)).outbound_allowed)

    def test_all_recurrence_modes_and_dst_boundaries(self):
        for recurrence in ('once', 'daily', 'weekly', 'weekdays'):
            response = self.write('/schedules', self.spec(recurrence=recurrence, weekdays=[0, 2] if recurrence == 'weekdays' else []))
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.write('/schedules', self.spec(start_date='2099-03-08', start_time='02:30', timezone='America/Chicago')).status_code, 422)

    def test_live_schedule_pins_existing_account(self):
        with patch('imessage_agent.dashboard_service.load_account_id', return_value='synthetic-account'):
            self.assertEqual(self.write('/schedules', self.spec(transport='live', account_id='changed')).status_code, 409)
            self.assertEqual(self.write('/schedules', self.spec(transport='live', account_id='synthetic-account')).status_code, 200)

    def submit(self):
        draft = self.draft()
        response = self.write(f'/drafts/{self.draft_id}/submit', {'revision': draft['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def approve(self, draft):
        return self.write(f'/approvals/{self.draft_id}/approve', dict(revision=draft['revision'],
                          recipient=draft['recipient'], body=draft['body'], confirmed=True))

    def test_exact_draft_approval_edit_revokes_and_resubmit(self):
        pending = self.submit()
        self.assertEqual(len(self.client.get('/api/approvals').json()), 1)
        approved = self.approve(pending)
        self.assertEqual(approved.status_code, 200, approved.text)
        approved = approved.json()
        permit = self.store.approval(approved['approval_id'])
        self.assertEqual(permit.payload_hash, payload_hash(self.recipient, pending['body']))
        edited = self.write('/drafts/' + self.draft_id, dict(revision=approved['revision'], body='Edited synthetic draft'), 'put')
        self.assertEqual(edited.json()['status'], 'draft')
        self.assertEqual(self.store.approval(permit.id).expires_at, 0)
        self.assertEqual(self.approve(pending).status_code, 409)
        resubmitted = self.submit()
        self.assertEqual(self.approve(resubmitted).status_code, 200)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_reject_discard_and_missing_drafts(self):
        pending = self.submit()
        rejected = self.write(f'/approvals/{self.draft_id}/reject', {'revision': pending['revision']}).json()
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(self.write(f'/drafts/{self.draft_id}/discard', {'revision': rejected['revision']}).status_code, 200)
        self.assertEqual(self.client.get('/api/drafts').json(), [])
        self.assertEqual(self.write('/drafts/missing/submit', {'revision': 'missing'}).status_code, 404)

    def test_approval_binds_recipient_body_and_revision(self):
        pending = self.submit()
        for field, value in [('body', 'different'), ('recipient', '+12025550102'), ('revision', 'stale')]:
            payload = dict(revision=pending['revision'], recipient=pending['recipient'], body=pending['body'], confirmed=True)
            payload[field] = value
            self.assertEqual(self.write(f'/approvals/{self.draft_id}/approve', payload).status_code, 409)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)

    def test_edit_validation_and_regenerate_requires_verified_source(self):
        draft = self.draft()
        for body in (' ', 'x' * 4001):
            self.assertEqual(self.write('/drafts/' + self.draft_id, {'revision': draft['revision'], 'body': body}, 'put').status_code, 422)
        self.assertEqual(self.write(f'/drafts/{self.draft_id}/regenerate', {'revision': draft['revision']}).status_code, 409)
        self.assertEqual(self.draft(), draft)

    def test_model_failure_does_not_change_draft(self):
        from imessage_agent.dashboard_service import DashboardService
        from imessage_agent.ollama import DraftError
        draft = self.draft()
        with patch.object(DashboardService, 'regenerate', side_effect=DraftError('ollama_unavailable_start_loopback_server_and_install_model')):
            result = self.write(f'/drafts/{self.draft_id}/regenerate', {'revision': draft['revision']})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.draft(), draft)

    def test_preferences_validate_and_persist_without_enabling_replies(self):
        for data in (dict(model='cloud-model'), dict(timezone='Fake/Zone'), dict(grace=-1), dict(auto_reply=True)):
            self.assertEqual(self.write('/settings', data, 'put').status_code, 422)
        self.assertEqual(self.write('/settings', dict(model='qwen2.5:3b', timezone='UTC', grace=600, notifications=True), 'put').status_code, 200)
        self.assertEqual(self.client.get('/api/settings').json()['timezone'], 'UTC')
        path = '/contacts/' + self.recipient.key + '/preferences'
        self.assertEqual(self.write(path, {'future_auto_reply': 'ask_first'}, 'put').status_code, 200)
        self.assertEqual(self.write(path, {'future_auto_reply': 'enabled'}, 'put').status_code, 422)

    def test_health_is_content_free_and_no_send_or_retry_endpoint(self):
        with patch('imessage_agent.dashboard.load_account_id', side_effect=__import__('imessage_agent.account_selection', fromlist=['AccountSelectionError']).AccountSelectionError('account_selection_required')), patch('imessage_agent.dashboard.Ollama.health', return_value={'status': 'ready'}):
            health = self.client.get('/api/health')
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()['auto_reply'])
        self.assertNotIn(str(self.path), health.text)
        for endpoint in ('/send', '/retry', '/scheduler-run', '/sql'):
            self.assertEqual(self.write(endpoint).status_code, 404)

    def test_v4_upgrade_retains_approvals_unknown_and_drafts(self):
        legacy = Path(self.directory.name) / 'v4.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.executescript(_SCHEMA + _OUTBOUND_SCHEMA + _DRAFT_SCHEMA + _SCHEDULER_SCHEMA)
            db.execute('INSERT INTO approvals VALUES (?,?,?,NULL)', ('old', 'hash', 123))
        legacy.chmod(0o600)
        upgraded = Store(legacy)
        try:
            self.assertEqual(upgraded.connection.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(upgraded.approval('old').payload_hash, 'hash')
            self.assertEqual(upgraded.connection.execute('SELECT COUNT(*) FROM dashboard_preferences').fetchone()[0], 0)
        finally:
            upgraded.close()

    def test_regeneration_revalidates_source_and_revokes_previous_approval(self):
        from types import SimpleNamespace
        pending = self.submit()
        approved = self.approve(pending).json()
        client = TestClient(create_app(self.path, database=Path(self.directory.name) / 'synthetic-source.sqlite3'),
                            base_url='http://127.0.0.1:8000')
        headers = {'Origin': self.headers['Origin'], 'X-Dashboard-Token': client.get('/api/session').json()['token']}
        target = SimpleNamespace(guid='incoming', sequence=1, chat_id='individual', occurred_at=1)
        with patch('imessage_agent.dashboard_service.MacOSMessagesReader') as reader, patch(
            'imessage_agent.dashboard_service.history', return_value=([], {})), patch(
            'imessage_agent.dashboard_service.case_history', return_value=[]), patch(
            'imessage_agent.dashboard_service.generate', return_value={'reply': 'Regenerated synthetic draft'}) as generate:
            source = reader.return_value.__enter__.return_value
            source.source_id, source.generation = 'synthetic', 'generation'
            source.individual_chats.return_value = {'individual': self.recipient}
            source.read_after.return_value = SimpleNamespace(messages=[target])
            source.read_before.return_value = SimpleNamespace(source_id='synthetic', generation='generation', messages=[target])
            response = client.post(f'/api/drafts/{self.draft_id}/regenerate', json={'revision': approved['revision']}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], 'draft')
            self.assertEqual(response.json()['body'], 'Regenerated synthetic draft')
            self.assertEqual(self.store.approval(approved['approval_id']).expires_at, 0)
            self.assertEqual(generate.call_count, 1)
        client.close()
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_session_preview_survives_without_ingested_message_rows(self):
        draft_id = save_draft(self.store, 'synthetic', 'generation', dict(
            source_guid='not-ingested', source_sequence=9, recipient_key=self.recipient.key,
            reply='', reason='ending_acknowledgment', conversation_state='LIKELY_ENDED',
            context=[dict(direction='inbound', body='thanks', occurred_at=100,
                          timestamp='2026-10-27T10:00:00-05:00', session_start=True)],
            historical_context=[dict(direction='outbound', body='old plans', historical=True, occurred_at=1)]))
        view = next(d for d in self.client.get('/api/drafts').json() if d['id'] == draft_id)
        self.assertEqual(view['conversation_state'], 'LIKELY_ENDED')
        self.assertEqual(view['context'][-1]['timestamp'], '2026-10-27T10:00:00-05:00')
        self.assertTrue(view['context'][0]['historical'])
        self.assertEqual(view['reason'], 'ending_acknowledgment')

    def test_recipient_search_is_explicit_and_has_no_import_or_authorization_effect(self):
        result = dict(status='authorized', matches=[dict(display_name='Another Alice',
            recipient='+12025550102', recipient_key='phone:+12025550102')])
        with patch('imessage_agent.contacts.Contacts.search', return_value=result) as search:
            self.assertEqual(self.client.post('/api/recipients/search', json={'query': 'Alice'}).status_code, 403)
            search.assert_not_called()
            self.assertEqual(self.write('/recipients/search', {'query': 'A'}).status_code, 422)
            search.assert_not_called()
            response = self.write('/recipients/search', {'query': 'Alice'})
            self.assertEqual(response.json(), result)
            search.assert_called_once_with('Alice')
        self.assertIsNone(self.store.contact(Recipient.parse('+12025550102')))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)
        self.assertEqual(self.write('/contacts/refresh').status_code, 404)

    def test_only_confirmed_schedule_saves_chosen_name_and_canonical_recipient(self):
        data = self.spec(recipient='+12025550102', recipient_name='Chosen Alice')
        self.assertEqual(self.write('/schedules', data).status_code, 422)
        self.assertIsNone(self.store.contact(Recipient.parse(data['recipient'])))
        data['authorize_recipient'] = True
        response = self.write('/schedules', data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['spec']['recipient'], '+12025550102')
        self.assertEqual(response.json()['display_name'], 'Chosen Alice')
        self.assertTrue(self.store.contact(Recipient.parse(data['recipient'])).outbound_allowed)
        self.assertEqual(self.write('/schedules', self.spec(recipient='Chosen Alice',
            recipient_name='Chosen Alice')).status_code, 422)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_conversation_thresholds_are_validated_and_never_enable_autoreplies(self):
        self.assertEqual(self.client.get('/api/conversation-thresholds').json()['burst_seconds'], 15)
        self.assertEqual(self.write('/conversation-thresholds', {'gap_seconds': 5000.0,
            'day_gap_seconds': 1000.0, 'burst_seconds': 20.0}, 'put').status_code, 200)
        self.assertEqual(self.write('/conversation-thresholds', {'gap_seconds': -1.0}, 'put').status_code, 422)
        self.assertEqual(self.write('/conversation-thresholds', {'burst_seconds': 'Infinity'}, 'put').status_code, 422)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_account_selection_binds_exact_reviewed_id(self):
        with patch('imessage_agent.dashboard.list_imessage_accounts', return_value=['synthetic-account']), patch(
            'imessage_agent.dashboard.diagnose_applescript', return_value={'accounts': 1, 'imessage_accounts': 1}), patch(
            'imessage_agent.dashboard.save_account_id') as save:
            self.assertEqual(self.write('/accounts', {'account_id': 'stale-account', 'confirmed': True}, 'put').status_code, 409)
            self.assertEqual(self.write('/accounts', {'account_id': 'synthetic-account', 'confirmed': True}, 'put').status_code, 200)
            save.assert_called_once_with('synthetic-account', replace=True)

    def test_account_replacement_uses_private_existing_selection(self):
        path = Path(self.directory.name) / 'account.json'
        with patch('imessage_agent.account_selection.selection_path', return_value=path):
            save_account_id('synthetic-account')
            save_account_id('synthetic-replacement', replace=True)
            self.assertEqual(load_account_id(), 'synthetic-replacement')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
