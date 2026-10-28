"""Fictional regression: a hedged answer is not an invitation to ask it back."""
from dataclasses import replace
import json
import unittest
from imessage_agent.drafting import generate
from imessage_agent.models import Direction
from imessage_agent.style import current_turn, examples_for, intent, pairs
from imessage_agent.style_edits import recurring
from test_drafting import CHATS, FakeProvider, msg


class AnswerReactionTests(unittest.TestCase):
    def test_self_hedges_are_not_opinion_requests(self):
        for text in ('A calendar tool\nI think?', 'A reminder app I guess?',
                     'An organizer I’m not sure?'):
            self.assertEqual(intent(text, 'what is it'), 'information_answer')
        self.assertEqual(intent('A calendar tool I think?'), 'statement')
        self.assertEqual(intent('What do you think?'), 'opinion')
        self.assertEqual(intent('What do you think?', 'what is it'), 'opinion')
        self.assertEqual(intent('Is it useful?', 'what is it'), 'question')
        self.assertEqual(intent('Where is it?', 'what is it'), 'logistics')
        self.assertEqual(intent('thanks so much', 'what is it'), 'gratitude')
        self.assertEqual(intent('hey', 'what is it'), 'greeting')
        self.assertEqual(intent('sounds good', 'what is it'), 'acknowledgment')
        self.assertNotEqual(intent('im doing well', 'how are you'), 'information_answer')
        self.assertNotEqual(intent('reading a book', 'what are you doing'), 'information_answer')

    def rows(self):
        return [msg(1, 'what does that tool do', True),
                msg(2, 'A reminder app I think?'), msg(3, 'hmmm', True),
                replace(msg(4, 'what is this', True), occurred_at=100000),
                replace(msg(5, 'It tracks chores'), occurred_at=100010),
                replace(msg(6, 'A calendar tool I think?'), occurred_at=100020)]

    def test_retrieval_retains_owner_question_and_matches_answer_reactions(self):
        rows = self.rows()
        examples = examples_for(rows, rows[-1], CHATS, exclude_guids={'g-4', 'g-5', 'g-6'})
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]['intent'], 'information_answer')
        self.assertEqual(examples[0]['reply'], 'hmmm')
        self.assertEqual([m['speaker'] for m in examples[0]['conversation']], ['user', 'contact', 'user'])
        self.assertEqual(examples[0]['source_guids'], ['g-1', 'g-2', 'g-3'])
        self.assertTrue(all(m['timestamp'] for m in examples[0]['conversation']))

    def test_prompt_asks_for_a_reaction_without_encoding_the_users_feedback_as_a_template(self):
        rows, provider = self.rows(), FakeProvider('gotcha')
        generate(rows, CHATS, rows[-1], provider, pipeline='fingerprint')
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertEqual(prompt['STYLE_FINGERPRINT']['situation'], 'information_answer')
        self.assertIn('do not repeat the explanation', prompt['response_goal'])
        self.assertIn(prompt['response_goal'], provider.prompts[0][0]['content'])
        self.assertIn('without current-session evidence', prompt['response_goal'])
        self.assertNotIn('Hmmmm', provider.prompts[0][0]['content'])
        self.assertNotIn('Interesting', provider.prompts[0][0]['content'])
        question = replace(rows[-1], body='What do you think?')
        provider = FakeProvider('gotcha')
        generate(rows[:-1], CHATS, question, provider, pipeline='fingerprint')
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertEqual(prompt['STYLE_FINGERPRINT']['situation'], 'opinion')
        self.assertNotIn('response_goal', prompt)

    def test_an_old_question_does_not_turn_a_new_session_into_an_answer(self):
        rows = [msg(1, 'what does it do', True),
                replace(msg(2, 'An organizer I think?'), occurred_at=300000),
                replace(msg(3, 'interesting', True), occurred_at=300010)]
        pair, = pairs(rows, CHATS)
        self.assertEqual(pair['intent'], 'statement')
        self.assertEqual(pair['source_guids'], ['g-2', 'g-3'])
        context = [dict(direction='outbound', body='what is this'),
                   dict(direction='inbound', body='An organizer I think?', session_start=True)]
        self.assertEqual(intent(*current_turn(context)), 'statement')
        boundary = replace(msg(2, None), direction=Direction.SYSTEM, sender=None)
        rows = [msg(1, 'what is this', True), boundary, msg(3, 'An organizer I think?'), msg(4, 'interesting', True)]
        pair, = pairs(rows, CHATS)
        self.assertEqual(pair['intent'], 'statement')

    def test_edit_corrections_use_the_same_answer_intent(self):
        context = [dict(direction='outbound', body='what is this'),
                   dict(direction='inbound', body='A calendar tool I think?')]
        self.assertEqual(intent(*current_turn(context)), 'information_answer')
        edits = [dict(source_id='source', source_guid=str(i), original='That is helpful',
                      edited='hmm', accepted_at=10 + i, context=context) for i in range(2)]
        correction, = recurring(edits)
        self.assertEqual(correction['intents'], {'information_answer': 2})

    def test_a_reaction_cannot_claim_prior_experience_without_claim_review(self):
        rows, provider = self.rows(), FakeProvider('ive used it', category='personal_fact')
        draft = generate(rows, CHATS, rows[-1], provider, pipeline='fingerprint')
        self.assertEqual(draft['classification'], 'NEEDS_USER_INPUT')
        self.assertFalse(draft['auto_sending'])
