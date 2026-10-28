"""Fictional timestamps and text only; no Messages or model access."""
import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest
from imessage_agent.drafting import generate, session_context
from imessage_agent.sessions import SessionConfig, segment, timing_reason
from test_drafting import CHATS, FakeProvider, msg


def at(n, text, seconds, owner=False):
    return replace(msg(n, text, owner), occurred_at=seconds)


class SessionTests(unittest.TestCase):
    def test_days_apart_are_separate_even_with_shared_words(self):
        rows = [at(1, 'dinner tonight', 100), at(2, 'sounds fun', 200, True),
                at(3, 'dinner tonight', 300000)]
        result = segment(rows, rows[-1])
        self.assertEqual(result['state'], 'NEW_SESSION')
        self.assertEqual(len(result['previous']), 1)
        self.assertEqual([m.sequence for m in result['current']], [3])

    def test_midnight_is_not_an_absolute_boundary(self):
        zone = ZoneInfo('America/Chicago')
        start = datetime(2026, 10, 26, 23, 59, tzinfo=zone).timestamp()
        rows = [at(1, 'homework question', start), at(2, 'which question', start + 120, True)]
        self.assertEqual(len(segment(rows, rows[-1])['previous']), 0)
        later = at(3, 'dinner plans', start + 7200)
        self.assertTrue(segment([rows[0], later], later)['starts_new_session'])
        self.assertFalse(segment([rows[0], later], later, SessionConfig(timezone='UTC'))['starts_new_session'])

    def test_timezone_dst_offsets_are_preserved_in_context(self):
        zone = ZoneInfo('America/Chicago')
        t = datetime(2026, 11, 1, 1, 50, tzinfo=zone, fold=0).timestamp()
        rows = [at(1, 'the movie ending', t), at(2, 'wild ending', t + 1200)]
        _, _, context, _, _ = session_context(rows, CHATS, rows[-1])
        self.assertTrue(context[0]['timestamp'].endswith('-05:00'))
        self.assertTrue(context[1]['timestamp'].endswith('-06:00'))
        self.assertEqual(context[1]['occurred_at'] - context[0]['occurred_at'], 1200)

    def test_same_day_switches_and_speaker_changes(self):
        rows = [at(1, 'homework assignment', 100), at(2, 'which class', 110, True),
                at(3, 'dinner plans tonight', 200)]
        result = segment(rows, rows[-1])
        self.assertEqual(result['state'], 'TOPIC_SWITCH')
        self.assertEqual(result['boundaries']['g-3'], 'topic_switch')
        self.assertEqual(len(result['current']), 3)
        self.assertFalse(result['starts_new_session'])
        later = at(4, 'btw did you see that', 210)
        self.assertEqual(segment(rows + [later], later)['state'], 'TOPIC_SWITCH')

    def test_explicit_reference_retrieves_only_historical_evidence(self):
        rows = [at(1, 'the movie ending was wild', 100), at(2, 'what a twist', 110, True),
                at(3, 'remember the movie ending', 300000),
                at(4, 'future personal fact', 300100, True)]
        provider = FakeProvider('wild twist lol')
        draft = generate(rows, CHATS, rows[2], provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertTrue(draft['references_earlier'])
        self.assertEqual(len(prompt['CURRENT_SESSION']), 1)
        self.assertTrue(prompt['HISTORICAL_CONTEXT'])
        self.assertTrue(all(m['historical'] for m in prompt['HISTORICAL_CONTEXT']))
        self.assertIn('STYLE_EXAMPLES', prompt)
        self.assertNotIn('future personal fact', json.dumps(provider.prompts + provider.claim_reviews))

    def test_unrelated_history_is_not_factual_context(self):
        rows = [at(1, 'homework assignment', 100), at(2, 'dinner tonight', 300000)]
        _, current, _, historical, _ = session_context(rows, CHATS, rows[-1])
        self.assertEqual([m.sequence for m in current], [2])
        self.assertEqual(historical, [])

    def test_ending_acknowledgments_skip_the_model_and_any_reply(self):
        for text in ('thanks', 'got it', 'sounds good', 'goodnight', '👍'):
            rows = [at(1, 'the movie was wild', 100, True), at(2, text, 110)]
            provider = FakeProvider()
            draft = generate(rows, CHATS, rows[-1], provider)
            self.assertEqual(draft['conversation_state'], 'LIKELY_ENDED', text)
            self.assertEqual(draft['classification'], 'NO_REPLY')
            self.assertFalse(provider.prompts)
            self.assertFalse(draft['auto_sending'])
        rows = [at(1, 'thanks', 100)]
        self.assertEqual(segment(rows, rows[0])['state'], 'UNCERTAIN')

    def test_bursts_and_staleness_veto_generation_only_in_live_mode(self):
        rows = [at(1, 'hey', 100), at(2, 'another question', 102)]
        self.assertEqual(timing_reason(rows, rows[0]), 'newer_message_stale_draft')
        self.assertEqual(timing_reason(rows, rows[-1], now=110), 'incoming_burst_settling')
        self.assertIsNone(timing_reason(rows, rows[-1], now=120))
        provider = FakeProvider()
        draft = generate(rows, CHATS, rows[-1], provider, now=110)
        self.assertEqual(draft['reason'], 'incoming_burst_settling')
        self.assertFalse(provider.prompts)
        rows.append(at(3, 'already replied', 115, True))
        self.assertEqual(timing_reason(rows, rows[0]), 'manual_reply_after_source')
        # Historical evaluation excludes all future evidence, including vetoes.
        generate(rows, CHATS, rows[0], provider)
        self.assertEqual(len(provider.prompts), 1)
        self.assertNotIn('already replied', json.dumps(provider.prompts))

    def test_future_timestamp_and_other_chat_cannot_leak(self):
        rows = [at(1, 'future timestamp', 900), at(2, 'current question', 100),
                replace(at(3, 'other chat', 90), chat_id='b')]
        _, current, _, historical, _ = session_context(rows, CHATS, rows[1])
        self.assertEqual([m.sequence for m in current], [2])
        self.assertFalse(historical)

    def test_threshold_validation_and_truncated_sessions_are_uncertain(self):
        for value in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                SessionConfig(gap_seconds=value)
        rows = [at(n, 'a shared topic', n) for n in range(1, 103)]
        _, _, _, _, result = session_context(rows, CHATS, rows[-1])
        self.assertEqual(result['state'], 'UNCERTAIN')
        self.assertEqual(result['reason'], 'current_session_truncated')
