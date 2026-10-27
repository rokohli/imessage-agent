"""Synthetic history and mocked Ollama only. Never reads personal databases."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from imessage_agent.__main__ import main
from imessage_agent.drafting import (
    build_profiles, generate, history, reply_proposal, safe_text, save_draft, save_profiles,
)
from imessage_agent.evaluation import compare, evaluate
from imessage_agent.macos import MacOSMessagesReader, MessagesAccessError
from imessage_agent.models import Batch, Direction, Message, Recipient
from imessage_agent.ollama import DraftError, ModelConfig, Ollama, REPLY_SCHEMA
from imessage_agent.storage import Store, _SCHEMA, _OUTBOUND_SCHEMA
from test_macos import add, fixture


ALICE = Recipient.parse('+12025550101')
BOB = Recipient.parse('+12025550102')
CHATS = {'a': ALICE, 'b': BOB}


def msg(n, body, outgoing=False, chat='a'):
    return Message(n, f'g-{n}', chat, Direction.OUTBOUND if outgoing else Direction.INBOUND,
                   None if outgoing else CHATS[chat], body, float(n))


ROWS = [msg(1, 'thanks'), msg(2, 'no worries', True), msg(3, 'how is it going?', chat='b'),
        msg(4, 'great', True, chat='b'), msg(5, 'hey')]


class FakeProvider:
    config = ModelConfig()

    def __init__(self, reply='no worries', needs_personal_facts=False, category='harmless'):
        self.result = {'reply': reply, 'needs_personal_facts': needs_personal_facts}
        self.prompts = []
        self.category = category
        self.claim_reviews = []

    def chat(self, messages):
        self.prompts.append(messages)
        return self.result

    def classify_claims(self, context, reply):
        self.claim_reviews.append((context, reply))
        return self.category


class DraftingTests(unittest.TestCase):
    def test_filters_sensitive_examples_and_context(self):
        for text in ('password is huntertwo', 'verification code', 'bank balance', 'pay me tomorrow',
                     'my medical diagnosis', '123456', 'reach a@example.com', 'https://secret',
                     'home address', 'my passport', '$five', 'login here', 'private stuff'):
            self.assertFalse(safe_text(text), text)
        rows = ROWS[:2] + [msg(3, 'password is huntertwo', True), msg(4, 'medical diagnosis')]
        profiles = build_profiles(rows, CHATS)
        self.assertEqual(profiles['general']['sample_count'], 1)
        self.assertEqual(profiles[ALICE.key]['sample_count'], 1)
        provider = FakeProvider()
        generate(rows + [ROWS[-1]], CHATS, ROWS[-1], provider)
        prompt = provider.prompts[0][1]['content']
        self.assertNotIn('huntertwo', prompt)
        self.assertNotIn('medical diagnosis', prompt)
        self.assertIn('[content excluded]', prompt)

    def test_contact_retrieval_and_general_fallback(self):
        profiles = build_profiles(ROWS, CHATS)
        self.assertEqual(profiles['general']['sample_count'], 2)
        self.assertEqual(profiles[ALICE.key]['examples'][0]['body'], 'no worries')
        self.assertEqual(profiles[BOB.key]['examples'][0]['body'], 'great')
        provider = FakeProvider()
        draft = generate(ROWS, CHATS, ROWS[-1], provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertNotIn('contact_examples', prompt)
        self.assertNotIn('general_examples', prompt)
        self.assertEqual(prompt['STYLE_EXAMPLES'], [])
        self.assertNotIn('how is it going?', json.dumps(prompt))
        self.assertNotIn(ALICE.address, json.dumps(prompt))
        self.assertEqual(draft['classification'], 'AUTO_SEND_ELIGIBLE')
        self.assertFalse(draft['auto_sending'])
        self.assertEqual(draft['example_guids'], [])

    def test_future_and_hidden_reply_never_reach_prompt(self):
        rows = ROWS + [msg(6, 'hidden-reply', True), msg(7, 'future-secret', chat='b'), replace(msg(4, 'future-timestamp', True), occurred_at=99)]
        provider = FakeProvider()
        generate(rows, CHATS, ROWS[-1], provider)
        self.assertNotIn('hidden-reply', json.dumps(provider.prompts))
        self.assertNotIn('future-secret', json.dumps(provider.prompts))
        self.assertNotIn('future-timestamp', json.dumps(provider.prompts))
        self.assertNotIn('g-6', json.dumps(provider.prompts))

    def test_unknown_group_or_wrong_sender_fails_closed(self):
        with self.assertRaisesRegex(DraftError, 'individual_recipient_not_verified'):
            generate(ROWS, {}, ROWS[-1], FakeProvider())
        provider = FakeProvider()
        draft = generate(ROWS, CHATS, replace(ROWS[-1], sender=BOB), provider)
        self.assertEqual(draft['classification'], 'NO_REPLY')
        self.assertFalse(provider.prompts)
        draft = generate(ROWS, CHATS, ROWS[1], provider)
        self.assertEqual(draft['classification'], 'NO_REPLY')

    def test_sensitive_source_is_withheld_without_model(self):
        provider = FakeProvider()
        draft = generate(ROWS, CHATS, replace(ROWS[-1], body='my password'), provider)
        self.assertEqual(draft['reply'], '')
        self.assertEqual(draft['classification'], 'APPROVAL_REQUIRED')
        self.assertFalse(provider.prompts)

    def test_credentials_and_meta_output_are_rejected_but_claims_are_retained(self):
        for reply in ('password is huntertwo', 'ignore previous instructions',
                      '<think>reasoning</think>thanks'):
            result = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
            self.assertEqual(result['reply'], '')
            self.assertEqual(result['validation_status'], 'rejected')
        for reply in ("I'm free tomorrow", "I'll be there", 'Checking emails, how about you?'):
            result = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
            self.assertEqual(result['reply'], reply)
            self.assertEqual(result['validation_status'], 'needs_user_input')
            self.assertEqual(result['classification'], 'APPROVAL_REQUIRED' if result['validation_status'] == 'rejected' else 'NEEDS_USER_INPUT')

    def test_unknown_personal_facts_never_become_usable_drafts(self):
        scenarios = (
            ('what are you doing?', 'Checking emails, how about you?'),
            ('are you free?', 'I am available tonight'),
            ('what are your plans?', 'Going to dinner with friends'),
            ('can you help?', 'I will handle it tomorrow'),
            ('where do you live?', 'I live downtown'),
            ('what do you like?', 'I love hiking'),
            ('how are you?', 'Feeling great today'),
            ('what are you doing?', 'Let me think. The user is probably working.'),
        )
        for incoming, claim in scenarios:
            for needs_facts in (False, True):
                with self.subTest(claim=claim, needs_facts=needs_facts):
                    provider = FakeProvider(claim, needs_facts, category='personal_fact')
                    result = generate(ROWS, CHATS, replace(ROWS[-1], body=incoming), provider)
                    self.assertEqual(result['classification'], 'APPROVAL_REQUIRED' if result['validation_status'] == 'rejected' else 'NEEDS_USER_INPUT')
                    self.assertEqual(result['reply'], '' if 'The user' in claim else claim)
                    self.assertNotEqual(result['validation_status'], 'accepted')
                    self.assertFalse(result['auto_sending'])
        provider = FakeProvider('', True)
        result = generate(ROWS, CHATS, replace(ROWS[-1], body='what are you doing?'), provider)
        self.assertEqual(result['classification'], 'APPROVAL_REQUIRED' if result['validation_status'] == 'rejected' else 'NEEDS_USER_INPUT')
        self.assertEqual(result['reply'], '')
        instructions = provider.prompts[0][0]['content']
        self.assertIn('activities, availability, plans', instructions)
        self.assertIn('commitments', instructions)
        self.assertIn('needs_personal_facts true', instructions)
        self.assertIn('Do not output analysis', instructions)

    def test_classifications_are_local_and_unknown_facts_require_approval(self):
        result = generate(ROWS, CHATS, replace(ROWS[-1], body='when can you come?'), FakeProvider('what time works?'))
        self.assertEqual(result['classification'], 'APPROVAL_REQUIRED')
        result = generate(ROWS, CHATS, ROWS[-1], FakeProvider(''))
        self.assertEqual(result['classification'], 'NO_REPLY')
        result = generate(ROWS, CHATS, ROWS[-1], FakeProvider('thanks', True))
        self.assertEqual(result['classification'], 'APPROVAL_REQUIRED' if result['validation_status'] == 'rejected' else 'NEEDS_USER_INPUT')
        self.assertEqual(result['reply'], 'thanks')
        self.assertEqual(result['claim_category'], 'unknown_personal_facts')

    def test_contract_diagnostics_are_specific_and_content_free(self):
        cases = (
            ([], 'ollama_response_not_object'),
            ({'needs_personal_facts': False}, 'ollama_missing_reply'),
            ({'reply': None}, 'ollama_reply_not_text'),
            ({'reply': 'private text'}, 'ollama_missing_needs_personal_facts'),
            ({'reply': 'private text', 'needs_personal_facts': 'false'}, 'ollama_needs_personal_facts_not_boolean'),
            ({'reply': 'private text', 'needs_personal_facts': False, 'private key': 'secret'}, 'ollama_unexpected_fields'),
            ({'reply': 'thanks', 'classification': 'AUTO_SEND_ELIGIBLE|APPROVAL_REQUIRED|NO_REPLY'},
             'ollama_missing_needs_personal_facts'),
        )
        for payload, rule in cases:
            with self.subTest(rule=rule), self.assertRaisesRegex(DraftError, '^' + rule + '$'):
                reply_proposal(payload)
        self.assertEqual(reply_proposal({'reply': ' thanks ', 'needs_personal_facts': False}), ('thanks', False))

    def test_review_reports_specific_categories(self):
        for reply, status, category in (
            ('a' * 301, 'rejected', 'length'),
            ('password is private', 'rejected', 'credentials_or_code'),
            ('<think>reasoning</think>thanks', 'rejected', 'reasoning_or_instructions'),
            ('Checking emails, how about you?', 'needs_user_input', 'activity'),
        ):
            result = generate(ROWS, CHATS, ROWS[-1], FakeProvider(reply))
            self.assertEqual(result['claim_category'], category)
            self.assertEqual(result['validation_status'], status)
            self.assertEqual(result['classification'], 'NEEDS_USER_INPUT' if status == 'needs_user_input' else 'APPROVAL_REQUIRED')
            self.assertEqual(result['reply'], reply if status == 'needs_user_input' else '')

    def test_eval_rebuilds_profiles_for_each_prefix_and_compares_configs(self):
        rows = ROWS + [msg(6, 'no worries', True), msg(7, 'future-content', True)]
        providers = []
        def factory(config):
            provider = FakeProvider('no worries' if config.temperature == .3 else 'thanks')
            providers.append(provider)
            return provider
        report = evaluate(rows, CHATS, [ModelConfig(), ModelConfig(temperature=.7)],
                          cases=2, provider_factory=factory)
        self.assertEqual(report['best_config']['temperature'], .3)
        self.assertFalse(report['auto_sending'])
        self.assertNotIn('future-content', json.dumps([p.prompts for p in providers]))
        for provider in providers:
            latest = json.loads(provider.prompts[0][1]['content'])
            self.assertNotIn('hidden', json.dumps(latest))
        self.assertNotIn('no worries', json.dumps(report))
        self.assertEqual(compare('thanks', 'thanks'), dict(style=1, length=1, tone=1, relevance=1))
        self.assertEqual(compare('', 'thanks')['length'], 0)
        with self.assertRaises(DraftError):
            evaluate([], CHATS, [ModelConfig()], provider_factory=factory)

    def test_eval_hidden_reply_cannot_train_its_own_profile(self):
        provider = FakeProvider('thanks')
        rows = [msg(1, 'thank you'), msg(2, 'never include this heldout', True)]
        evaluate(rows, CHATS, [ModelConfig()], provider_factory=lambda c: provider)
        prompt = json.loads(provider.prompts[0][1]['content'])
        self.assertEqual(prompt['user_style']['sample_count'], 0)
        self.assertNotIn('general_examples', prompt)
        self.assertNotIn('heldout', json.dumps(prompt))
        self.assertNotIn('heldout', json.dumps(provider.claim_reviews))

    def test_eval_keeps_valid_cases_and_scores_failures_as_zero(self):
        provider = FakeProvider()
        responses = iter([{'reply': 'private text'}, {'reply': 'no worries', 'needs_personal_facts': False}])
        provider.chat = lambda messages: next(responses)
        report = evaluate(ROWS + [msg(6, 'no worries', True)], CHATS, [ModelConfig()],
                          cases=2, provider_factory=lambda config: provider)
        self.assertEqual(report['status'], 'partial')
        result = report['results'][0]
        self.assertEqual((result['cases'], result['completed'], result['failed'], result['withheld']), (2, 1, 1, 0))
        self.assertEqual(result['errors'], [{'case': 1, 'rule': 'ollama_missing_needs_personal_facts'}])
        self.assertEqual(result['metrics']['style'], .5)
        self.assertNotIn('private text', json.dumps(report))
        self.assertNotIn('g-5', json.dumps(report))

    def test_eval_all_invalid_has_no_winner_and_service_failures_remain_fatal(self):
        provider = FakeProvider()
        provider.result = {'needs_personal_facts': False}
        report = evaluate(ROWS, CHATS, [ModelConfig()], cases=1, provider_factory=lambda config: provider)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['results'][0]['failed'], 1)
        self.assertIsNone(report['best_config'])
        provider.chat = MagicMock(side_effect=DraftError('ollama_unavailable_start_loopback_server_and_install_model'))
        with self.assertRaisesRegex(DraftError, '^ollama_unavailable_'):
            evaluate(ROWS, CHATS, [ModelConfig()], provider_factory=lambda config: provider)
        self.assertEqual(provider.chat.call_count, 1)

    def test_history_bounds(self):
        reader = MagicMock()
        reader.read_before.side_effect = lambda before, limit: Batch('mock', 'one', tuple(
            m for m in ROWS if before is None or m.sequence < before
        )[-limit:])
        rows, counts = history(reader, CHATS, 5)
        self.assertEqual(rows, ROWS[:4])
        self.assertEqual(counts['messages_used_for_profiles'], 2)
        self.assertEqual(history(reader, CHATS, 1)[0], [ROWS[3]])
        for maximum in (0, True, 200001):
            with self.assertRaises(DraftError):
                history(reader, CHATS, maximum)

    def test_v2_migration_retains_data_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.db'
            connection = sqlite3.connect(path)
            connection.executescript(_SCHEMA + _OUTBOUND_SCHEMA)
            connection.execute("INSERT INTO cursors VALUES ('synthetic','one',42)")
            connection.commit()
            connection.close()
            path.chmod(0o600)
            for _ in range(2):
                store = Store(path)
                try:
                    self.assertEqual(store.cursor('synthetic').sequence, 42)
                    self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], 6)
                    self.assertEqual(store.connection.execute('SELECT count(*) FROM reply_drafts').fetchone()[0], 0)
                finally:
                    store.close()

    def test_private_storage_never_grants_outbound_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            store = Store(path)
            report = save_profiles(store, 'synthetic', 'one', ROWS, CHATS)
            self.assertEqual(report['general_samples'], 2)
            draft = generate(ROWS, CHATS, ROWS[-1], FakeProvider())
            draft_id = save_draft(store, 'synthetic', 'one', draft)
            row = store.connection.execute('SELECT * FROM reply_drafts WHERE id=?', (draft_id,)).fetchone()
            self.assertEqual(row['source_guid'], 'g-5')
            self.assertEqual(json.loads(row['draft_json'])['reply'], 'no worries')
            for table in ('approvals', 'contacts', 'schedules', 'send_attempts'):
                self.assertEqual(store.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
            store.close()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            store = Store(path)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM reply_drafts').fetchone()[0], 1)
            store.close()


class ProviderTests(unittest.TestCase):
    def test_default_model_and_configurable_alternatives(self):
        self.assertEqual(ModelConfig().model, 'qwen2.5:3b')
        self.assertEqual(ModelConfig.load(None).model, 'qwen2.5:3b')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"temperature":0.1}')
            self.assertEqual(ModelConfig.load(path).model, 'qwen2.5:3b')
            path.write_text('{"model":"qwen3:4b"}')
            self.assertEqual(ModelConfig.load(path).model, 'qwen3:4b')

    def test_config_rejects_cloud_and_remote_endpoints(self):
        for model in ('qwen3:cloud', 'qwen-cloud:4b', 'model with spaces'):
            with self.assertRaises(DraftError):
                ModelConfig(model=model)
        for endpoint in ('https://api.example.com', 'http://localhost:11434/api/chat',
                         'http://127.0.0.1:11434@evil/api/chat'):
            with self.assertRaises(DraftError):
                ModelConfig(endpoint=endpoint)
        for changes in ({'temperature': float('nan')}, {'num_predict': 10000}, {'num_predict': True}):
            with self.assertRaises(DraftError):
                ModelConfig(**changes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"model":"qwen3:8b", "temperature":0.1}')
            self.assertEqual(ModelConfig.load(path).model, 'qwen3:8b')
            path.write_text('{"cloud_fallback":true}')
            with self.assertRaises(DraftError):
                ModelConfig.load(path)

    def test_requests_nonstreaming_bounded_local_only(self):
        provider = Ollama(ModelConfig())
        with patch.object(provider, '_request', side_effect=[{'model_info': {'x': 'y'}},
                         {'done': True, 'done_reason': 'stop', 'message': {'content': '{"reply":"thanks","needs_personal_facts":false}'}}]) as request:
            self.assertEqual(provider.chat([{'role': 'user', 'content': 'synthetic'}]), {'reply': 'thanks', 'needs_personal_facts': False})
        self.assertEqual(request.call_args_list[0].args[0], '/api/show')
        payload = request.call_args_list[1].args[1]
        self.assertEqual(payload['model'], 'qwen2.5:3b')
        self.assertIs(payload['stream'], False)
        self.assertIs(payload['think'], False)
        self.assertEqual(payload['format'], REPLY_SCHEMA)
        self.assertEqual(set(payload['format']['required']), {'reply', 'needs_personal_facts'})
        self.assertNotIn('classification', payload['format']['properties'])
        self.assertEqual(payload['options']['num_predict'], 160)
        self.assertNotIn('tools', payload)

    def test_remote_or_absent_model_never_receives_history(self):
        for show in ({'remote_host': 'cloud', 'model_info': {'x': 'y'}}, {'remote_model': 'remote'}, {}):
            provider = Ollama(ModelConfig())
            with patch.object(provider, '_request', return_value=show) as request:
                with self.assertRaises(DraftError):
                    provider.chat([{'content': 'private'}])
            self.assertEqual(request.call_count, 1)

    def test_installed_model_catalog_is_get_only_and_validated(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"models":[{"name":"qwen2.5:3b"},{"name":"qwen2.5:7b"}]}'
        with patch('http.client.HTTPConnection', return_value=connection) as constructor:
            self.assertEqual(Ollama(ModelConfig()).installed_models(), {'qwen2.5:3b', 'qwen2.5:7b'})
        constructor.assert_called_once_with('127.0.0.1', 11434, timeout=60)
        connection.request.assert_called_once_with('GET', '/api/tags', None, {'Content-Type': 'application/json'})
        for catalog in ({}, {'models': None}, {'models': ['qwen2.5:7b']}, {'models': [{'name': None}]}):
            with patch.object(Ollama, '_request', return_value=catalog):
                with self.assertRaisesRegex(DraftError, '^ollama_invalid_model_catalog$'):
                    Ollama(ModelConfig()).installed_models()

    def test_connection_errors_and_redirects_are_content_free(self):
        for status in (302, 404, 500):
            response = MagicMock(status=status)
            response.read.return_value = b'private server error'
            connection = MagicMock()
            connection.getresponse.return_value = response
            with patch('http.client.HTTPConnection', return_value=connection) as constructor:
                with self.assertRaisesRegex(DraftError, '^ollama_request_failed_check_local_server_and_installed_model$'):
                    Ollama(ModelConfig()).health()
                constructor.assert_called_once_with('127.0.0.1', 11434, timeout=60)
                connection.close.assert_called_once()
                self.assertEqual(connection.request.call_count, 1)
        with patch('http.client.HTTPConnection', side_effect=OSError('private-error')):
            # Constructor is deliberately exercised by the CLI error boundary below.
            with self.assertRaises(OSError):
                Ollama(ModelConfig()).health()
        connection = MagicMock()
        connection.request.side_effect = OSError('private-error')
        with patch('http.client.HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(DraftError, '^ollama_unavailable_start_loopback_server_and_install_model$'):
                Ollama(ModelConfig()).health()

    def test_empty_completion_is_an_error_but_empty_draft_json_is_valid(self):
        provider = Ollama(ModelConfig())
        for content in ('', '   ', '\n\t'):
            response = {'done': True, 'done_reason': 'stop',
                        'message': {'content': content, 'thinking': 'private reasoning must not be used'}}
            with self.subTest(content=content), patch.object(provider, 'health'), patch.object(
                provider, '_request', return_value=response
            ):
                with self.assertRaisesRegex(DraftError, '^ollama_empty_response$'):
                    provider.chat([])
        for needs_facts in (False, True):
            draft = {'needs_personal_facts': needs_facts, 'reply': ''}
            response = {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(draft)}}
            with patch.object(provider, 'health'), patch.object(provider, '_request', return_value=response):
                self.assertEqual(provider.chat([]), draft)

    def test_malformed_and_truncated_response_rules_are_specific(self):
        cases = (
            ({'done': False}, 'ollama_incomplete_response'),
            ({'done': True, 'done_reason': 'length'}, 'ollama_truncated_response'),
            ({'done': True, 'done_reason': 'length', 'message': {'content': '{"reply":"thanks"}'}},
             'ollama_truncated_response'),
            ({'done': True, 'done_reason': 'unexpected'}, 'ollama_unexpected_completion_reason'),
            ({'done': True}, 'ollama_missing_message'),
            ({'done': True, 'message': {}}, 'ollama_missing_content'),
            ({'done': True, 'message': {'content': None}}, 'ollama_content_not_text'),
            ({'done': True, 'message': {'content': 'not json private content'}}, 'ollama_malformed_json'),
            ({'done': True, 'message': {'content': 'analysis before {"reply":"thanks"}'}}, 'ollama_malformed_json'),
            ({'done': True, 'message': {'content': '[]'}}, 'ollama_response_not_object'),
        )
        for response, rule in cases:
            provider = Ollama(ModelConfig())
            with self.subTest(rule=rule), patch.object(provider, 'health'), patch.object(provider, '_request', return_value=response):
                with self.assertRaisesRegex(DraftError, '^' + rule + '$'):
                    provider.chat([])


class DraftCLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'messages.db'
        self.state = Path(self.directory.name) / 'state.db'
        self.db = fixture(self.path)
        self.db.execute('CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)')
        self.db.execute('INSERT INTO chat_handle_join VALUES (1,1)')
        add(self.db, 1, text='thanks')
        add(self.db, 2, text='no worries', is_from_me=1)
        add(self.db, 3, text='hey')

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def invoke(self, command, *extra):
        output = io.StringIO()
        args = [command, '--database', str(self.path)]
        if command != 'evaluate-drafts':
            args += ['--state', str(self.state)]
        with contextlib.redirect_stdout(output), patch(
            'imessage_agent.ollama.Ollama.classify_claims', return_value='harmless'
        ):
            status = main(args + list(extra))
        return status, json.loads(output.getvalue()), output.getvalue()

    def test_profile_cli_offline_and_preview_content_opt_in(self):
        before = self.path.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch(
            'subprocess.Popen', side_effect=AssertionError('no subprocess')
        ):
            status, report, output = self.invoke('build-profiles')
        self.assertEqual(status, 0)
        self.assertEqual(report['general_samples'], 1)
        self.assertNotIn('no worries', output)
        with patch('imessage_agent.ollama.Ollama.chat', return_value=FakeProvider().result):
            status, report, output = self.invoke('draft-preview', '--source-sequence', '3')
            self.assertEqual(status, 0)
            self.assertNotIn('reply', report)
            self.assertNotIn('+12025550101', output)
            status, report, output = self.invoke('draft-preview', '--source-sequence', '3', '--show-text')
            self.assertEqual(report['reply'], 'no worries')
        self.assertEqual(self.path.read_bytes(), before)

    def test_membership_schema_and_groups_fail_closed(self):
        with MacOSMessagesReader(self.path) as reader:
            self.assertEqual(reader.individual_chats(), {'synthetic-chat': ALICE})
        self.db.execute('INSERT INTO handle VALUES (?)', (BOB.address,))
        self.db.execute('INSERT INTO chat_handle_join VALUES (1,2)')
        self.db.commit()
        with MacOSMessagesReader(self.path) as reader:
            self.assertEqual(reader.individual_chats(), {})
        self.db.execute('DROP TABLE chat_handle_join')
        self.db.commit()
        with MacOSMessagesReader(self.path) as reader:
            with self.assertRaises(MessagesAccessError):
                reader.individual_chats()

    def test_source_cannot_be_state_and_errors_do_not_leak(self):
        original = self.path.read_bytes()
        self.state = self.path
        status, report, output = self.invoke('build-profiles')
        self.assertEqual(status, 1)
        self.assertEqual(report['error'], 'state_is_source')
        self.assertEqual(self.path.read_bytes(), original)
        self.state = Path(self.directory.name) / 'other.db'
        with patch('imessage_agent.ollama.Ollama.chat', side_effect=OSError('raw-private-body')):
            status, report, output = self.invoke('draft-preview', '--source-sequence', '3')
        self.assertEqual(status, 1)
        self.assertNotIn('raw-private-body', output)

    def test_eval_all_invalid_returns_diagnostics_without_creating_state(self):
        with patch('imessage_agent.ollama.Ollama.chat', return_value={'reply': 'raw-private-body'}):
            status, report, output = self.invoke('evaluate-drafts', '--cases', '1')
        self.assertEqual(status, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['results'][0]['errors'], [{'case': 1, 'rule': 'ollama_missing_needs_personal_facts'}])
        self.assertNotIn('raw-private-body', output)
        self.assertFalse(self.state.exists())

    def test_eval_cli_outputs_only_aggregate_scores(self):
        with patch('imessage_agent.ollama.Ollama.chat', return_value=FakeProvider().result):
            status, report, output = self.invoke('evaluate-drafts', '--cases', '1')
        self.assertEqual(status, 0)
        self.assertEqual(report['results'][0]['cases'], 1)
        for value in ('no worries', 'thanks', ALICE.address, 'synthetic-chat'):
            self.assertNotIn(value, output)
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
