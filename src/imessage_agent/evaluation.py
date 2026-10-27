"""Chronological held-out evaluation. No sending, raw-text reports, or cloud judge."""

import re
import statistics
from collections import Counter
from dataclasses import asdict

from .drafting import generate, safe_text, usable
from .models import Direction
from .ollama import DraftError, Ollama


def _style(body):
    return (body == body.lower(), body.rstrip().endswith(('.', '!', '?')),
            any(ord(c) > 0x1F000 or c in '❤♥' for c in body))


def _tone(body):
    return (bool(re.search(r'\b(thanks|thank|ty|thx)\b', body, re.I)),
            bool(re.search(r'\b(lol|haha|lmao)\b|😂', body, re.I)),
            '?' in body, '!' in body)


def compare(generated, actual):
    """Transparent proxies, not a semantic or human judgment of correctness."""
    if not generated:
        return dict(style=0.0, length=0.0, tone=0.0, relevance=0.0)
    left, right = set(re.findall(r'\w+', generated.lower())), set(re.findall(r'\w+', actual.lower()))
    return {
        'style': sum(a == b for a, b in zip(_style(generated), _style(actual))) / 3,
        'length': min(len(generated.split()), len(actual.split())) / max(1, len(generated.split()), len(actual.split())),
        'tone': sum(a == b for a, b in zip(_tone(generated), _tone(actual))) / 4,
        'relevance': 2 * len(left & right) / max(1, len(left) + len(right)),
    }


def select_cases(rows, chats, limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DraftError('evaluation_cases_must_be_1_to_100')
    candidates = []
    previous = {}
    for message in rows:
        if message.chat_id not in chats:
            continue
        if not usable([message], chats):
            previous.pop(message.chat_id, None)
            continue
        incoming = previous.get(message.chat_id)
        if (message.direction == Direction.OUTBOUND and incoming and incoming.direction == Direction.INBOUND
                and safe_text(incoming.body) and safe_text(message.body)
                and 0 <= message.occurred_at - incoming.occurred_at <= 86400):
            candidates.append((incoming, message))
        previous[message.chat_id] = message
    # Deterministic round-robin across chats, newest held-out pairs first.
    selected = []
    while candidates and len(selected) < limit:
        seen = set()
        remaining = []
        for pair in reversed(candidates):
            if pair[0].chat_id not in seen and len(selected) < limit:
                selected.append(pair)
                seen.add(pair[0].chat_id)
            else:
                remaining.append(pair)
        candidates = list(reversed(remaining))
    return selected


def case_history(rows, incoming, reader=None):
    """Hydrate exact recent chat context omitted by the bounded style sample."""
    preceding = {m.sequence: m for m in rows if m.sequence <= incoming.sequence}
    if reader is not None:
        batch = reader.read_before(incoming.sequence, 1000, chat_id=incoming.chat_id)
        if batch.source_id != reader.source_id or batch.generation != reader.generation:
            raise DraftError('case_context_source_changed')
        for message in batch.messages:
            if message.sequence >= incoming.sequence or message.chat_id != incoming.chat_id:
                raise DraftError('case_context_boundary_mismatch')
            preceding[message.sequence] = message
    return sorted(preceding.values(), key=lambda message: message.sequence)


def evaluate(rows, chats, configs, *, cases=10, provider_factory=Ollama, reader=None):
    selected = select_cases(rows, chats, cases)
    if not selected:
        raise DraftError('no_safe_historical_reply_pairs')
    reports = []
    for config in configs:
        provider = provider_factory(config)
        scores, blocked = [], 0
        errors, reasons = [], Counter()
        outcomes = Counter(accepted=0, approval_required=0, needs_user_input=0, rejected=0, no_reply=0)
        categories, validations, quality = Counter(), [], Counter()
        for case_number, (incoming, actual) in enumerate(selected, 1):
            # Even profile construction and retrieval see only the preceding prefix.
            try:
                preceding = case_history(rows, incoming, reader)
                draft = generate(preceding, chats, incoming, provider)
            except DraftError as error:
                # Service/locality failures affect every case; never switch providers.
                if str(error) in (
                    'ollama_unavailable_start_loopback_server_and_install_model',
                    'ollama_request_failed_check_local_server_and_installed_model',
                    'installed_local_model_required_cloud_models_forbidden',
                ):
                    raise
                errors.append({'case': case_number, 'rule': str(error)})
                outcomes['rejected'] += 1
                categories['invalid_response'] += 1
                validations.append({'case': case_number, 'status': 'rejected', 'category': 'invalid_response'})
                scores.append(compare('', actual.body))
                continue
            status = draft['validation_status']
            outcomes[status] += 1
            categories[draft['claim_category']] += 1
            quality.update(draft.get('quality_flags', []))
            validations.append({'case': case_number, 'status': status, 'category': draft['claim_category'],
                                'quality_flags': draft.get('quality_flags', [])})
            # Unverified claims must not earn relevance credit as factual answers.
            scores.append(compare(draft['reply'] if status == 'accepted' else '', actual.body))
            blocked += not bool(draft['reply'])
            reasons[draft['reason']] += 1
        means = {key: round(statistics.mean(score[key] for score in scores), 4) for key in scores[0]}
        reports.append({'config': asdict(config), 'cases': len(selected), 'withheld': blocked,
                        'completed': len(selected) - len(errors), 'failed': len(errors),
                        'errors': errors, 'draft_reasons': dict(reasons),
                        'validation_counts': dict(outcomes), 'content_categories': dict(categories),
                        'case_validations': validations, 'quality_flags': dict(quality),
                        'metrics': means, 'mean_score': round(statistics.mean(means.values()), 4)})
    winner = max(reports, key=lambda report: report['mean_score'])
    tied = sum(report['mean_score'] == winner['mean_score'] for report in reports) > 1
    completed = sum(report['completed'] for report in reports)
    failed = sum(report['failed'] for report in reports)
    status = 'complete' if not failed else ('partial' if completed else 'failed')
    return {'status': status, 'evaluation': 'chronological_held_out', 'auto_sending': False,
            'conversations': len({pair[0].chat_id for pair in selected}), 'results': reports,
            'best_config': winner['config'] if winner['mean_score'] > 0 and not tied else None,
            'conclusion': 'tie_or_no_usable_output' if tied or winner['mean_score'] == 0 else 'highest_heuristic_score',
            'limitations': 'Style/casing/punctuation/emoji, word-count ratio, tone markers and lexical F1 against '
                           'the actual reply are proxies. Relevance requires human review; alternate valid replies '
                           'may score poorly. Small samples do not establish a general model winner.'}
