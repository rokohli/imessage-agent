"""Synthetic Contacts framework responses. Never requests personal permission."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from imessage_agent.contacts import Contacts, normalize_handle, native_contacts, KEY
from imessage_agent.dashboard_service import DashboardService
from imessage_agent.models import Contact
from imessage_agent.storage import Store
from test_drafting import ALICE, BOB


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.now = 100
        self.native = Mock(return_value=dict(status='authorized', contacts=[
            dict(id='one', name='Alice', handles=['+1 (202) 555-0101', 'Alice@example.com']),
            dict(id='two', name='Bob', handles=[BOB.address])]))
        self.names = Contacts(self.store, native=self.native, clock=lambda: self.now)

    def test_normalization_never_merges_country_suffixes_or_email_local_parts(self):
        self.assertEqual(normalize_handle('+1 (202) 555-0101'), ALICE.address)
        self.assertEqual(normalize_handle('0012025550101'), ALICE.address)
        self.assertIsNone(normalize_handle('202-555-0101'))
        self.assertEqual(normalize_handle('202-555-0101', '1'), ALICE.address)
        self.assertIsNone(normalize_handle('202-555-0101 ext 3', '1'))
        self.assertEqual(normalize_handle('Alice@EXAMPLE.COM'), 'Alice@example.com')
        self.assertNotEqual(normalize_handle('alice@example.com'), normalize_handle('Alice@example.com'))

    def test_multiple_handles_share_a_name_but_not_a_send_identity(self):
        self.names.refresh()
        phone, email = self.names.resolve(ALICE.address), self.names.resolve('Alice@example.com')
        self.assertEqual(phone['display_name'], email['display_name'])
        self.assertEqual(len(phone['handles']), 2)
        self.assertEqual(ALICE.key, 'phone:+12025550101')
        self.assertIsNone(self.store.contact(ALICE))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)

    def test_duplicate_names_are_disambiguated_by_handle(self):
        self.native.return_value['contacts'][1]['name'] = 'Alice'
        self.names.refresh()
        self.assertEqual(self.names.resolve(ALICE.address)['display_name'], 'Alice · ' + ALICE.address)
        self.assertNotEqual(self.names.resolve(ALICE.address)['display_name'], self.names.resolve(BOB.address)['display_name'])

    def test_same_handle_on_multiple_contacts_falls_back_without_guessing(self):
        self.native.return_value['contacts'][1]['handles'].append(ALICE.address)
        self.names.refresh()
        self.assertEqual(self.names.resolve(ALICE.address), dict(display_name=ALICE.address, handles=[ALICE.address], resolution='ambiguous'))

    def test_denial_and_unavailability_clear_previous_cache(self):
        self.names.refresh()
        for status in ('denied', 'unavailable', 'not_determined'):
            self.native.return_value = dict(status=status, contacts=[])
            names = Contacts(self.store, native=self.native)
            self.assertEqual(names.resolve(ALICE.address)['display_name'], ALICE.address)
            self.assertIsNone(self.store.connection.execute('SELECT value FROM dashboard_preferences WHERE key=?', (KEY,)).fetchone())

    def test_authorization_is_explicit_refresh_and_invalidation_are_local(self):
        self.names.refresh(authorize=True)
        self.assertEqual([c.args[0] for c in self.native.call_args_list], ['authorize', 'export'])
        self.native.reset_mock()
        self.names.resolve(ALICE.address)
        self.assertFalse(self.native.called)
        self.names.invalidate()
        self.assertEqual(self.names.resolve(ALICE.address)['display_name'], ALICE.address)
        self.assertFalse(self.native.called)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM send_attempts').fetchone()[0], 0)

    def test_expiry_refreshes_renames_and_new_instances_check_revocation(self):
        self.names.refresh()
        self.native.return_value['contacts'][0]['name'] = 'Renamed'
        self.now += 3601
        names = Contacts(self.store, native=self.native, clock=lambda: self.now)
        self.assertEqual(names.resolve(ALICE.address)['display_name'], 'Renamed')
        self.assertIn('export', [c.args[0] for c in self.native.call_args_list])
        self.native.return_value = dict(status='denied', contacts=[])
        self.assertEqual(Contacts(self.store, native=self.native).resolve(ALICE.address)['resolution'], 'unresolved')

    def test_dashboard_labels_do_not_change_allowlist_or_canonical_keys(self):
        self.store.put_contact(Contact(ALICE, ALICE.address, False))
        self.names.refresh()
        service = DashboardService(self.store)
        service.names = self.names
        view = service.contacts()[0]
        self.assertEqual(view['display_name'], 'Alice')
        self.assertEqual(view['address'], ALICE.address)
        self.assertEqual(view['recipient_key'], ALICE.key)
        self.assertFalse(view['outbound_allowed'])
        self.assertEqual(len(service.contacts('Alice')), 1)

    def test_installer_bundles_a_purpose_string_and_supported_framework(self):
        from imessage_agent.contacts import install_helper
        import plistlib
        target = Path(self.temp.name) / 'Contacts.app'
        with patch('imessage_agent.contacts.sys.platform', 'darwin'), patch(
                'imessage_agent.contacts.helper_path', return_value=target), patch('subprocess.run') as run:
            install_helper()
        info = plistlib.loads((target / 'Contents/Info.plist').read_bytes())
        self.assertIn('NSContactsUsageDescription', info)
        self.assertEqual(info['CFBundleIdentifier'], 'io.github.rokohli.imessage-agent.contacts')
        self.assertIn('Contacts', run.call_args_list[0].args[0])
        self.assertIn('ContactResolver', run.call_args_list[0].args[0])
        self.assertFalse((target / 'Contents/MacOS/Contacts').exists())  # Compiler was mocked.

    def test_missing_helper_never_prompts_or_launches(self):
        with patch('imessage_agent.contacts.helper_path', return_value=Path(self.temp.name) / 'absent'), patch('subprocess.run') as run:
            self.assertEqual(native_contacts()['status'], 'unavailable')
            run.assert_not_called()
