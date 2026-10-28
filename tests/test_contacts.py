"""On-demand name queries only; synthetic framework responses, never personal Contacts."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from imessage_agent.contacts import Contacts, normalize_handle, native_contacts, KEY, SCHEDULED_RECIPIENT
from imessage_agent.dashboard_service import DashboardService
from imessage_agent.models import Contact
from imessage_agent.storage import Store
from test_drafting import ALICE, BOB


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.native = Mock(return_value=dict(status='authorized', contacts=[
            dict(name='Alice', handles=['+1 (202) 555-0101', 'Alice@example.com']),
            dict(name='Alice', handles=[BOB.address])]))
        self.names = Contacts(self.store, native=self.native)

    def test_normalization_never_guesses_country_or_email_aliases(self):
        self.assertEqual(normalize_handle('+1 (202) 555-0101'), ALICE.address)
        self.assertEqual(normalize_handle('0012025550101'), ALICE.address)
        self.assertIsNone(normalize_handle('202-555-0101'))
        self.assertEqual(normalize_handle('202-555-0101', '1'), ALICE.address)
        self.assertIsNone(normalize_handle('202-555-0101 ext 3', '1'))
        self.assertEqual(normalize_handle('Alice@EXAMPLE.COM'), 'Alice@example.com')
        self.assertNotEqual(normalize_handle('alice@example.com'), normalize_handle('Alice@example.com'))

    def test_name_query_returns_explicit_choices_without_importing_or_caching(self):
        result = self.names.search('Alice')
        self.native.assert_called_once_with('search', 'Alice')
        self.assertEqual(len(result['matches']), 3)
        self.assertEqual([m['recipient'] for m in result['matches']], [ALICE.address, 'Alice@example.com', BOB.address])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM contacts').fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dashboard_preferences').fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)

    def test_ambiguous_names_and_shared_handles_are_choices_not_guesses(self):
        self.native.return_value['contacts'].append(dict(name='Different Alice', handles=[ALICE.address]))
        result = self.names.search('Alice')
        choices = [m for m in result['matches'] if m['recipient'] == ALICE.address]
        self.assertEqual(len(choices), 2)
        self.assertEqual(self.names.resolve(ALICE.address)['display_name'], ALICE.address)
        self.assertIsNone(self.store.contact(ALICE))

    def test_authorization_never_fetches_contacts_or_persists_returned_contacts(self):
        self.names.authorize(country_code='1')
        self.native.assert_called_once_with('authorize')
        preferences = list(self.store.connection.execute('SELECT key,value FROM dashboard_preferences'))
        self.assertEqual([(r['key'], json.loads(r['value'])) for r in preferences], [(KEY, dict(country_code='1'))])
        self.assertNotIn('Alice', preferences[0]['value'])

    def test_permission_denial_has_no_results_and_no_import_side_effects(self):
        for status in ('denied', 'unavailable', 'not_determined'):
            self.native.return_value = dict(status=status, contacts=[])
            self.assertEqual(self.names.search('Alice'), dict(status=status, matches=[]))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM contacts').fetchone()[0], 0)

    def test_empty_and_short_queries_never_access_contacts(self):
        for query in ('', ' ', 'A', 'x' * 101):
            with self.assertRaises(ValueError):
                self.names.search(query)
        self.native.assert_not_called()
        with self.assertRaises(ValueError):
            native_contacts('export')

    def test_saved_recipient_label_resolves_without_any_native_access(self):
        self.store.put_contact(Contact(ALICE, 'Chosen Alice', False))
        self.assertEqual(self.names.resolve(ALICE.address)['display_name'], 'Chosen Alice')
        service = DashboardService(self.store)
        service.names = self.names
        view = service.contacts()[0]
        self.assertEqual(view['display_name'], 'Chosen Alice')
        self.assertEqual(view['recipient_key'], ALICE.key)
        self.assertFalse(view['outbound_allowed'])
        self.native.assert_not_called()

    def test_placeholder_recipient_names_fall_back_to_canonical_handle(self):
        self.store.put_contact(Contact(ALICE, SCHEDULED_RECIPIENT, True))
        resolved = self.names.resolve(ALICE.address)
        self.assertEqual(resolved['display_name'], ALICE.address)
        self.assertEqual(resolved['resolution'], 'unresolved')
        self.assertEqual(resolved['handles'], [ALICE.address])
        service = DashboardService(self.store)
        service.names = self.names
        view = service.contacts()[0]
        self.assertEqual(view['display_name'], ALICE.address)

    def test_opening_state_discards_old_bulk_cache_but_keeps_authorizations(self):
        self.store.put_contact(Contact(ALICE, 'Chosen Alice', True))
        with self.store.connection:
            self.store.connection.execute('INSERT INTO dashboard_preferences VALUES (?,?)',
                ('contacts:cache', json.dumps(dict(contacts=[dict(name='Unselected Person')]))))
        another = Store(self.path)
        try:
            self.assertIsNone(another.connection.execute("SELECT value FROM dashboard_preferences WHERE key='contacts:cache'").fetchone())
            self.assertTrue(another.contact(ALICE).outbound_allowed)
        finally:
            another.close()

    def test_installer_bundles_purpose_string_and_supported_framework(self):
        from imessage_agent.contacts import install_helper
        import plistlib
        target = Path(self.temp.name) / 'Contacts.app'
        with patch('imessage_agent.contacts.sys.platform', 'darwin'), patch(
                'imessage_agent.contacts.helper_path', return_value=target), patch('subprocess.run') as run:
            install_helper()
        info = plistlib.loads((target / 'Contents/Info.plist').read_bytes())
        self.assertIn('NSContactsUsageDescription', info)
        self.assertEqual(info['CFBundleVersion'], '2')
        self.assertIn('Contacts', run.call_args_list[0].args[0])
        self.assertIn('ContactResolver', run.call_args_list[0].args[0])

    def test_failed_rebuild_keeps_existing_helper(self):
        from imessage_agent.contacts import install_helper
        import subprocess
        target = Path(self.temp.name) / 'Contacts.app'
        target.mkdir()
        marker = target / 'previous'
        marker.write_text('previous helper')
        with patch('imessage_agent.contacts.sys.platform', 'darwin'), patch(
                'imessage_agent.contacts.helper_path', return_value=target), patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'compiler')):
            with self.assertRaises(subprocess.CalledProcessError):
                install_helper(replace=True)
        self.assertEqual(marker.read_text(), 'previous helper')

    def test_missing_helper_never_prompts_or_launches(self):
        with patch('imessage_agent.contacts.helper_path', return_value=Path(self.temp.name) / 'absent'), patch('subprocess.run') as run:
            self.assertEqual(native_contacts()['status'], 'unavailable')
            run.assert_not_called()
