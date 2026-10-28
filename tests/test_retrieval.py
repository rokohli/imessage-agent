"""Fictional retrieval, chronological leakage and exact-context grounding checks."""

import json
import unittest
from dataclasses import replace

from imessage_agent.drafting import examples_for, generate
from imessage_agent.evaluation import case_history
from imessage_agent.macos import MacOSMessagesReader
from imessage_agent.models import Batch, Direction
from imessage_agent.ollama import DraftError
from test_drafting import ALICE, BOB, CHATS, FakeProvider, ROWS, msg
from test_macos import add
import test_review


class RetrievalTests(unittest.TestCase):
    def test_same_recipient_across_chats_ranked_by_relevance_not_just_recency(self):
        chats = {**CHATS, 'a-other': ALICE}
        rows = [
            msg(1, 'are you watching the playoffs?'), msg(2, 'nah just highlights lol', True),
            msg(3, 'watching playoffs tonight?', chat='b'), msg(4, 'other contact wording', True, chat='b'),
            replace(msg(5, 'are you watching the playoffs?'), chat_id='a-other'),
            replace(msg(6, 'only the clips 😂', True), chat_id='a-other'),
            msg(7, 'thanks'), msg(8, 'np', True), msg(9, 'watching playoffs tonight?'),
            msg(10, 'held out response', True), msg(11, 'future topic'),
        ]
        pairs = examples_for(rows, rows[8], chats)
        self.assertEqual(pairs[0]['reply'], 'only the clips 😂')
        self.assertNotIn('np', [pair['reply'] for pair in pairs])  # No filler pairs with zero relevance.
        encoded = json.dumps(pairs)
        self.assertIn('other contact wording', encoded)  # Only two same-contact pairs: explicit general fallback.
        self.assertEqual([pair['recipient_key'] for pair in pairs[:2]], [ALICE.key, ALICE.key])
        self.assertNotIn('held out response', encoded)
        self.assertNotIn('future topic', encoded)
        provider = FakeProvider()
        generate(rows, chats, rows[8], provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertEqual(prompt['STYLE_EXAMPLES'][0]['messages'][-1]['text'], 'only the clips 😂')
        self.assertNotIn('general_examples', prompt)  # No isolated outgoing snippets.

    def test_multiple_incoming_messages_are_retrieved_as_one_turn(self):
        rows = [msg(1, 'about the ending'), msg(2, 'what did you think?'),
                msg(3, 'absolute chaos 😂', True), msg(4, 'what did you think about the ending?')]
        pair, = examples_for(rows, rows[-1], CHATS)
        self.assertEqual(pair['incoming'], 'about the ending\nwhat did you think?')
        self.assertEqual(pair['source_guids'], ['g-1', 'g-2', 'g-3'])
        self.assertEqual(pair['conversation'], [
            {'speaker': 'contact', 'text': 'about the ending'},
            {'speaker': 'contact', 'text': 'what did you think?'},
            {'speaker': 'user', 'text': 'absolute chaos 😂'},
        ])

    def test_enough_same_contact_examples_exclude_general_and_cap_at_five(self):
        rows = []
        for index, word in enumerate(('wild', 'chaos', 'unreal', 'bruh', 'nuts', 'crazy')):
            rows += [msg(index * 2 + 1, 'what did you think about the ending'),
                     msg(index * 2 + 2, word, True)]
        rows += [msg(13, 'what did you think about the ending', chat='b'),
                 msg(14, 'other contact wording', True, chat='b'),
                 msg(15, 'what did you think about the ending')]
        pairs = examples_for(rows, rows[-1], CHATS)
        self.assertEqual(len(pairs), 5)
        self.assertEqual({pair['recipient_key'] for pair in pairs}, {ALICE.key})
        self.assertEqual(pairs[0]['reply'], 'crazy')
        self.assertEqual(pairs, examples_for(rows, rows[-1], CHATS))
        self.assertNotIn('g-12', [pair['source_guids'][-1] for pair in
                                 examples_for(rows, rows[-1], CHATS, exclude_guids={'g-12'})])

    def test_recent_chat_context_keeps_chronology_speakers_and_current_turn(self):
        rows = [msg(n, 'a thought' if n % 2 else 'fair enough', n % 2 == 0) for n in range(1, 20)]
        rows += [msg(20, 'other person', chat='b'), msg(21, 'latest question'),
                 msg(22, 'hidden reply', True), msg(23, 'future text')]
        provider = FakeProvider('bruh')
        result = generate(rows, CHATS, rows[-3], provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        # The complete observed active session, followed by the current incoming turn.
        self.assertEqual(result['context_guids'], [f'g-{n}' for n in range(1, 20)] + ['g-21'])
        self.assertEqual([row['speaker'] for row in prompt['CURRENT_SESSION']],
                         ['user' if n % 2 == 0 else 'contact' for n in range(1, 20)] + ['contact'])
        encoded = json.dumps(provider.prompts + provider.claim_reviews)
        for forbidden in ('hidden reply', 'future text', 'other person'):
            self.assertNotIn(forbidden, encoded)

    def test_duplicates_sensitive_pairs_stale_pairs_and_ambiguous_senders_are_excluded(self):
        target = replace(msg(30, 'thanks'), occurred_at=200000)
        rows = [
            msg(1, 'thanks'), msg(2, 'no worries', True),
            msg(3, 'thanks again'), msg(4, 'NO WORRIES', True),
            msg(5, 'password is private'), msg(6, 'credential reply must not appear', True),
            msg(7, 'thanks'), replace(msg(8, 'stale response', True), occurred_at=100000),
            msg(9, 'thanks'), replace(msg(10, 'system boundary'), direction=Direction.SYSTEM, sender=None),
            msg(11, 'system reply must not appear', True),
            replace(msg(12, 'thanks'), sender=BOB), msg(13, 'wrong sender response', True),
            replace(msg(14, 'thanks'), occurred_at=300000),
            replace(msg(15, 'future timestamp response', True), occurred_at=300001), target,
        ]
        pairs = examples_for(rows, target, CHATS)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['reply'], 'no worries')

    def test_context_loader_rejects_boundary_and_source_identity_mismatches(self):
        class Reader:
            source_id = 'fictional'
            generation = 'one'
            def read_before(self, *args, **kwargs):
                return self.batch
        reader = Reader()
        for batch in (
            Batch('wrong', 'one', (ROWS[0],)),
            Batch('fictional', 'two', (ROWS[0],)),
            Batch('fictional', 'one', (ROWS[-1],)),  # Includes target itself.
            Batch('fictional', 'one', (ROWS[2],)),  # Other chat.
        ):
            reader.batch = batch
            with self.assertRaises(DraftError):
                case_history(ROWS, ROWS[-1], reader)


class ContextHydrationTests(unittest.TestCase):
    setUp = test_review.ReviewTests.setUp
    def test_context_hydrates_incoming_messages_missing_from_style_sample(self):
        add(self.db, 41, text='another part of the question', date=700000041)
        add(self.db, 42, text='which part did you mean?', date=700000042)
        add(self.db, 43, text='unique held out actual', is_from_me=1, date=700000043)
        with MacOSMessagesReader(self.path) as reader:
            chats = reader.individual_chats()
            all_rows = reader.read_after(0, 100).messages
            incoming = all_rows[-2]
            # Mimic a sparse global sample, with recent unanchored incoming content omitted.
            sampled = [all_rows[0], all_rows[1], incoming]
            prepared = case_history(sampled, incoming, reader)
        self.assertIn(41, [m.sequence for m in prepared])
        self.assertNotIn(43, [m.sequence for m in prepared])
        provider = FakeProvider('what did you mean there?')
        generate(prepared, chats, incoming, provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertIn('another part of the question', json.dumps(prompt['CURRENT_SESSION']))
        self.assertNotIn('unique held out actual', json.dumps(provider.prompts))
        self.assertNotIn('unique held out actual', json.dumps(provider.claim_reviews))


if __name__ == '__main__':
    unittest.main()
