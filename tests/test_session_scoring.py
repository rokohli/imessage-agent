"""The local judge is explicitly separate from human ratings and style."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from imessage_agent.ollama import DraftError
from test_drafting import CHATS, FakeProvider, msg

scorer = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/score_session_comparison.py'))


class SessionScoringTests(unittest.TestCase):
    def test_schema_validation_rejects_coercion_and_missing_scores(self):
        score = dict(relevance=4, naturalness=3, session_boundary_correct=True,
                     topic_switch_correct=None, unnecessary_reply=False, invented_personal_facts=False)
        self.assertEqual(scorer['validate'](score), score)
        for bad in ({**score, 'relevance': True}, {**score, 'naturalness': 6},
                    {**score, 'unnecessary_reply': None}, {}):
            with self.assertRaises(DraftError):
                scorer['validate'](bad)

    def test_judge_sees_no_heldout_or_future_message_and_never_rates_humans(self):
        incoming, actual = msg(2, 'hey'), msg(3, 'heldout actual', True)
        frozen = [dict(source_case=1, incoming=incoming, actual=actual,
                       preceding=[msg(1, 'past question'), incoming, actual, msg(4, 'future text')])]
        provider = FakeProvider()
        provider._chat = Mock(return_value=dict(relevance=4, naturalness=3, session_boundary_correct=True,
            topic_switch_correct=None, unnecessary_reply=False, invented_personal_facts=False))
        comparison = Mock()
        comparison.session.return_value = dict(session_study=True, preparation_complete=True, variants={'A': {}})
        snapshot = dict(source_case=1, source_guid=incoming.guid, held_out_guid=actual.guid, variant='A',
            draft=dict(reply='hi', conversation_state='ACTIVE_CONVERSATION', starts_new_session=False),
            heuristic_scores=dict(style=.5))
        comparison.cases.return_value = [dict(snapshot_json=json.dumps(snapshot))]
        # run_path functions retain their globals dictionary.
        function = scorer['score_comparison']
        with patch.dict(function.__globals__, _frozen_cases=lambda *args: ({}, CHATS, frozen, {})):
            report = function(None, None, comparison, provider)
        encoded = json.dumps(provider._chat.call_args.args)
        self.assertNotIn('heldout actual', encoded)
        self.assertNotIn('future text', encoded)
        self.assertEqual(report['variants']['A']['conversation_quality']['naturalness']['mean'], 3)
        self.assertEqual(report['variants']['A']['style_similarity_proxy'], 1.0)
        self.assertIn('not_human', report['basis'])
        comparison.rate.assert_not_called()
