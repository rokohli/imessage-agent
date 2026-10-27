"""Synthetic regressions for non-authored history, copied drafts and texting style."""

import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from imessage_agent.drafting import build_profiles, examples_for, generate, history, prepare_context, profile, punctuation_policy, safe_text
from imessage_agent.evaluation import evaluate, select_cases
from imessage_agent.ingestion import ingest_once
from imessage_agent.macos import MacOSMessagesReader
from imessage_agent.models import Direction
from imessage_agent.ollama import DraftError
from imessage_agent.quality import artifact_category
from imessage_agent.storage import Store
from imessage_agent.review import ReviewStore
from test_drafting import CHATS, FakeProvider, msg
from test_macos import add
import test_review


class QualityTests(unittest.TestCase):
    def test_english_spanish_reactions_and_explicit_test_markers_are_not_authored_text(self):
        cases = (
            ('Liked “fictional text”', 'reaction_notification'),
            ('Loved "fictional text"', 'reaction_notification'),
            ('Disliked «fictional text»', 'reaction_notification'),
            ('Le gustó “fictional text”', 'reaction_notification'),
            ('Le gusto\u0301 “fictional text”', 'reaction_notification'),
            ('Le encantó “fictional text”', 'reaction_notification'),
            ('AGENT TEST UNICODE 🚀', 'agent_test'),
            ('Testing agent. No respondse needed.', 'agent_test'),
        )
        for body, category in cases:
            with self.subTest(body=body):
                self.assertEqual(artifact_category(body), category)
                self.assertFalse(safe_text(body))
        for body in ('I loved the movie', 'Liked it a lot', 'the math test was hard'):
            # Only explicit marker prefixes are excluded, not ordinary uses of the word test.
            self.assertIsNone(artifact_category(body))
            self.assertTrue(safe_text(body))

    def test_artifacts_are_excluded_from_profiles_retrieval_context_and_eval_targets(self):
        rows = [msg(1, 'how was the ending?'), msg(2, 'absolute chaos 😂', True),
                msg(3, 'Liked “fictional text”', True), msg(4, 'AGENT TEST UNICODE 🚀', True),
                msg(5, 'Testing agent. No respondse needed.', True),
                msg(6, 'how was the ending again?'), msg(7, 'Le gustó “another fictional text”', True),
                msg(8, 'how was the ending again?'), msg(9, 'still wild lol', True),
                msg(10, 'how was the ending?')]
        profiles = build_profiles(rows, CHATS)
        self.assertEqual(profiles['general']['sample_count'], 2)
        pairs = examples_for(rows, rows[-1], CHATS)
        self.assertEqual(len(pairs), 2)
        _, _, context = prepare_context(rows, CHATS, rows[-1])
        encoded = json.dumps({'profiles': profiles, 'pairs': pairs, 'context': context})
        for junk in ('Liked', 'Le gust', 'AGENT TEST', 'Testing agent'):
            self.assertNotIn(junk, encoded)
        selected = select_cases(rows, CHATS, 20)
        self.assertEqual([(incoming.sequence, actual.sequence) for incoming, actual in selected], [(8, 9), (1, 2)])
        for junk in rows[2:5] + [rows[6]]:
            provider = FakeProvider()
            draft = generate(rows, CHATS, replace(junk, direction=Direction.INBOUND, sender=CHATS['a']), provider)
            self.assertEqual(draft['reason'], 'non_authored_source')
            self.assertFalse(provider.prompts)

    def test_removed_artifact_boundaries_are_not_used_as_reply_turns(self):
        for junk in (msg(2, 'Loved “fictional”', True), msg(2, 'AGENT TEST', True),
                     replace(msg(2, 'opaque service payload', True), is_service=True)):
            rows = [msg(1, 'ending?'), junk, msg(3, 'unpaired ending reply', True)]
            self.assertEqual(select_cases(rows, CHATS, 5), [])
            self.assertEqual(examples_for(rows, msg(4, 'ending?'), CHATS), [])

    def test_pat_credential_topic_is_redacted_without_treating_a_name_as_a_token(self):
        for body in ('where is your PAT', 'do you know ur PAT', 'personal access token'):
            self.assertFalse(safe_text(body))
        self.assertTrue(safe_text('Pat liked the movie'))
        rows = [msg(1, 'where is your PAT'), msg(2, 'lemme check')]
        provider = FakeProvider()
        generate(rows, CHATS, rows[-1], provider)
        self.assertNotIn('your PAT', json.dumps(provider.prompts))
        self.assertIn('[content excluded]', json.dumps(provider.prompts))

    def test_copied_old_statement_gets_one_grounded_rewrite(self):
        rows = [msg(1, 'what happened?'), msg(2, 'that ending was wild', True), msg(3, 'miss you pal')]
        provider = FakeProvider()
        provider.chat = MagicMock(side_effect=[
            {'reply': 'that ending was wild', 'needs_personal_facts': False},
            {'reply': 'haha hey stranger 😂', 'needs_personal_facts': False},
        ])
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(draft['reply'], 'haha hey stranger 😂')
        self.assertEqual(draft['generation_attempts'], 2)
        self.assertEqual(draft['quality_flags'], ['copy_rewrite'])
        self.assertEqual(provider.chat.call_count, 2)
        messages = provider.chat.call_args.args[0]
        self.assertIn('Do not invent', messages[-1]['content'])
        prompt = json.loads(messages[1]['content'])
        self.assertEqual(prompt['latest_incoming_turn'], ['miss you pal'])
        self.assertNotIn('contact_examples', prompt)
        self.assertEqual(prompt['conversation_examples'], [])
        self.assertFalse(draft['auto_sending'])

    def test_repeated_copy_or_failed_rewrite_stays_reviewable_never_loops(self):
        rows = [msg(1, 'old topic'), msg(2, 'unrelated old wording', True), msg(3, 'a new question?')]
        provider = FakeProvider('unrelated old wording')
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(draft['validation_status'], 'approval_required')
        self.assertEqual(draft['claim_category'], 'historical_copy')
        self.assertIn('historical_copy', draft['quality_flags'])
        self.assertEqual(draft['reply'], 'unrelated old wording')
        provider.chat = MagicMock(side_effect=[provider.result, DraftError('ollama_malformed_json')])
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(provider.chat.call_count, 2)
        self.assertIn('copy_rewrite_failed', draft['quality_flags'])
        self.assertEqual(draft['classification'], 'APPROVAL_REQUIRED')
        self.assertEqual(draft['reply'], 'unrelated old wording')

    def test_rewrite_does_not_weaken_claim_validation_or_force_a_promise(self):
        rows = [msg(1, 'old topic'), msg(2, 'unrelated old wording', True), msg(3, 'can you come?')]
        provider = FakeProvider()
        provider.chat = MagicMock(side_effect=[
            {'reply': 'unrelated old wording', 'needs_personal_facts': False},
            {'reply': "I'll be there", 'needs_personal_facts': False},
        ])
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(draft['claim_category'], 'commitment')
        self.assertEqual(draft['classification'], 'NEEDS_USER_INPUT')
        self.assertFalse(draft['auto_sending'])
        provider.chat = MagicMock(side_effect=[
            {'reply': 'unrelated old wording', 'needs_personal_facts': False},
            {'reply': '', 'needs_personal_facts': True},
        ])
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(draft['reply'], '')
        self.assertEqual(draft['classification'], 'NEEDS_USER_INPUT')

    def test_credentials_are_not_sent_to_an_optional_copy_rewrite(self):
        rows = [msg(1, 'old topic'), msg(2, 'password is huntertwo', True), msg(3, 'hello')]
        provider = FakeProvider('password is huntertwo')
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(len(provider.prompts), 1)
        self.assertEqual(draft['validation_status'], 'rejected')
        self.assertEqual(draft['reply'], '')
        self.assertNotIn('huntertwo', json.dumps(provider.prompts))

    def test_generic_acknowledgments_and_emoji_can_repeat_without_a_retry(self):
        for reply in ('no worries', 'lol 😂', '😭😭'):
            rows = [msg(1, 'old topic'), msg(2, reply, True), msg(3, 'thanks')]
            provider = FakeProvider(reply)
            draft = generate(rows, CHATS, rows[-1], provider)
            self.assertEqual(draft['generation_attempts'], 1)
            self.assertEqual(len(provider.prompts), 1)
            self.assertEqual(draft['quality_flags'], [])

    def test_model_cannot_output_notification_or_test_artifacts_as_a_reply(self):
        for reply in ('Le encantó “fictional text”', 'AGENT TEST UNICODE 🚀'):
            provider = FakeProvider(reply)
            draft = generate([msg(1, 'hello')], CHATS, msg(1, 'hello'), provider)
            self.assertEqual(draft['validation_status'], 'rejected')
            self.assertEqual(draft['claim_category'], 'non_authored_output')
            self.assertEqual(draft['reply'], '')
            self.assertFalse(provider.claim_reviews)

    def test_light_punctuation_preference_is_explicit_and_does_not_strip_meaning(self):
        rows = [msg(1, 'im around', True), msg(2, "where's that?", True),
                msg(3, 'yep', True), msg(4, 'what a plot twist')]
        stats = profile(rows)
        self.assertEqual(stats['final_period_fraction'], 0)
        self.assertAlmostEqual(stats['question_mark_fraction'], 1 / 3)
        self.assertEqual(stats['comma_fraction'], 0)
        self.assertAlmostEqual(stats['terminal_punctuation_fraction'], 1 / 3)
        provider = FakeProvider("what'd you mean 😂")
        draft = generate(rows, CHATS, rows[-1], provider)
        self.assertEqual(draft['reply'], "what'd you mean 😂")
        instructions = provider.prompts[0][0]['content']
        self.assertIn('DEFAULT TO NO PUNCTUATION', instructions)
        self.assertIn('punctuation_policy.allowed_marks', instructions)
        self.assertIn('question marks and apostrophes', instructions)
        policy = json.loads(provider.prompts[0][1]['content'])['punctuation_policy']
        self.assertEqual(policy, {'default': 'none', 'outgoing_samples': 3, 'allowed_marks': []})

    def test_punctuation_requires_three_owner_uses_and_a_strong_majority(self):
        self.assertEqual(punctuation_policy([])['allowed_marks'], [])
        sparse = [msg(1, 'yep!', True), msg(2, 'for sure!', True)]
        self.assertEqual(punctuation_policy(sparse)['allowed_marks'], [])
        strong = sparse + [msg(3, 'absolutely!', True)]
        self.assertEqual(punctuation_policy(strong)['allowed_marks'], ['!'])
        weak = strong + [msg(4, 'ok', True), msg(5, 'yep', True)]
        self.assertEqual(punctuation_policy(weak)['allowed_marks'], [])
        self.assertEqual(punctuation_policy(strong + [msg(4, 'ok', True)])['allowed_marks'], ['!'])

    def test_incoming_other_chat_and_heldout_punctuation_cannot_change_reply_policy(self):
        rows = [msg(1, 'wow!!!'), msg(2, 'really!!!'), msg(3, 'cool!!!'),
                msg(4, 'yes.', True, chat='b'), msg(5, 'certainly.', True, chat='b'),
                msg(6, 'absolutely.', True, chat='b'), msg(7, 'what do you think?'),
                msg(8, 'future punctuation!!!', True)]
        provider = FakeProvider('looks good')
        generate(rows, CHATS, rows[6], provider)
        policy = json.loads(provider.prompts[0][1]['content'])['punctuation_policy']
        self.assertEqual(policy, {'default': 'none', 'outgoing_samples': 0, 'allowed_marks': []})
        rows = [msg(1, 'where?', True), msg(2, 'which?', True), msg(3, 'when?', True), msg(4, 'hey')]
        provider = FakeProvider('which one')
        generate(rows, CHATS, rows[-1], provider)
        policy = json.loads(provider.prompts[0][1]['content'])['punctuation_policy']
        self.assertEqual(policy['allowed_marks'], ['?'])
        self.assertNotIn('.', policy['allowed_marks'])
        self.assertNotIn(',', policy['allowed_marks'])

    def test_quality_flags_are_in_content_free_evaluation_diagnostics(self):
        rows = [msg(1, 'old topic'), msg(2, 'unrelated old wording', True),
                msg(3, 'a new question?'), msg(4, 'authored held out reply', True)]
        report = evaluate(rows, CHATS, [FakeProvider.config], cases=1,
                          provider_factory=lambda config: FakeProvider('unrelated old wording'))
        result = report['results'][0]
        self.assertEqual(result['quality_flags'], {'copy_rewrite': 1, 'historical_copy': 1})
        self.assertEqual(result['validation_counts']['approval_required'], 1)
        self.assertNotIn('unrelated old wording', json.dumps(report))
        self.assertNotIn('authored held out reply', json.dumps(report))


class MetadataQualityTests(unittest.TestCase):
    setUp = test_review.ReviewTests.setUp

    def test_native_service_metadata_is_excluded_without_reclassifying_raw_ingestion(self):
        add(self.db, 41, is_from_me=1, text='opaque payload', associated_message_type=2000)
        with MacOSMessagesReader(self.path) as reader:
            service = reader.read_after(40, 1).messages[0]
            self.assertEqual(service.direction, Direction.OUTBOUND)
            self.assertTrue(service.is_service)
            chats = reader.individual_chats()
            rows, counts = history(reader, chats, 100)
            self.assertNotIn(41, [m.sequence for m in rows])
            self.assertEqual(counts['artifact_messages_filtered'], {'service_metadata': 1})
            self.assertEqual(build_profiles([service], chats)['general']['sample_count'], 0)
            self.assertEqual(profile([service])['sample_count'], 0)
            store = Store(self.root / 'agent.db')
            try:
                while ingest_once(store, reader):
                    pass
                saved = store.messages(reader.source_id)[-1]
                self.assertEqual(saved['direction'], 'outbound')
                self.assertEqual(saved['body'], 'opaque payload')
                self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], 5)
            finally:
                store.close()

    def test_clean_sampling_excludes_text_artifacts_without_changing_source(self):
        add(self.db, 41, is_from_me=1, text='Liked “fictional text”')
        add(self.db, 42, is_from_me=1, text='AGENT TEST UNICODE 🚀')
        add(self.db, 43, is_from_me=1, text='Testing agent. No respondse needed.')
        before = self.path.read_bytes()
        with MacOSMessagesReader(self.path) as reader:
            chats = reader.individual_chats()
            rows, counts = history(reader, chats, 100)
        self.assertEqual(counts['artifact_messages_filtered'], {'reaction_notification': 1, 'agent_test': 2})
        selected = select_cases(rows, chats, 20)
        self.assertEqual(len(selected), 20)
        self.assertTrue(all(actual.sequence <= 40 for _, actual in selected))
        self.assertEqual(build_profiles(rows, chats)['general']['sample_count'], 20)
        self.assertEqual(self.path.read_bytes(), before)

    def test_resume_preserves_old_review_snapshots_instead_of_silently_cleaning_them(self):
        test_review.ReviewTests.invoke(self, ['q'], cases=1)
        store = ReviewStore(self.ratings, resume=True)
        try:
            row = store.cases()[0]
            snapshot = json.loads(row['snapshot_json'])
            snapshot['actual_reply'] = 'Liked “fictional old reference”'
            snapshot['draft'].pop('quality_flags', None)
            snapshot['draft'].pop('generation_attempts', None)
            encoded = json.dumps(snapshot)
            with store.connection:
                store.connection.execute('UPDATE review_cases SET snapshot_json=? WHERE ordinal=1', (encoded,))
        finally:
            store.close()
        with patch('imessage_agent.review.MacOSMessagesReader', side_effect=AssertionError('no new history access')):
            status, report, output, _ = test_review.ReviewTests.invoke(self, ['1 1 1 n'], cases=1, resume=True)
        self.assertEqual(status, 0)
        self.assertEqual(report['human_review']['rated'], 1)
        self.assertIn('fictional old reference', output)
        store = ReviewStore(self.ratings, resume=True)
        try:
            self.assertEqual(store.cases()[0]['snapshot_json'], encoded)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
