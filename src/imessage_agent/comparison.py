"""Prepare A/B/C on the exact saved cases, then save shuffled private blind trials."""

import json
import random
import time
import uuid
from dataclasses import replace

from .drafting import generate, history, prepare_context, safe_text, usable
from .evaluation import case_history, compare
from .macos import MacOSMessagesReader
from .models import Direction
from .ollama import DraftError, ModelConfig, Ollama
from .review import ReviewStore
from .smoke import _state_path


_SERVICE_ERRORS = {
    'ollama_unavailable_start_loopback_server_and_install_model',
    'ollama_request_failed_check_local_server_and_installed_model',
    'installed_local_model_required_cloud_models_forbidden',
}


def _saved_message(reader, sequence, guid):
    if type(sequence) is not int or sequence <= 0 or not isinstance(guid, str):
        raise DraftError('invalid_saved_case_reference')
    batch = reader.read_after(sequence - 1, 1)
    if not batch.messages or batch.messages[0].sequence != sequence or batch.messages[0].guid != guid:
        raise DraftError('saved_case_changed_or_missing')
    return batch.messages[0]


def _frozen_cases(reader, source, maximum):
    metadata, records = source.session(), source.cases()
    if metadata.get('kind') == 'blind_comparison' or len(records) != 20:
        raise DraftError('comparison_requires_twenty_saved_source_cases')
    if metadata['source_id'] != reader.source_id or metadata['generation'] != reader.generation:
        raise DraftError('comparison_source_identity_or_generation_changed')
    chats = reader.individual_chats()
    selected, seen = [], set()
    for row in records:
        snapshot = json.loads(row['snapshot_json'])
        try:
            incoming = _saved_message(reader, snapshot['source_sequence'], snapshot['source_guid'])
            actual = _saved_message(reader, snapshot['actual_sequence'], snapshot['held_out_guid'])
            saved_context = snapshot['context']
            expected_incoming = saved_context[-1]['body']
        except (KeyError, IndexError, TypeError):
            raise DraftError('invalid_saved_case_reference') from None
        if (incoming.guid in seen or incoming.direction != Direction.INBOUND
                or actual.direction != Direction.OUTBOUND or actual.sequence <= incoming.sequence
                or incoming.chat_id != actual.chat_id or not usable([incoming], chats)
                or not safe_text(incoming.body)
                or not 0 <= actual.occurred_at - incoming.occurred_at <= 86400):
            raise DraftError('saved_case_ineligible_use_clean_source_review')
        if incoming.body != expected_incoming or actual.body != snapshot['actual_reply']:
            raise DraftError('saved_case_changed_or_missing')
        # A saved reference may be a reaction notification; it never enters inference.
        seen.add(incoming.guid)
        selected.append((row['ordinal'], incoming, actual))
    # New messages since the original review cannot consume the old cases' sample budget.
    rows, sampling = history(reader, chats, maximum, before=max(m.sequence for _, m, _ in selected) + 1)
    frozen = []
    for ordinal, incoming, actual in selected:
        preceding = case_history(rows + [incoming], incoming, reader)
        _, _, context = prepare_context(preceding, chats, incoming)
        frozen.append({'source_case': ordinal, 'incoming': incoming, 'actual': actual,
                       'preceding': preceding, 'context': context})
    return metadata, chats, frozen, sampling


def run_comparison(args):
    source_path = _state_path(args.database, args.source_review)
    output = _state_path(args.database, args.comparison)
    output = _state_path(source_path, output)
    if output.exists():
        raise DraftError('comparison_file_exists_use_new_path')
    base = ModelConfig.load(args.config)
    if base.model != 'qwen2.5:3b':
        raise DraftError('comparison_requires_qwen25_3b_base_config')
    source = ReviewStore(source_path, resume=True, readonly=True)
    store = None
    try:
        with MacOSMessagesReader(args.database) as reader:
            source_metadata, chats, frozen, sampling = _frozen_cases(reader, source, args.max_messages)
        # No private data goes to a provider until model locality is checked.
        provider = Ollama(base)
        installed = provider.installed_models()
        if base.model not in installed:
            raise DraftError('comparison_base_model_not_installed')
        provider.health()
        variants = {
            'A': {'pipeline': 'baseline', 'config': vars(base)},
            'B': {'pipeline': 'improved', 'config': vars(base)},
        }
        skipped = []
        session_study = args.command == 'compare-sessions'
        if session_study:
            variants = {'A': {'pipeline': 'legacy_context', 'config': vars(base)},
                        'B': {'pipeline': 'improved', 'config': vars(base)}}
        if not session_study and 'qwen2.5:7b' in installed:
            larger = replace(base, model='qwen2.5:7b')
            Ollama(larger).health()
            variants['C'] = {'pipeline': 'improved', 'config': vars(larger)}
        elif not session_study:
            skipped.append({'variant': 'C', 'model': 'qwen2.5:7b', 'reason': 'not_installed_locally'})
        jobs = [(label, case['source_case']) for label in variants for case in frozen]
        random.SystemRandom().shuffle(jobs)
        ordinals = {job: index for index, job in enumerate(jobs, 1)}
        store = ReviewStore(output)
        metadata = {'kind': 'blind_comparison', 'review_id': uuid.uuid4().hex,
                    'source_review_id': source_metadata['review_id'],
                    'source_id': source_metadata['source_id'], 'generation': source_metadata['generation'],
                    'requested': len(jobs), 'cases_per_variant': 20, 'variants': variants,
                    'skipped_variants': skipped, 'sampling': sampling,
                    'preparation_complete': False, 'created_at': time.time(), 'session_study': session_study}
        store.put_session(metadata)
        prepared = 0
        for label, description in variants.items():
            provider = Ollama(ModelConfig(**description['config']))
            for case in frozen:
                incoming, actual = case['incoming'], case['actual']
                start = time.perf_counter()
                error, draft = None, None
                try:
                    draft = generate(case['preceding'], chats, incoming, provider, pipeline=description['pipeline'])
                except DraftError as failure:
                    error = str(failure)
                elapsed = (time.perf_counter() - start) * 1000
                snapshot = {
                    'variant': label, 'source_case': case['source_case'],
                    'source_guid': incoming.guid, 'held_out_guid': actual.guid,
                    'source_sequence': incoming.sequence, 'actual_sequence': actual.sequence,
                    'context': (sorted(draft.get('historical_context', []) + draft.get('context', []),
                                       key=lambda m: m.get('occurred_at', 0)) if session_study and draft else case['context']), 'draft': draft, 'actual_reply': actual.body,
                    'generation_error': error, 'latency_ms': elapsed,
                    'heuristic_scores': compare(draft['reply'] if draft and draft['validation_status'] == 'accepted'
                                                else '', actual.body),
                }
                store.add_case(ordinals[(label, case['source_case'])], snapshot)
                prepared += 1
                print(f'Prepared blind trial {prepared}/{len(jobs)}; no sending.')
                if error in _SERVICE_ERRORS:
                    raise DraftError(error)
        metadata['preparation_complete'] = True
        store.put_session(metadata)
        return {'status': 'prepared', 'auto_sending': False, 'review_id': metadata['review_id'],
                'cases_per_variant': 20, 'trials': prepared, 'variants_prepared': len(variants),
                'skipped_variants': skipped, 'variants_hidden': True}
    except (EOFError, KeyboardInterrupt):
        return {'status': 'interrupted', 'auto_sending': False,
                'prepared': len(store.cases()) if store is not None else 0, 'variants_hidden': True}
    finally:
        source.close()
        if store is not None:
            store.close()
