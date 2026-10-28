"""Synthetic owner wording only; no personal history, model or transport."""
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock
from imessage_agent.drafting import build_profiles, generate
from imessage_agent.models import Direction
from imessage_agent.ollama import DraftError
from imessage_agent.style import fingerprint, examples_for, intent, pairs
from imessage_agent.style_edits import recurring
from test_drafting import CHATS, ALICE, BOB, FakeProvider, msg


class FingerprintTests(unittest.TestCase):
    def rows(self):
        return [msg(1, 'that ending was funny lol'), msg(2, 'nah thats wild 😂', True),
                msg(3, 'what did you think lol'), msg(4, 'nah thats wild 😂', True),
                msg(5, 'that movie was funny lol'), msg(6, 'nah thats wild 😂', True),
                msg(7, 'are you free'), msg(8, 'idk rn', True),
                msg(9, 'hello'), msg(10, 'hey there', True),
                msg(11, 'thats funny lol')]

    def test_frequencies_context_rare_forms_and_contact_profiles(self):
        rows = self.rows() + [msg(12, 'uniqueword', True, chat='b'),
                             msg(13, 'password secret', True), msg(14, 'not owner slang')]
        profiles = build_profiles(rows, CHATS)
        fp = profiles[ALICE.key]['fingerprint']
        forms = {v['text']: v for v in fp['observed_forms']}
        self.assertEqual(forms['nah']['count'], 3)
        self.assertEqual(forms['nah']['frequency'], 'common')
        self.assertEqual(forms['idk']['frequency'], 'rare')
        self.assertIn('nah thats wild', [p['text'] for p in fp['expressions']])
        self.assertEqual(fp['capitalization']['lowercase'], 5)
        self.assertEqual(fp['emoji'][0]['count'], 3)
        self.assertNotIn('uniqueword', json.dumps(fp))
        self.assertNotIn('secret', json.dumps(profiles))
        self.assertNotIn('not owner slang', json.dumps(profiles))
        self.assertIn('humor', fp['intents'])
        self.assertTrue(fp['intents']['humor']['examples'][0]['timestamp'])
        self.assertEqual(profiles[BOB.key]['fingerprint']['sample_count'], 1)

    def test_intent_precedes_contact_and_no_unrelated_slang_padding(self):
        rows = [msg(1, 'thanks for helping', chat='b'), msg(2, 'anytime', True, chat='b'),
                msg(3, 'that ending was funny lol'), msg(4, 'lmaooo', True),
                msg(5, 'thank you for that')]
        examples = examples_for(rows, rows[-1], CHATS)
        self.assertEqual([p['reply'] for p in examples], ['anytime'])
        self.assertEqual(examples[0]['recipient_key'], BOB.key)
        self.assertEqual(examples[0]['intent'], 'gratitude')

    def test_examples_do_not_cross_sessions_system_or_topic_boundaries(self):
        rows = [msg(1, 'thanks'), replace(msg(2, 'earlier topic'), direction=Direction.SYSTEM, sender=None),
                msg(3, 'np', True), msg(4, 'thanks'),
                replace(msg(5, 'delayed reply', True), occurred_at=300000)]
        self.assertEqual(pairs(rows, CHATS), [])
        rows = [msg(1, 'homework question'), msg(2, 'btw dinner plans'), msg(3, 'sounds good', True)]
        pair, = pairs(rows, CHATS)
        self.assertEqual(pair['incoming'], 'btw dinner plans')

    def test_future_and_same_case_edits_do_not_enter_style_evidence(self):
        target = msg(11, 'thats funny lol')
        edit = dict(source_id='source', source_guid='g-1', source_sequence=1, recipient_key=ALICE.key,
            edited='specific owner expression', original='formal phrase', context=[dict(direction='inbound', body='funny lol')],
            occurred_at=1, accepted_at=10)
        candidates = examples_for(self.rows(), target, CHATS, accepted_edits=[edit])
        self.assertEqual(candidates[0]['origin'], 'accepted_edit')
        for invalid in ({**edit, 'accepted_at': 99}, {**edit, 'accepted_at': None},
                        {**edit, 'source_guid': target.guid}, {**edit, 'occurred_at': 999}):
            candidates = examples_for(self.rows(), target, CHATS, accepted_edits=[invalid])
            self.assertFalse(any(p['origin'] == 'accepted_edit' for p in candidates))

    def test_new_prompt_uses_observed_wording_without_brevity_or_demographic_rules(self):
        provider = FakeProvider('lol no way')
        generate(self.rows(), CHATS, self.rows()[-1], provider, pipeline='fingerprint')
        system, data = provider.prompts[0]
        payload = json.loads(data['content'])
        self.assertIn('STYLE_FINGERPRINT', payload)
        self.assertNotIn('median_words', data['content'])
        for forbidden in ('fewer than 15', 'college student', 'youthful', 'use casual slang'):
            self.assertNotIn(forbidden, system['content'])
        self.assertIn('DEFAULT TO NO PUNCTUATION', system['content'])
        self.assertNotIn(ALICE.address, json.dumps(provider.prompts))

    def test_two_pass_rewrite_is_meaning_checked_and_final_claim_validated(self):
        provider = FakeProvider()
        provider.chat = Mock(side_effect=[dict(reply='That was surprising', needs_personal_facts=False),
                                         dict(reply='nah thats wild', needs_personal_facts=False)])
        provider.preserves_meaning = Mock(return_value=True)
        draft = generate(self.rows(), CHATS, self.rows()[-1], provider, pipeline='style_revision')
        self.assertEqual(draft['reply'], 'nah thats wild')
        provider.preserves_meaning.assert_called_once_with('That was surprising', 'nah thats wild')
        self.assertEqual(provider.claim_reviews[-1][1], 'nah thats wild')
        self.assertIn('style_revision', draft['quality_flags'])
        self.assertFalse(draft['auto_sending'])
        self.assertEqual(draft['first_pass_reply'], 'That was surprising')

    def test_changed_meaning_failed_guard_and_missing_facts_fail_closed(self):
        for verdict in (False, DraftError('invalid')):
            provider = FakeProvider()
            provider.chat = Mock(side_effect=[dict(reply='oh wow', needs_personal_facts=False),
                                              dict(reply='ill come tomorrow', needs_personal_facts=False)])
            provider.preserves_meaning = Mock(return_value=verdict if verdict is False else None,
                                             side_effect=verdict if isinstance(verdict, Exception) else None)
            draft = generate([msg(1, 'hello')], CHATS, msg(1, 'hello'), provider, pipeline='style_revision')
            self.assertEqual(draft['reply'], 'oh wow')
            self.assertEqual(provider.claim_reviews[-1][1], 'oh wow')
        provider = FakeProvider('', needs_personal_facts=True)
        generate([msg(1, 'are you free')], CHATS, msg(1, 'are you free'), provider, pipeline='style_revision')
        self.assertEqual(len(provider.prompts), 1)

    def test_rewrite_cannot_bypass_claim_validation_even_if_guard_is_wrong(self):
        provider = FakeProvider()
        provider.chat = Mock(side_effect=[dict(reply='oh wow', needs_personal_facts=False),
                                          dict(reply='ill come tomorrow', needs_personal_facts=False)])
        draft = generate([msg(1, 'hello')], CHATS, msg(1, 'hello'), provider, pipeline='style_revision')
        self.assertEqual(draft['classification'], 'NEEDS_USER_INPUT')
        self.assertEqual(draft['claim_category'], 'commitment')
        self.assertFalse(draft['auto_sending'])

    def test_recurring_corrections_need_independent_accepted_messages(self):
        edit = dict(source_id='source', source_guid='one', original='Absolutely', edited='yea',
                    context=[dict(direction='inbound', body='hello')], accepted_at=10)
        self.assertEqual(recurring([{**edit, 'accepted_at': None}]), [])
        self.assertEqual(recurring([edit, edit]), [])
        result, = recurring([edit, {**edit, 'source_guid': 'two'}])
        self.assertEqual(result['support'], 2)
        self.assertEqual(result['edited'], 'yea')

    def test_accepted_edit_punctuation_never_authorizes_marks_and_future_words_stay_out(self):
        target = msg(11, 'thats funny lol')
        edits = [dict(source_id='source', source_guid='old-' + str(n), source_sequence=n,
            recipient_key=ALICE.key, original='How surprising.', edited='nah thats wild.',
            context=[dict(direction='inbound', body='funny lol')], occurred_at=n, accepted_at=n + .5)
            for n in range(1, 4)]
        provider = FakeProvider('lol no way')
        rows = self.rows() + [msg(12, 'futureprivateword', True), replace(msg(4, 'futuretimestampword', True), occurred_at=999)]
        generate(rows, CHATS, target, provider, pipeline='fingerprint', accepted_edits=edits)
        payload = json.loads(provider.prompts[0][1]['content'])
        self.assertEqual(payload['punctuation_policy']['allowed_marks'], [])
        self.assertNotIn('futureprivateword', json.dumps(provider.prompts))
        self.assertNotIn('futuretimestampword', json.dumps(provider.prompts))

    def test_local_meaning_review_rejects_nonboolean_or_extra_fields(self):
        from imessage_agent.ollama import Ollama, ModelConfig
        provider = Ollama(ModelConfig())
        for invalid in ({'preserved': 'true'}, {'preserved': True, 'extra': False}, {}):
            provider._chat = Mock(return_value=invalid)
            with self.assertRaises(DraftError):
                provider.preserves_meaning('possibly', 'definitely')
        provider._chat = Mock(return_value={'preserved': False})
        self.assertFalse(provider.preserves_meaning('possibly', 'definitely'))
