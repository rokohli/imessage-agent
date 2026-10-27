"""Large synthetic Messages history; no model, personal data or sending."""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.drafting import build_profiles, history
from imessage_agent.evaluation import select_cases
from imessage_agent.macos import MacOSMessagesReader, MessagesAccessError
from imessage_agent.models import Contact, Direction
from imessage_agent.storage import Store
from test_drafting import ALICE, BOB
from test_macos import fixture


class HistorySamplingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='history-synthetic-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'messages.db'
        self.state = Path(self.directory.name) / 'state.db'
        self.db = fixture(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript('''
            ALTER TABLE chat ADD COLUMN style INTEGER DEFAULT 45;
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            CREATE INDEX sample_membership ON chat_handle_join(chat_id);
            CREATE INDEX sample_message_chat ON chat_message_join(message_id);
            INSERT INTO handle VALUES ('+12025550102');
            INSERT INTO chat VALUES ('bob-chat',45);
            INSERT INTO chat VALUES ('busy-group',43);
            INSERT INTO chat_handle_join VALUES (1,1),(2,2),(3,1),(3,2);
        ''')
        # More than 20,000 individual records, followed by even more group traffic.
        self.last_individual = 21010
        total = self.last_individual + 30000
        def chat_for(sequence):
            return (1 if sequence % 4 in (1, 2) else 2) if sequence <= self.last_individual else 3
        records = []
        for sequence in range(1, total + 1):
            chat = chat_for(sequence)
            outgoing = sequence % 2 == 0
            records.append((sequence, f'synthetic-{sequence}', 700000000 + sequence,
                            int(outgoing), chat if chat < 3 else 1,
                            ('no worries' if chat == 1 else 'thx') if outgoing else 'thanks'))
        self.db.executemany('INSERT INTO message (ROWID,guid,date,is_from_me,handle_id,text) VALUES (?,?,?,?,?,?)', records)
        self.db.executemany('INSERT INTO chat_message_join VALUES (?,?)',
                            ((chat_for(n), n) for n in range(1, total + 1)))
        self.db.commit()

    def sample(self, maximum, **kwargs):
        with MacOSMessagesReader(self.path) as reader:
            chats = reader.individual_chats()
            rows, counts = history(reader, chats, maximum, **kwargs)
        return rows, counts, chats

    def invoke(self, command, *extra):
        args = [command, '--database', str(self.path)]
        if command != 'evaluate-drafts':
            args += ['--state', str(self.state)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch(
            'imessage_agent.ollama.Ollama.classify_claims', return_value='harmless'
        ):
            status = main(args + list(extra))
        return status, json.loads(output.getvalue())

    def test_large_history_is_recent_bounded_ordered_and_deterministic(self):
        original = self.path.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('no network')), patch(
            'subprocess.Popen', side_effect=AssertionError('no sending')
        ):
            rows, counts, chats = self.sample(5000)
            repeated, repeated_counts, _ = self.sample(5000)
            larger, _, _ = self.sample(10000)
            formerly_failing, _, _ = self.sample(20000)
        self.assertEqual(rows, repeated)
        self.assertEqual(counts, repeated_counts)
        self.assertEqual([m.sequence for m in rows], list(range(self.last_individual - 4999, self.last_individual + 1)))
        self.assertEqual(len(larger), 10000)
        self.assertEqual(len(formerly_failing), 20000)
        self.assertTrue(all(m.chat_id != 'busy-group' for m in rows))
        self.assertEqual(counts['messages_scanned'], 5000)
        self.assertEqual(counts['messages_selected'], 5000)
        self.assertEqual(counts['messages_filtered'], 0)
        self.assertEqual(counts['messages_used_for_profiles'], 2500)
        self.assertEqual(self.path.read_bytes(), original)
        profiles = build_profiles(rows, chats)
        self.assertEqual(profiles['general']['sample_count'], 2500)
        self.assertEqual(profiles[ALICE.key]['sample_count'], 1250)
        self.assertEqual(profiles[BOB.key]['sample_count'], 1250)
        self.assertEqual(profiles[ALICE.key]['examples'][-1]['body'], 'no worries')
        self.assertEqual(profiles[BOB.key]['examples'][-1]['body'], 'thx')
        for incoming, outgoing in select_cases(rows, chats, 20):
            self.assertEqual(incoming.chat_id, outgoing.chat_id)
            self.assertEqual(incoming.sequence + 1, outgoing.sequence)

    def test_sparse_useful_history_skips_sensitive_and_redacts_context(self):
        # Useful messages are older than an entire page of rejected examples.
        self.db.execute("UPDATE message SET text='password is secret' WHERE ROWID>19000 AND ROWID<=21010")
        self.db.execute("UPDATE message SET text='medical diagnosis' WHERE ROWID=18999")
        self.db.commit()
        rows, counts, chats = self.sample(6)
        self.assertEqual([m.sequence for m in rows], list(range(18995, 19001)))
        self.assertIsNone(rows[-2].body)
        self.assertEqual(rows[-1].body, 'thx')
        self.assertGreater(counts['messages_scanned'], counts['messages_selected'])
        self.assertEqual(counts['messages_filtered'], counts['messages_scanned'] - 6)
        self.assertEqual(counts['context_messages_redacted'], 1)
        self.assertEqual(counts['messages_used_for_profiles'], 3)
        self.assertNotIn('medical', json.dumps(build_profiles(rows, chats)))
        self.assertNotIn('password', json.dumps(build_profiles(rows, chats)))
        self.assertTrue(all(incoming.sequence != 18999 for incoming, _ in select_cases(rows, chats, 10)))

    def test_reverse_reader_pages_without_gaps_duplicates_or_source_writes(self):
        before = self.path.read_bytes()
        with MacOSMessagesReader(self.path) as reader:
            first = reader.read_before(None, 3).messages
            second = reader.read_before(first[0].sequence, 3).messages
            self.assertEqual([m.sequence for m in second + first], list(range(21005, 21011)))
            self.assertEqual(reader._connection.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.DatabaseError):
                reader._connection.execute('DELETE FROM message')
            for sequence, limit in ((True, 1), (-1, 1), (None, 0), (None, 1001)):
                with self.assertRaises(ValueError):
                    reader.read_before(sequence, limit)
        with self.assertRaisesRegex(MessagesAccessError, 'reader_closed'):
            reader.read_before(None, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_profiles_and_evaluation_share_loader_and_keep_outbound_state(self):
        store = Store(self.state)
        try:
            store.put_contact(Contact(ALICE, 'Fictional', False))
            store.approve('existing', ALICE, 'previously approved text', 9999999999)
            store.connection.execute("INSERT INTO cursors VALUES ('fictional','one',42)")
            store.connection.commit()
            tables = ('contacts', 'approvals', 'send_attempts', 'outbound_claims', 'schedules', 'messages', 'cursors')
            existing = {table: [tuple(row) for row in store.connection.execute(f'SELECT * FROM {table}')]
                        for table in tables}
            status, report = self.invoke('build-profiles', '--max-messages', '10000')
            self.assertEqual(status, 0)
            self.assertEqual(report['messages_selected'], 10000)
            self.assertEqual(report['general_samples'], 5000)
            self.assertEqual(report['messages_used_for_profiles'], 5000)
            for table in tables:
                self.assertEqual([tuple(row) for row in store.connection.execute(f'SELECT * FROM {table}')], existing[table])
        finally:
            store.close()
        state_before = self.state.read_bytes()
        with patch('imessage_agent.ollama.Ollama.chat', return_value={
            'needs_personal_facts': False, 'reply': 'no worries'
        }) as model:
            status, report = self.invoke('evaluate-drafts', '--max-messages', '10000', '--cases', '3')
        self.assertEqual(status, 0)
        self.assertEqual(report['messages_selected'], 10000)
        self.assertEqual(report['results'][0]['cases'], 3)
        self.assertEqual(model.call_count, 3)
        self.assertFalse(report['auto_sending'])
        self.assertEqual(self.state.read_bytes(), state_before)

    def test_old_preview_target_is_read_independently_and_never_sees_later_history(self):
        # This target is far outside the latest sample and must still be found.
        with patch('imessage_agent.ollama.Ollama.chat', return_value={
            'needs_personal_facts': False, 'reply': 'thanks'
        }) as model:
            status, report = self.invoke('draft-preview', '--max-messages', '4', '--source-sequence', '101')
        self.assertEqual(status, 0)
        self.assertLessEqual(report['messages_selected'], 4)
        store = Store(self.state)
        try:
            row = store.connection.execute('SELECT * FROM reply_drafts').fetchone()
            draft = json.loads(row['draft_json'])
        finally:
            store.close()
        self.assertEqual(row['source_sequence'], 101)
        self.assertTrue(all(int(guid.split('-')[-1]) <= 101 for guid in draft['context_guids']))
        self.assertNotIn('busy-group', json.dumps(model.call_args.args))
        rows, _, _ = self.sample(4, before=101)
        self.assertTrue(all(message.sequence < 101 for message in rows))

    def test_preview_preserves_recent_incoming_context_and_strict_budget(self):
        self.db.execute("UPDATE message SET is_from_me=0,text='can you explain?' WHERE ROWID BETWEEN 101 AND 120")
        self.db.execute('UPDATE message SET handle_id=1 WHERE ROWID BETWEEN 101 AND 120')
        self.db.execute('UPDATE chat_message_join SET chat_id=1 WHERE message_id BETWEEN 101 AND 120')
        self.db.commit()
        for maximum in (1, 3, 12, 25):
            rows, counts, _ = self.sample(maximum, before=121, context_chat='synthetic-chat')
            self.assertLessEqual(len(rows), maximum)
            self.assertEqual([m.sequence for m in rows][-min(12, maximum):],
                             list(range(121 - min(12, maximum), 121)))
            self.assertEqual(counts['messages_selected'], len(rows))

    def test_only_groups_or_filtered_history_returns_empty_profiles_without_overflow(self):
        self.db.execute("UPDATE message SET text='verification code' WHERE ROWID<=21010")
        self.db.commit()
        rows, counts, _ = self.sample(5000)
        self.assertEqual(rows, [])
        self.assertEqual(counts['messages_scanned'], self.last_individual)
        self.assertEqual(counts['messages_filtered'], self.last_individual)
        self.db.execute('UPDATE chat SET style=43')
        self.db.commit()
        rows, counts, _ = self.sample(5000)
        self.assertEqual(rows, [])
        self.assertEqual(counts['messages_scanned'], 0)
        status, report = self.invoke('build-profiles', '--max-messages', '5000')
        self.assertEqual(status, 0)
        self.assertEqual(report['messages_used_for_profiles'], 0)


if __name__ == '__main__':
    unittest.main()
