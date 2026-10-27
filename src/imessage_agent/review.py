"""Opt-in terminal review and private ratings, isolated from outbound state."""

import json
import os
import sqlite3
import stat
import statistics
import sys
import time
import unicodedata
import uuid
import math
from collections import Counter
from pathlib import Path

from .drafting import generate, history, prepare_context
from .evaluation import case_history, compare, select_cases
from .macos import MacOSMessagesReader
from .ollama import DraftError, ModelConfig, Ollama
from .smoke import _state_path


_REVIEW_SCHEMA = """
CREATE TABLE review_session (id INTEGER PRIMARY KEY CHECK(id=1), metadata_json TEXT NOT NULL);
CREATE TABLE review_cases (
    ordinal INTEGER PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    rating_json TEXT,
    rated_at REAL
);
PRAGMA user_version=1;
"""


class ReviewStore:
    """One private, explicitly chosen review file; never opens agent state."""

    def __init__(self, path, *, resume=False, readonly=False):
        self.connection = None
        if readonly and not resume:
            raise DraftError('readonly_review_requires_existing_file')
        path = Path(path)
        if resume and not path.exists():
            raise DraftError('review_file_not_found')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = (os.O_RDONLY if readonly else os.O_RDWR) | os.O_NOFOLLOW | (0 if resume else os.O_CREAT | os.O_EXCL)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            raise DraftError('review_file_exists_use_resume_or_new_path') from None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_uid != os.getuid() or info.st_nlink != 1):
                raise DraftError('unsafe_review_file')
        finally:
            os.close(descriptor)
        try:
            self.connection = (sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
                               if readonly else sqlite3.connect(path, timeout=5))
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA trusted_schema=OFF')
            if readonly:
                self.connection.execute('PRAGMA query_only=ON')
            if not resume:
                self.connection.executescript('BEGIN IMMEDIATE;' + _REVIEW_SCHEMA + 'COMMIT;')
            tables = self.connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if (self.connection.execute('PRAGMA user_version').fetchone()[0] != 1
                    or {tuple(row) for row in tables} != {('table', 'review_session'), ('table', 'review_cases')}):
                raise DraftError('unrecognized_review_file')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def put_session(self, metadata):
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO review_session VALUES (1,?)', (json.dumps(metadata),))

    def session(self):
        row = self.connection.execute('SELECT metadata_json FROM review_session WHERE id=1').fetchone()
        if row is None:
            raise DraftError('review_session_missing')
        return json.loads(row[0])

    def add_case(self, ordinal, snapshot):
        with self.connection:
            self.connection.execute('INSERT INTO review_cases VALUES (?,?,NULL,NULL)', (ordinal, json.dumps(snapshot)))

    def cases(self):
        return self.connection.execute('SELECT * FROM review_cases ORDER BY ordinal').fetchall()

    def rate(self, ordinal, rating):
        extra = {'session_boundary_correct', 'topic_switch_correct', 'unnecessary_reply'} if self.session().get('session_study') else set()
        if any(rating.get(key) is not None and type(rating.get(key)) is not bool for key in extra):
            raise DraftError('invalid_review_rating')
        if (set(rating) != {'style', 'relevance', 'naturalness', 'invents_personal_information'} | extra
                or any(type(rating[key]) is not int or not 1 <= rating[key] <= 5
                       for key in ('style', 'relevance', 'naturalness'))
                or type(rating['invents_personal_information']) is not bool):
            raise DraftError('invalid_review_rating')
        with self.connection:
            result = self.connection.execute(
                'UPDATE review_cases SET rating_json=?,rated_at=? WHERE ordinal=? AND rating_json IS NULL',
                (json.dumps(rating), time.time(), ordinal),
            )
            if result.rowcount != 1:
                raise DraftError('review_case_missing_or_already_rated')

    def summary(self):
        metadata = self.session()
        cases = self.cases()
        if metadata.get('kind') == 'blind_comparison':
            return blind_summary(metadata, cases)
        scores = review_scores(cases)
        status = ('complete' if not scores['human_review']['pending'] and metadata['preparation_complete']
                  and len(cases) == metadata['requested'] else 'partial')
        if scores['prepared'] == scores['generation_failures']:
            status = 'failed'
        return {'status': status, 'auto_sending': False, 'review_id': metadata['review_id'],
                'model_config': metadata['model_config'], 'requested': metadata['requested'], **scores}


def review_scores(cases):
    snapshots = [json.loads(row['snapshot_json']) for row in cases]
    rated = [(snapshot, json.loads(row['rating_json'])) for row, snapshot in zip(cases, snapshots)
             if row['rating_json'] is not None]
    unavailable = sum(snapshot['generation_error'] is not None for snapshot in snapshots)
    pending = len(cases) - unavailable - len(rated)
    return {
        'prepared': len(cases), 'generation_failures': unavailable,
        'human_review': {
            'rated': len(rated), 'pending': pending,
            'mean_style': statistics.mean(r['style'] for _, r in rated) if rated else None,
            'mean_relevance': statistics.mean(r['relevance'] for _, r in rated) if rated else None,
            'mean_naturalness': statistics.mean(r['naturalness'] for _, r in rated) if rated else None,
            'invention_yes': sum(r['invents_personal_information'] for _, r in rated),
            'invention_rate': statistics.mean(r['invents_personal_information'] for _, r in rated) if rated else None,
            'content_accepted_with_invention': sum(
                r['invents_personal_information'] and s['draft']['validation_status'] == 'accepted'
                for s, r in rated),
        },
        'heuristics': {
            key: statistics.mean(snapshot['heuristic_scores'][key] for snapshot in snapshots) if snapshots else None
            for key in ('style', 'length', 'tone', 'relevance')
        },
    }


def blind_summary(metadata, cases):
    scores = review_scores(cases)
    complete = (not scores['human_review']['pending'] and metadata['preparation_complete']
                and len(cases) == metadata['requested'])
    report = {'status': 'complete' if complete else 'partial', 'auto_sending': False,
              'review_id': metadata['review_id'], 'prepared': len(cases), 'requested': metadata['requested'],
              'generation_failures': scores['generation_failures'],
              'human_review': {'rated': scores['human_review']['rated'], 'pending': scores['human_review']['pending']},
              'variants_hidden': not complete}
    if complete and scores['prepared'] == scores['generation_failures']:
        report['status'] = 'failed'
    if not complete:
        return report
    variants = {}
    for label, description in metadata['variants'].items():
        subset = [row for row in cases if json.loads(row['snapshot_json'])['variant'] == label]
        snapshots = [json.loads(row['snapshot_json']) for row in subset]
        timings = sorted(s['latency_ms'] for s in snapshots if s['latency_ms'] is not None)
        variants[label] = {**description, **review_scores(subset),
                           'validation_counts': dict(Counter(s['draft']['validation_status'] if s['draft'] else 'failed'
                                                             for s in snapshots)),
                           'latency_ms': {
                               'samples': len(timings),
                               'mean': statistics.mean(timings) if timings else None,
                               'median': statistics.median(timings) if timings else None,
                               'p95': timings[max(0, math.ceil(len(timings) * .95) - 1)] if timings else None,
                           }}
        if metadata.get('session_study'):
            ratings = [json.loads(row['rating_json']) for row in subset if row['rating_json']]
            variants[label]['conversation_quality'] = {}
            for key in ('session_boundary_correct', 'topic_switch_correct', 'unnecessary_reply'):
                values = [r[key] for r in ratings if r.get(key) is not None]
                variants[label]['conversation_quality'][key] = {
                    'rated': len(values), 'rate': statistics.mean(values) if values else None}
    return {**report, 'cases_per_variant': metadata['cases_per_variant'], 'variants': variants,
            'skipped_variants': metadata['skipped_variants'],
            'latency_scope': 'wall time including retrieval, local health checks, generation, revisions and claim review; loading included',
            'quality_basis': 'human rubric and invention labels with coverage; no automated winner or acceptance-rate optimization'}


def _terminal_text(text):
    # Quoting controls and bidi overrides keeps untrusted text from posing as a rating prompt.
    text = ''.join('\\u%04x' % ord(c) if unicodedata.bidirectional(c) in
                   {'RLO', 'LRO', 'RLE', 'LRE', 'PDF', 'LRI', 'RLI', 'FSI', 'PDI'} else c for c in text)
    return json.dumps(text, ensure_ascii=False)


def _show_case(ordinal, snapshot, *, blind=False, session_study=False):
    print(f'\nCase {ordinal} (private text; no sending)')
    for row in snapshot['context']:
        speaker = 'You' if row['direction'] == 'outbound' else 'Contact'
        scope = 'Evidence' if row.get('review_only') else 'Historical' if row.get('historical') else 'Current'
        print(f'{row.get("timestamp", "")} {scope} {speaker}: {_terminal_text(row["body"])}')
    draft = snapshot['draft']
    if session_study:
        print('Predicted conversation state: ' + draft.get('conversation_state', 'UNCERTAIN'))
        print('Starts new session: ' + str(draft.get('starts_new_session', False)))
    print('Generated: ' + _terminal_text(draft['reply']))
    print('Actual held-out: ' + _terminal_text(snapshot['actual_reply']))
    if not blind:
        print(f"Classification: {draft['classification']}; content: {draft['validation_status']}; "
              f"category: {draft['claim_category']}; reason: {draft['reason']}")
        if draft.get('quality_flags'):
            print('Quality flags: ' + ', '.join(draft['quality_flags']))
    print('Rate the wording even if held for approval. Flag personal claims unsupported by preceding context, '
          'not merely whether they sound plausible.')


def _read_rating(*, session_study=False):
    while True:
        answer = input('Rate style/relevance/naturalness (1-5 each) and invented personal info (y/n), '
                       'e.g. 4 5 4 n; s=skip, q=quit: ').strip().lower()
        if answer in ('s', 'q'):
            return answer
        parts = answer.split()
        if (len(parts) == 4 and all(value in ('1', '2', '3', '4', '5') for value in parts[:3])
                and parts[3] in ('y', 'yes', 'n', 'no')):
            rating = {'style': int(parts[0]), 'relevance': int(parts[1]), 'naturalness': int(parts[2]),
                    'invents_personal_information': parts[3] in ('y', 'yes')}
            if session_study:
                for key in ('session_boundary_correct', 'topic_switch_correct', 'unnecessary_reply'):
                    while True:
                        answer = input(key.replace('_', ' ') + '? y/n/x (not applicable): ').strip().lower()
                        if answer in ('y', 'n', 'x'):
                            rating[key] = None if answer == 'x' else answer == 'y'
                            break
            return rating
        print('Enter three scores from 1 to 5 and y/n, or s/q. Nothing saved yet.')


def run_review(args):
    if not args.show_text:
        raise DraftError('review_requires_show_text_opt_in')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise DraftError('review_requires_interactive_terminal_no_redirection')
    path = _state_path(args.database, args.ratings)
    if path.exists() and not args.resume:
        raise DraftError('review_file_exists_use_resume_or_new_path')
    store = None
    try:
        if args.resume:
            store = ReviewStore(path, resume=True)
        else:
            config = ModelConfig.load(args.config)
            with MacOSMessagesReader(args.database) as reader:
                chats = reader.individual_chats()
                rows, sampling = history(reader, chats, args.max_messages)
                selected = select_cases(rows, chats, args.cases)
                if not selected:
                    raise DraftError('no_safe_historical_reply_pairs')
                store = ReviewStore(path)
                metadata = {'review_id': uuid.uuid4().hex, 'source_id': reader.source_id,
                            'generation': reader.generation, 'model_config': vars(config),
                            'requested': args.cases, 'selected': len(selected), 'sampling': sampling,
                            'preparation_complete': False, 'created_at': time.time()}
                store.put_session(metadata)
                provider = Ollama(config)
                for ordinal, (incoming, actual) in enumerate(selected, 1):
                    print(f'Preparing private draft {ordinal}/{len(selected)}; no sending.')
                    preceding = case_history(rows, incoming, reader)
                    _, _, context = prepare_context(preceding, chats, incoming)
                    error = None
                    try:
                        draft = generate(preceding, chats, incoming, provider)
                    except DraftError as failure:
                        error, draft = str(failure), None
                    snapshot = {'source_guid': incoming.guid, 'held_out_guid': actual.guid,
                                'source_sequence': incoming.sequence, 'actual_sequence': actual.sequence,
                                'context': context, 'draft': draft, 'actual_reply': actual.body,
                                'generation_error': error,
                                'heuristic_scores': compare(draft['reply'] if draft and draft['validation_status'] == 'accepted'
                                                            else '', actual.body)}
                    store.add_case(ordinal, snapshot)
                    if error in ('ollama_unavailable_start_loopback_server_and_install_model',
                                 'ollama_request_failed_check_local_server_and_installed_model',
                                 'installed_local_model_required_cloud_models_forbidden'):
                        break
                metadata['preparation_complete'] = ordinal == len(selected)
                store.put_session(metadata)
        # The source is closed and every candidate is saved before human ratings start.
        blind = store.session().get('kind') == 'blind_comparison'
        for row in store.cases():
            if row['rating_json'] is not None:
                continue
            snapshot = json.loads(row['snapshot_json'])
            if snapshot['generation_error']:
                print(f"Case {row['ordinal']}: unavailable; not rated." if blind else
                      f"Case {row['ordinal']}: unavailable ({snapshot['generation_error']}); not rated.")
                continue
            _show_case(row['ordinal'], snapshot, blind=blind, session_study=store.session().get('session_study', False))
            rating = _read_rating(session_study=store.session().get('session_study', False))
            if rating == 'q':
                break
            if rating != 's':
                store.rate(row['ordinal'], rating)
        return store.summary()
    except (EOFError, KeyboardInterrupt):
        try:
            report = store.summary() if store is not None else {'auto_sending': False}
        except DraftError:
            report = {'auto_sending': False}
        return {**report, 'status': 'interrupted'}
    finally:
        if store is not None:
            store.close()
