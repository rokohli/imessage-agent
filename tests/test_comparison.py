"""Frozen synthetic comparisons; no personal Messages, Ollama, or sending."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from imessage_agent.__main__ import main
from imessage_agent.review import ReviewStore
from test_drafting import FakeProvider
import test_review


class ComparisonTests(unittest.TestCase):
    setUp = test_review.ReviewTests.setUp

    def prepare(self, *, models=('qwen2.5:3b',), reply='bruh 💀'):
        if not self.ratings.exists():
            result = test_review.ReviewTests.invoke(self, ['4 5 3 n'] * 20)
            self.assertEqual(result[0], 0)
        self.output_path = self.root / 'comparison.db'
        self.providers = []
        def factory(config):
            provider = FakeProvider(reply)
            provider.config = config
            provider.installed_models = lambda: set(models)
            provider.health = lambda: {'status': 'ready'}
            self.providers.append(provider)
            return provider
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('imessage_agent.comparison.Ollama', side_effect=factory):
            status = main(['compare-drafts', '--database', str(self.path), '--source-review', str(self.ratings),
                           '--comparison', str(self.output_path), '--max-messages', '10000'])
        self.preparation_text = output.getvalue()
        return status, json.loads(self.preparation_text.splitlines()[-1])

    def snapshots(self):
        store = ReviewStore(self.output_path, resume=True, readonly=True)
        try:
            return store.session(), [json.loads(row['snapshot_json']) for row in store.cases()]
        finally:
            store.close()

    def test_exact_cases_and_source_files_preserved_with_future_reply_excluded(self):
        test_review.ReviewTests.invoke(self, ['4 5 3 n'] * 20)
        source_before, messages_before = self.ratings.read_bytes(), self.path.read_bytes()
        status, report = self.prepare()
        self.assertEqual(status, 0)
        self.assertEqual(report['trials'], 40)
        self.assertEqual(report['skipped_variants'][0]['reason'], 'not_installed_locally')
        self.assertEqual(self.ratings.read_bytes(), source_before)
        self.assertEqual(self.path.read_bytes(), messages_before)
        metadata, snapshots = self.snapshots()
        self.assertEqual(set(metadata['variants']), {'A', 'B'})
        for label in ('A', 'B'):
            self.assertEqual({s['source_case'] for s in snapshots if s['variant'] == label}, set(range(1, 21)))
        for number in range(1, 21):
            left, right = [s for s in snapshots if s['source_case'] == number]
            for key in ('source_guid', 'held_out_guid', 'source_sequence', 'actual_sequence', 'context', 'actual_reply'):
                self.assertEqual(left[key], right[key])
        for provider in self.providers:
            source_cases = sorted((s for s in snapshots if s['variant'] == 'A'), key=lambda s: s['source_case'])
            for case, messages, claim in zip(source_cases, provider.prompts, provider.claim_reviews):
                prompt = json.loads(messages[1]['content'])
                conversation = prompt.get('conversation', prompt.get('current_conversation'))
                self.assertTrue(conversation)
                self.assertNotIn('actual phrase t', json.dumps(messages))  # Final actual is never preceding.
                encoded = json.dumps([messages, claim])
                for future in source_cases:
                    if future['actual_sequence'] >= case['source_sequence']:
                        self.assertNotIn(future['actual_reply'], encoded)
            self.assertNotIn('actual phrase t', json.dumps(provider.claim_reviews))
        self.assertFalse(report['auto_sending'])
        self.assertNotIn('actual phrase', self.preparation_text)
        self.assertNotIn('bruh', self.preparation_text)

    def test_optional_seven_b_and_blind_resume_then_separate_results(self):
        status, report = self.prepare(models=('qwen2.5:3b', 'qwen2.5:7b'))
        self.assertEqual(status, 0)
        self.assertEqual(report['trials'], 60)
        metadata, snapshots = self.snapshots()
        self.assertEqual(metadata['variants']['A']['pipeline'], 'baseline')
        self.assertEqual(metadata['variants']['B']['pipeline'], 'improved')
        self.assertEqual(metadata['variants']['C']['config']['model'], 'qwen2.5:7b')
        self.assertEqual(len([s for s in snapshots if s['variant'] == 'C']), 20)
        generation_providers = [p for p in self.providers if p.prompts]
        self.assertEqual(generation_providers[1].prompts, generation_providers[2].prompts)
        self.assertEqual(generation_providers[1].claim_reviews, generation_providers[2].claim_reviews)
        self.ratings = self.output_path
        with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('resume source access')):
            status, partial, text, provider = test_review.ReviewTests.invoke(self, ['4 5 3 n', 'q'], resume=True)
        self.assertTrue(partial['variants_hidden'])
        for private_label in ('qwen2.5', 'Classification:', 'latency_ms', 'pipeline', 'Quality flags:', 'mean_style'):
            self.assertNotIn(private_label, text)
        self.assertEqual(provider.prompts, [])
        with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('resume source access')):
            status, final, _, provider = test_review.ReviewTests.invoke(self, ['4 5 3 n'] * 59, resume=True)
        self.assertEqual(status, 0)
        self.assertFalse(final['variants_hidden'])
        self.assertEqual(final['status'], 'complete')
        self.assertEqual(provider.prompts, [])
        for variant in final['variants'].values():
            self.assertEqual(variant['human_review']['rated'], 20)
            self.assertEqual(variant['human_review']['mean_style'], 4)
            self.assertEqual(variant['human_review']['mean_relevance'], 5)
            self.assertEqual(variant['human_review']['mean_naturalness'], 3)
            self.assertEqual(variant['human_review']['invention_rate'], 0)
            self.assertEqual(variant['latency_ms']['samples'], 20)
            self.assertGreaterEqual(variant['latency_ms']['p95'], variant['latency_ms']['median'])
            self.assertIn('heuristics', variant)
            self.assertIn('validation_counts', variant)
        self.assertNotIn('best_config', final)

    def test_baseline_and_improved_payloads_keep_exact_punctuation_instructions(self):
        self.prepare()
        prompts = [p.prompts[0] for p in self.providers if p.prompts]
        self.assertEqual(len(prompts), 2)
        old, new = [json.loads(p[1]['content']) for p in prompts]
        self.assertIn('contact_examples', old)
        self.assertNotIn('contact_examples', new)
        self.assertIn('conversation_examples', new)
        self.assertEqual(old['punctuation_policy'], new['punctuation_policy'])
        from imessage_agent.drafting import _PUNCTUATION_INSTRUCTIONS
        for messages in prompts:
            self.assertIn(_PUNCTUATION_INSTRUCTIONS.strip(), messages[0]['content'])
            self.assertNotIn('classification', json.dumps(messages[1]['content']))
        self.assertLess(len(prompts[1][0]['content']), len(prompts[0][0]['content']))

    def test_changed_case_or_wrong_case_count_fails_without_reselection(self):
        test_review.ReviewTests.invoke(self, ['4 5 3 n'] * 20)
        self.db.execute("UPDATE message SET text='changed actual' WHERE ROWID=40")
        self.db.commit()
        status, report = self.prepare()
        self.assertEqual(status, 1)
        self.assertEqual(report['error'], 'saved_case_changed_or_missing')
        self.assertEqual(self.providers, [])
        self.assertFalse(self.output_path.exists())
        store = ReviewStore(self.ratings, resume=True)
        store.connection.execute('DELETE FROM review_cases WHERE ordinal=1')
        store.connection.commit()
        store.close()
        status, report = self.prepare()
        self.assertEqual(report['error'], 'comparison_requires_twenty_saved_source_cases')

    def test_individual_generation_errors_are_saved_without_aborting_other_trials(self):
        status, report = self.prepare(reply=None)
        self.assertEqual(status, 0)
        _, snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 40)
        self.assertTrue(all(s['generation_error'] == 'ollama_reply_not_text' for s in snapshots))
        self.assertNotIn('ollama_reply_not_text', self.preparation_text)

    def test_source_review_cannot_be_used_as_output_or_written_readonly(self):
        test_review.ReviewTests.invoke(self, ['4 5 3 n'] * 20)
        before = self.ratings.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(['compare-drafts', '--database', str(self.path), '--source-review', str(self.ratings),
                           '--comparison', str(self.ratings)])
        self.assertEqual(status, 1)
        self.assertEqual(self.ratings.read_bytes(), before)
        import sqlite3
        store = ReviewStore(self.ratings, resume=True, readonly=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                store.connection.execute('DELETE FROM review_cases')
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
