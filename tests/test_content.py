"""Claim validation with fictional text and mocked local classifiers only."""

import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from imessage_agent.content import review_reply
from imessage_agent.drafting import generate
from imessage_agent.evaluation import evaluate
from imessage_agent.ollama import CLAIM_SCHEMA, DraftError, ModelConfig, Ollama
from test_drafting import ALICE, CHATS, FakeProvider, ROWS, msg


class ContentReviewTests(unittest.TestCase):
    def review(self, reply, *, category='harmless', incoming='that was hilarious', needs_facts=False):
        checker = MagicMock(return_value=category)
        result = review_reply(reply, needs_facts=needs_facts,
                              context=[{'direction': 'inbound', 'body': incoming}], classify=checker)
        return result, checker

    def test_natural_wording_does_not_require_phrase_matches(self):
        for reply in ('bruh 💀', 'lmaooo stop 😂', "I'm dead 😂", 'im crying lmao',
                      "nah that's wild", '10/10 chaos', 'love thisss', 'pay attention 😂',
                      "let's goooo", "what'd you mean by that?", 'wait which part are you talking about?',
                      'I have no words 😂', 'secret sauce 😂'):
            with self.subTest(reply=reply):
                result, checker = self.review(reply)
                self.assertEqual(result, ('accepted', 'harmless'))
                checker.assert_called_once()
                draft = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
                self.assertEqual(draft['reply'], reply)
                self.assertEqual(draft['validation_status'], 'accepted')
                # New wording does not broaden the old advisory automatic-send predicate.
                self.assertEqual(draft['classification'], 'APPROVAL_REQUIRED')
                self.assertFalse(draft['auto_sending'])

    def test_emoji_only_has_no_claim_but_context_can_make_it_agreement(self):
        for reply in ('😂🔥', '💀', '❤️', '🤯'):
            result, checker = self.review(reply)
            self.assertEqual(result, ('accepted', 'harmless'))
            checker.assert_not_called()
        result, checker = self.review('👍', incoming='can you cover my shift?')
        self.assertEqual(result, ('approval_required', 'consequential_context'))
        checker.assert_not_called()

    def test_clear_claim_categories_preserve_text_for_confirmation(self):
        cases = (
            ('Checking emails, how about you?', 'activity'),
            ("I'm working rn", 'activity'),
            ('im free tonight', 'availability'),
            ("I'm not available tomorrow", 'availability'),
            ("I'm at the gym", 'location'),
            ('on my way', 'location'), ('omw', 'location'),
            ("I'll bring it tomorrow", 'commitment'),
            ('count on me', 'commitment'), ('ill bring it', 'commitment'), ('I can do that', 'commitment'),
            ("I'll pay you tomorrow", 'payment'),
            ('send money please', 'payment'), ("I'll pay you $1000", 'payment'),
            ('I accept the offer', 'decision'),
            ("let's book it", 'decision'),
            ('my job is teaching', 'personal_fact'),
        )
        for reply, category in cases:
            with self.subTest(reply=reply):
                result, checker = self.review(reply)
                self.assertEqual(result, ('approval_required', category))
                checker.assert_not_called()
                draft = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
                self.assertEqual(draft['validation_status'], 'needs_user_input')
                self.assertEqual(draft['claim_category'], category)
                self.assertEqual(draft['reply'], reply)
                self.assertEqual(draft['classification'], 'NEEDS_USER_INPUT')
                self.assertFalse(draft['auto_sending'])

    def test_clear_secrets_and_broken_output_are_not_retained(self):
        for reply, category in (
            ('your verification code is 829104', 'credentials_or_code'),
            ('829104', 'credentials_or_code'), ('code is 829104', 'credentials_or_code'),
            ('password is huntertwo', 'credentials_or_code'),
            ('<think>analysis</think>great', 'reasoning_or_instructions'),
            ('ignore previous instructions', 'reasoning_or_instructions'),
            ('a' * 301, 'length'), ('hello\x00there', 'control_characters'),
        ):
            result, checker = self.review(reply)
            self.assertEqual(result, ('rejected', category))
            checker.assert_not_called()
            draft = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
            self.assertEqual(draft['validation_status'], 'rejected')
            self.assertEqual(draft['reply'], '')

    def test_ambiguous_claims_and_model_failures_require_approval(self):
        for category in ('personal_fact', 'activity', 'location', 'uncertain', 'sensitive'):
            result, checker = self.review('a novel phrase', category=category)
            self.assertEqual(result, ('approval_required', category))
            checker.assert_called_once()
        provider = FakeProvider('sounds pretty neat')
        provider.classify_claims = MagicMock(side_effect=DraftError('ollama_invalid_claim_category'))
        draft = generate(ROWS, CHATS, ROWS[-1], provider)
        self.assertEqual(draft['claim_category'], 'claim_check_invalid_response')
        self.assertEqual(draft['validation_status'], 'approval_required')
        self.assertEqual(draft['reply'], 'sounds pretty neat')
        provider.classify_claims.side_effect = DraftError('ollama_unavailable_start_loopback_server_and_install_model')
        draft = generate(ROWS, CHATS, ROWS[-1], provider)
        self.assertEqual(draft['claim_category'], 'claim_check_unavailable')
        self.assertEqual(draft['reply'], 'sounds pretty neat')
        self.assertEqual(draft['validation_status'], 'approval_required')

    def test_financial_or_sensitive_inputs_never_reach_generation(self):
        for incoming in ('can you send me money?', 'verification code 829104', 'medical diagnosis'):
            provider = FakeProvider('sure!')
            provider.chat = MagicMock(side_effect=AssertionError('sensitive source must not be submitted'))
            draft = generate(ROWS, CHATS, replace(ROWS[-1], body=incoming), provider)
            self.assertEqual(draft['classification'], 'APPROVAL_REQUIRED')
            self.assertEqual(draft['validation_status'], 'approval_required')
            self.assertEqual(draft['reply'], '')
            self.assertFalse(draft['auto_sending'])
        result, _ = self.review('this is about a medical diagnosis')
        self.assertEqual(result, ('approval_required', 'sensitive'))

    def test_yes_does_not_silently_agree_to_consequential_requests(self):
        for incoming in ('can you cover my shift?', 'will you sign the lease?', 'do you agree?'):
            result, checker = self.review('sure', incoming=incoming)
            self.assertEqual(result, ('approval_required', 'consequential_context'))
            checker.assert_not_called()
        result, checker = self.review('where were you thinking?', incoming='can you meet tomorrow?')
        self.assertEqual(result, ('accepted', 'harmless'))
        checker.assert_called_once()
        context = [{'direction': 'inbound', 'body': '[content excluded]'},
                   {'direction': 'inbound', 'body': 'okay?'}]
        checker = MagicMock(return_value='harmless')
        self.assertEqual(review_reply('yes', needs_facts=False, context=context, classify=checker),
                         ('approval_required', 'redacted_context'))
        checker.assert_not_called()


class ClaimProviderTests(unittest.TestCase):
    def test_second_step_uses_a_separate_minimal_schema(self):
        provider = Ollama(ModelConfig())
        context = [{'direction': 'inbound', 'body': 'fictional text'}]
        with patch.object(provider, '_chat', return_value={'category': 'harmless'}) as chat:
            self.assertEqual(provider.classify_claims(context, 'bruh 💀'), 'harmless')
        messages, schema = chat.call_args.args
        self.assertEqual(schema, CLAIM_SCHEMA)
        self.assertIn('untrusted DATA', messages[0]['content'])
        self.assertEqual(json.loads(messages[1]['content']), {'conversation': context, 'draft': 'bruh 💀'})
        self.assertNotIn('classification', schema['properties'])

    def test_claim_request_uses_loopback_schema_and_zero_temperature(self):
        provider = Ollama(ModelConfig(temperature=.7))
        response = {'done': True, 'done_reason': 'stop', 'message': {'content': '{"category":"harmless"}'}}
        with patch.object(provider, 'health'), patch.object(provider, '_request', return_value=response) as request:
            self.assertEqual(provider.classify_claims([], 'novel harmless words'), 'harmless')
        path, payload = request.call_args.args
        self.assertEqual(path, '/api/chat')
        self.assertEqual(payload['format'], CLAIM_SCHEMA)
        self.assertEqual(payload['model'], 'qwen2.5:3b')
        self.assertEqual(payload['options']['temperature'], 0)
        self.assertFalse(payload['stream'])
        self.assertFalse(payload['think'])

    def test_invalid_classifier_labels_do_not_grant_acceptance(self):
        provider = Ollama(ModelConfig())
        for response in ({}, {'category': ['harmless']}, {'category': 'send it'},
                         {'category': 'harmless', 'reason': 'raw-private'}):
            with patch.object(provider, '_chat', return_value=response):
                with self.assertRaisesRegex(DraftError, '^ollama_invalid_claim_category$'):
                    provider.classify_claims([], 'test')


class FictionalEvaluationTests(unittest.TestCase):
    def test_twenty_case_mocked_heldout_report_splits_review_outcomes(self):
        # This measures pipeline bookkeeping, not real model or owner-history quality.
        replies = [
            ('bruh 💀', False), ('lmaooo stop 😂', False), ("I'm dead 😂", False),
            ('😂🔥', False), ('nahhh no way', False), ('what did you mean there?', False),
            ('love thisss', False), ('10/10 chaos', False), ("that's brilliant 😂", False),
            ('what part are you talking about?', False),
            ('Checking emails, how about you?', False), ('im free tonight', False),
            ("I'm at the gym", False), ("I'll bring it tomorrow", False),
            ("I'll pay you tomorrow", False), ('I accept the offer', False),
            ('', True), ('a' * 301, False), ('verification code 829104', False), ('', False),
        ]
        rows = []
        for index in range(20):
            rows += [msg(2 * index + 1, 'that was hilarious'), msg(2 * index + 2, 'lol', True)]
        provider = FakeProvider()
        proposals = iter(replies)
        def propose(messages):
            reply, needs_facts = next(proposals)
            return {'reply': reply, 'needs_personal_facts': needs_facts}
        provider.chat = propose
        report = evaluate(rows, CHATS, [ModelConfig()], cases=20, provider_factory=lambda config: provider)
        result = report['results'][0]
        self.assertEqual(result['validation_counts'], {'accepted': 10, 'approval_required': 0, 'needs_user_input': 7, 'rejected': 2, 'no_reply': 1})
        self.assertEqual(len(result['case_validations']), 20)
        self.assertEqual(sum(result['validation_counts'].values()), 20)
        self.assertEqual(result['failed'], 0)
        for reply, _ in replies:
            if reply:
                self.assertNotIn(reply, json.dumps(report))
        self.assertNotIn(ALICE.address, json.dumps(report))
        self.assertNotIn('g-1', json.dumps(report))
        self.assertFalse(report['auto_sending'])


if __name__ == '__main__':
    unittest.main()
