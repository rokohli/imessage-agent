"""Opt-in human review with synthetic databases and mocked local inference only."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.models import Contact
from imessage_agent.ollama import DraftError
from imessage_agent.review import ReviewStore, _terminal_text
from imessage_agent.storage import Store
from test_drafting import ALICE, FakeProvider
from test_macos import add, fixture


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='review-synthetic-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'messages.db'
        self.ratings = self.root / 'reviews' / 'ratings.db'
        self.db = fixture(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE chat_handle_join(chat_id INTEGER,handle_id INTEGER)')
        self.db.execute('INSERT INTO chat_handle_join VALUES (1,1)')
        self.db.execute('CREATE INDEX sample_message_chat ON chat_message_join(message_id)')
        self.db.commit()
        for index in range(20):
            add(self.db, index * 2 + 1, text='that ending was wild', date=700000000 + index * 2 + 1)
            add(self.db, index * 2 + 2, text='actual phrase ' + chr(97 + index),
                is_from_me=1, date=700000000 + index * 2 + 2)

    def invoke(self, answers=(), *, cases=20, show=True, resume=False, tty=True, provider=None):
        output = io.StringIO()
        args = ['review-drafts', '--database', str(self.path), '--ratings', str(self.ratings), '--cases', str(cases)]
        if show:
            args.append('--show-text')
        if resume:
            args.append('--resume')
        provider = provider or FakeProvider('bruh 💀')
        with contextlib.redirect_stdout(output), patch('sys.stdin.isatty', return_value=tty), patch(
            'sys.stdout.isatty', return_value=tty
        ), patch('builtins.input', side_effect=answers), patch('imessage_agent.review.Ollama', return_value=provider):
            status = main(args)
        text = output.getvalue()
        return status, json.loads(text.splitlines()[-1]), text, provider

    def test_no_opt_in_or_nonterminal_does_not_read_source_or_create_files(self):
        for show, tty, rule in (
            (False, True, 'review_requires_show_text_opt_in'),
            (True, False, 'review_requires_interactive_terminal_no_redirection'),
        ):
            with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('no source access')):
                status, report, output, _ = self.invoke(show=show, tty=tty)
            self.assertEqual(status, 1)
            self.assertEqual(report['error'], rule)
            self.assertNotIn('actual phrase', output)
            self.assertFalse(self.ratings.exists())

    def test_twenty_snapshots_and_ratings_are_private_and_human_scores_are_separate(self):
        before = self.path.read_bytes()
        status, report, output, provider = self.invoke(['4 5 3 n'] * 20)
        self.assertEqual(status, 0)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['prepared'], 20)
        self.assertEqual(report['human_review']['rated'], 20)
        self.assertEqual(report['human_review']['pending'], 0)
        self.assertEqual(report['human_review']['mean_style'], 4)
        self.assertEqual(report['human_review']['mean_relevance'], 5)
        self.assertEqual(report['human_review']['mean_naturalness'], 3)
        self.assertEqual(report['human_review']['invention_yes'], 0)
        self.assertIn('heuristics', report)
        self.assertNotIn('mean_score', report)
        self.assertFalse(report['auto_sending'])
        self.assertIn('Contact:', output)
        self.assertIn('Generated: "bruh 💀"', output)
        self.assertIn('Actual held-out:', output)
        self.assertIn('Classification:', output)
        self.assertNotIn('actual phrase t', json.dumps(provider.prompts))
        self.assertNotIn('actual phrase t', json.dumps(provider.claim_reviews))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.stat(self.ratings).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.ratings.parent).st_mode & 0o777, 0o700)
        with contextlib.closing(sqlite3.connect(self.ratings)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM review_cases WHERE rating_json IS NOT NULL').fetchone()[0], 20)
            self.assertEqual({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             {'review_session', 'review_cases'})

    def test_resume_uses_saved_candidates_without_source_or_model_access(self):
        status, report, _, _ = self.invoke(['3 4 5 y', 'q'], cases=2)
        self.assertEqual(status, 0)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['human_review']['rated'], 1)
        self.assertEqual(report['human_review']['content_accepted_with_invention'], 1)
        with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('resume must not read Messages')):
            provider = FakeProvider()
            provider.chat = lambda messages: self.fail('resume must not generate')
            status, report, output, _ = self.invoke(['5 5 5 n'], cases=2, resume=True, provider=provider)
        self.assertEqual(status, 0)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['human_review']['rated'], 2)
        self.assertEqual(report['human_review']['invention_yes'], 1)
        self.assertEqual(report['human_review']['mean_style'], 4)
        self.assertNotIn('Case 1 (private', output)
        self.assertIn('Generated: "bruh 💀"', output)
        self.assertNotIn('Generated: "no worries"', output)
        before = self.ratings.read_bytes()
        with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('existing file must fail early')):
            status, report, _, _ = self.invoke(cases=2)
        self.assertEqual(report['error'], 'review_file_exists_use_resume_or_new_path')
        self.assertEqual(self.ratings.read_bytes(), before)

    def test_invalid_ratings_are_reprompted_and_interrupt_keeps_committed_ratings(self):
        answers = ['6 5 5 n', '1 2 3 maybe', '1 2 3 n', KeyboardInterrupt()]
        status, report, output, _ = self.invoke(answers, cases=2)
        self.assertEqual(status, 130)
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual(report['human_review']['rated'], 1)
        self.assertEqual(report['human_review']['mean_style'], 1)
        self.assertEqual(output.count('Nothing saved yet.'), 2)
        status, report, _, _ = self.invoke(['s'], cases=2, resume=True)
        self.assertEqual(status, 0)
        self.assertEqual(report['human_review']['pending'], 1)

    def test_outbound_state_cannot_be_reused_or_migrated_as_a_review_file(self):
        path = self.root / 'agent.db'
        store = Store(path)
        store.put_contact(Contact(ALICE, 'Fictional', False))
        store.approve('prior', ALICE, 'prior text', 9999999999)
        store.close()
        before = path.read_bytes()
        self.ratings = path
        status, report, _, _ = self.invoke(resume=True)
        self.assertEqual(status, 1)
        self.assertEqual(report['error'], 'unrecognized_review_file')
        self.assertEqual(path.read_bytes(), before)
        self.ratings = self.path
        status, report, _, _ = self.invoke()
        self.assertEqual(report['error'], 'state_is_source')
        self.assertEqual(status, 1)

    def test_symlinks_hardlinks_and_permissive_review_files_are_refused(self):
        real = self.root / 'real.db'
        real.touch(mode=0o600)
        alias = self.root / 'alias.db'
        alias.symlink_to(real)
        self.ratings = alias
        self.assertEqual(self.invoke(resume=True)[1]['error'], 'unsafe_state_path')
        alias.unlink()
        os.link(real, alias)
        self.assertEqual(self.invoke(resume=True)[1]['error'], 'unsafe_state_file')
        alias.unlink()
        real.chmod(0o644)
        self.ratings = real
        self.assertEqual(self.invoke(resume=True)[1]['error'], 'unsafe_state_file')

    def test_generation_error_does_not_become_a_human_rating(self):
        provider = FakeProvider()
        provider.chat = lambda messages: {'reply': 'malformed private'}
        status, report, output, _ = self.invoke(cases=2, provider=provider)
        self.assertEqual(status, 1)
        self.assertEqual(report['generation_failures'], 2)
        self.assertEqual(report['human_review']['rated'], 0)
        self.assertIsNone(report['human_review']['mean_style'])
        self.assertNotIn('malformed private', output)
        self.assertIn('ollama_missing_needs_personal_facts', output)
        self.assertEqual(report['heuristics']['relevance'], 0)

    def test_ratings_are_not_approval_or_training_signals(self):
        status, report, _, provider = self.invoke(['5 5 5 y'], cases=1)
        self.assertEqual(status, 0)
        self.assertEqual(len(provider.prompts), 1)
        self.assertEqual(len(provider.claim_reviews), 1)
        self.assertFalse(report['auto_sending'])
        self.assertEqual(report['human_review']['content_accepted_with_invention'], 1)
        self.assertNotIn('5 5 5', json.dumps(provider.prompts))
        self.assertNotIn('human_review', json.dumps(provider.prompts))
        store = ReviewStore(self.ratings, resume=True)
        try:
            for rating in (
                {'style': True, 'relevance': 5, 'naturalness': 5, 'invents_personal_information': False},
                {'style': 5, 'relevance': 0, 'naturalness': 5, 'invents_personal_information': False},
                {'style': 5, 'relevance': 5, 'naturalness': 5, 'invents_personal_information': 'no'},
            ):
                with self.assertRaisesRegex(DraftError, '^invalid_review_rating$'):
                    store.rate(1, rating)
            with self.assertRaisesRegex(DraftError, '^review_case_missing_or_already_rated$'):
                store.rate(1, {'style': 5, 'relevance': 5, 'naturalness': 5, 'invents_personal_information': False})
        finally:
            store.close()

    def test_terminal_representation_escapes_controls_but_preserves_emoji(self):
        displayed = _terminal_text('hello\x1b]52;clipboard\x07\n\u202e😂')
        self.assertNotIn('\x1b', displayed)
        self.assertNotIn('\x07', displayed)
        self.assertNotIn('\u202e', displayed)
        self.assertIn('😂', displayed)
        self.assertEqual(len(displayed.splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
