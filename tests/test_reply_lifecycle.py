"""Freshness and cancellation using only synthetic agent/source databases."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from imessage_agent.dashboard_service import DashboardService, DashboardError
from imessage_agent.drafting import save_draft
from imessage_agent.macos import MacOSMessagesReader
from imessage_agent.models import Batch, Direction
from imessage_agent.reply_lifecycle import fresh_reason, invalidate_stale_drafts
from imessage_agent.storage import Store
from test_drafting import ALICE, msg
from test_macos import fixture, add


class ReplyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state.db'
        self.store = Store(self.state)
        self.addCleanup(self.store.close)

    def test_worker_cancels_pending_drafts_and_revokes_unused_approval(self):
        for outgoing in (False, True):
            source = 'source-' + str(outgoing)
            target, newer = msg(1, 'hey'), msg(2, 'already answered' if outgoing else 'another question', outgoing)
            self.store.ingest(Batch(source, 'one', (target, newer)), None)
            draft_id = save_draft(self.store, source, 'one', dict(source_guid=target.guid, source_sequence=1,
                recipient_key=ALICE.key, reply='hey', reason='preview_only', dashboard_status='approved',
                dashboard_approval_id=source))
            self.store.approve(source, ALICE, 'hey', 9999999999)
            invalidate_stale_drafts(self.store)
            draft = json.loads(self.store.connection.execute('SELECT draft_json FROM reply_drafts WHERE id=?', (draft_id,)).fetchone()[0])
            self.assertEqual(draft['dashboard_status'], 'cancelled')
            self.assertEqual(draft['reply'], '')
            self.assertEqual(draft['reason'], 'manual_reply_after_source' if outgoing else 'newer_message_stale_draft')
            self.assertEqual(self.store.approval(source).expires_at, 0)
            self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)
            with self.assertRaises(DashboardError):
                DashboardService(self.store).check_fresh(draft_id)

    def synthetic_source(self):
        path = Path(self.temp.name) / 'source.db'
        db = fixture(path)
        self.addCleanup(db.close)
        db.execute('CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)')
        db.execute('INSERT INTO chat_handle_join VALUES (1,1)')
        add(db, 1, text='hey')
        return path, db

    def test_manual_reply_during_slow_generation_never_overwrites_draft(self):
        path, db = self.synthetic_source()
        with MacOSMessagesReader(path) as reader:
            target = reader.read_after(0, 1).messages[0]
            draft_id = save_draft(self.store, reader.source_id, reader.generation, dict(
                source_guid=target.guid, source_sequence=target.sequence, recipient_key=ALICE.key,
                reply='original', reason='preview_only'))
        service = DashboardService(self.store, path)
        row, value = service.draft(draft_id)
        revision = service.draft_view(row, value)['revision']
        def generating(*args, **kwargs):
            add(db, 2, text='manual reply during model call', is_from_me=1)
            return dict(reply='stale AI reply')
        before = row['draft_json']
        with patch('imessage_agent.dashboard_service.generate', side_effect=generating):
            with self.assertRaisesRegex(DashboardError, 'manual_reply_after_source'):
                service.regenerate(draft_id, revision)
        self.assertEqual(service.draft(draft_id)[0]['draft_json'], before)
        # The existing worker can see live changes even without a separate ingestion run.
        invalidate_stale_drafts(self.store, database=path)
        self.assertEqual(service.draft(draft_id)[1]['dashboard_status'], 'cancelled')

    def test_new_incoming_makes_saved_draft_stale_before_submit(self):
        path, db = self.synthetic_source()
        with MacOSMessagesReader(path) as reader:
            target = reader.read_after(0, 1).messages[0]
            self.assertIsNone(fresh_reason(reader, target, now=target.occurred_at + 20))
            self.assertEqual(fresh_reason(reader, target, now=target.occurred_at + 5), 'incoming_burst_settling')
            draft_id = save_draft(self.store, reader.source_id, reader.generation, dict(
                source_guid=target.guid, source_sequence=target.sequence, recipient_key=ALICE.key,
                reply='original', reason='preview_only', session_config={}))
            add(db, 2, text='new topic')
            self.assertEqual(fresh_reason(reader, target), 'newer_message_stale_draft')
        service = DashboardService(self.store, path)
        row, value = service.draft(draft_id)
        revision = service.draft_view(row, value)['revision']
        with self.assertRaisesRegex(DashboardError, 'newer_message_stale_draft'):
            service.change_draft(draft_id, revision, 'submit')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)
