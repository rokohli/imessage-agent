"""Human edits require explicit acceptance; AI regeneration never teaches style."""
import json
import unittest
from unittest.mock import patch
from imessage_agent import style_edits
from imessage_agent.dashboard_service import DashboardService
import test_dashboard


class EditLearningTests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    tearDown = test_dashboard.DashboardTests.tearDown
    write = test_dashboard.DashboardTests.write
    draft = test_dashboard.DashboardTests.draft
    submit = test_dashboard.DashboardTests.submit
    approve = test_dashboard.DashboardTests.approve

    def edit(self, text):
        draft = self.draft()
        response = self.write('/drafts/' + draft['id'], dict(body=text, revision=draft['revision']), 'put')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_records_original_and_context_but_unapproved_edits_are_not_learned(self):
        self.edit('yea sounds good')
        records = self.client.get('/api/style-edits').json()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['original'], 'Synthetic draft')
        self.assertEqual(records[0]['edited'], 'yea sounds good')
        self.assertIsNone(records[0]['accepted_at'])
        self.assertEqual(style_edits.entries(self.store.connection, before=9999999999), [])
        pending = self.submit()
        self.assertIsNone(self.client.get('/api/style-edits').json()[0]['accepted_at'])
        self.assertEqual(self.approve(pending).status_code, 200)
        self.assertIsNotNone(self.client.get('/api/style-edits').json()[0]['accepted_at'])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_unchanged_ai_approval_does_not_become_owner_style(self):
        self.assertEqual(self.approve(self.submit()).status_code, 200)
        self.assertEqual(style_edits.entries(self.store.connection), [])

    def test_delete_individual_and_all_edits_preserves_authorizations(self):
        self.edit('nah thats wild')
        self.edit('yea thats wild')
        records = self.client.get('/api/style-edits').json()
        self.assertEqual(len(records), 2)
        self.assertEqual(self.client.delete('/api/style-edits/' + records[0]['id']).status_code, 403)
        self.assertEqual(self.write('/style-edits/' + records[0]['id'], method='delete').status_code, 200)
        self.assertEqual(len(self.client.get('/api/style-edits').json()), 1)
        self.assertEqual(self.write('/style-edits', method='delete').status_code, 200)
        self.assertEqual(self.client.get('/api/style-edits').json(), [])
        self.assertTrue(self.store.contact(self.recipient).outbound_allowed)

    def test_regenerated_output_is_not_recorded_as_human_and_old_edit_is_not_reaccepted(self):
        current = self.edit('owner wording')
        service = DashboardService(self.store)
        generated = service.change_draft(current['id'], current['revision'], 'edit',
            body='new AI wording', generated=dict(reply='new AI wording'))
        self.assertEqual(len(style_edits.entries(self.store.connection)), 1)
        self.assertEqual(self.approve(self.submit()).status_code, 200)
        self.assertIsNone(style_edits.entries(self.store.connection)[0]['accepted_at'])
        self.assertEqual(generated['body'], 'new AI wording')

    def test_reverting_to_original_ai_does_not_learn_that_draft(self):
        self.edit('edited human words')
        self.edit('Synthetic draft')
        self.assertEqual(self.approve(self.submit()).status_code, 200)
        self.assertEqual(style_edits.entries(self.store.connection, before=9999999999), [])

    def test_accepted_entries_are_cut_off_by_timestamp_and_source_generation(self):
        self.edit('owner words')
        self.assertEqual(self.approve(self.submit()).status_code, 200)
        edit = style_edits.entries(self.store.connection)[0]
        self.assertEqual(style_edits.entries(self.store.connection, before=edit['accepted_at'] - 1), [])
        self.assertEqual(style_edits.entries(self.store.connection, source='other'), [])
        self.assertEqual(style_edits.entries(self.store.connection, generation='changed'), [])
        self.assertEqual(len(style_edits.entries(self.store.connection, source='synthetic', generation='generation', before=edit['accepted_at'])), 1)
