"""Explicit-path Phase 3A commands; preview content is opt-in."""

import json
import sqlite3
import time
from pathlib import Path

from .drafting import generate, history, save_draft, save_profiles
from .evaluation import evaluate, case_history
from .sessions import config_from_store
from .contacts import Contacts
from .reply_lifecycle import fresh_reason
from .macos import MacOSMessagesReader, MessagesAccessError
from .ollama import DraftError, ModelConfig, Ollama
from .storage import Store
from .smoke import SmokeError, _state_path
from .review import run_review
from .comparison import run_comparison


COMMANDS = ('model-health', 'build-profiles', 'draft-preview', 'evaluate-drafts', 'review-drafts', 'compare-drafts', 'compare-sessions', 'compare-styles')


def add_commands(commands):
    for name in COMMANDS:
        parser = commands.add_parser(name, help='Local-only drafting; never sends messages')
        if name != 'build-profiles':
            parser.add_argument('--config', type=Path)
        if name != 'model-health':
            parser.add_argument('--database', type=Path, required=True)
            parser.add_argument('--max-messages', type=int, default=20000,
                                help='Maximum selected history rows, including preceding context (1–200000)')
        if name in ('build-profiles', 'draft-preview'):
            parser.add_argument('--state', type=Path, required=True)
        if name in ('draft-preview', 'review-drafts', 'evaluate-drafts'):
            parser.add_argument('--style-mode', choices=('single_pass', 'two_pass'), default='single_pass')
        if name == 'draft-preview':
            parser.add_argument('--source-sequence', type=int, required=True)
            parser.add_argument('--show-text', action='store_true', help='Display private draft text in this terminal')
        if name in ('evaluate-drafts', 'review-drafts'):
            parser.add_argument('--cases', type=int, default=20 if name == 'review-drafts' else 10)
        if name == 'review-drafts':
            parser.add_argument('--ratings', type=Path, required=True, help='Private plaintext snapshots and ratings, mode 0600, outside the repository')
            parser.add_argument('--show-text', action='store_true', help='Opt in to displaying private context, drafts and held-out replies')
            parser.add_argument('--resume', action='store_true', help='Rate saved snapshots without reading Messages or calling a model')
        if name in ('compare-drafts', 'compare-sessions', 'compare-styles'):
            parser.add_argument('--source-review', type=Path, required=True, help='Existing private review containing the exact 20 cases')
            parser.add_argument('--comparison', type=Path, required=True, help='New private blind-review SQLite file')
        if name == 'evaluate-drafts':
            parser.add_argument('--compare-config', type=Path, action='append', default=[])


def run_command(args):
    store = None
    try:
        if args.command in ('compare-drafts', 'compare-sessions', 'compare-styles'):
            report = run_comparison(args)
        elif args.command == 'review-drafts':
            report = run_review(args)
        elif args.command == 'model-health':
            report = Ollama(ModelConfig.load(args.config)).health()
        else:
            with MacOSMessagesReader(args.database) as reader:
                chats = reader.individual_chats()
                target = None
                if args.command == 'draft-preview':
                    if args.source_sequence <= 0:
                        raise DraftError('source_sequence_not_found')
                    batch = reader.read_after(args.source_sequence - 1, 1)
                    if not batch.messages or batch.messages[0].sequence != args.source_sequence:
                        raise DraftError('source_sequence_not_found')
                    target = batch.messages[0]
                rows, sampling = history(reader, chats, args.max_messages,
                                         before=target.sequence if target else None,
                                         context_chat=target.chat_id if target else None)
                if args.command == 'evaluate-drafts':
                    configs = [ModelConfig.load(args.config)] + [ModelConfig.load(p) for p in args.compare_config]
                    report = evaluate(rows, chats, configs, cases=args.cases, reader=reader,
                                      pipeline='style_revision' if args.style_mode == 'two_pass' else 'fingerprint')
                else:
                    state = _state_path(args.database, args.state)
                    store = Store(state)
                    if args.command == 'build-profiles':
                        report = save_profiles(store, reader.source_id, reader.generation, rows, chats)
                    else:
                        rows = case_history(rows, target, reader)
                        config = config_from_store(store)
                        reason = fresh_reason(reader, target, config=config)
                        from .style_edits import entries
                        draft = generate(rows, chats, target, Ollama(ModelConfig.load(args.config)), session_config=config,
                                         now=time.time(), veto=reason,
                                         pipeline='style_revision' if args.style_mode == 'two_pass' else 'fingerprint',
                                         accepted_edits=entries(store.connection, source=reader.source_id,
                                             generation=reader.generation, before=target.occurred_at))
                        reason = reason or fresh_reason(reader, target, config=config)
                        if reason:
                            draft.update(reply='', classification='NO_REPLY', reason=reason, validation_status='no_reply', auto_sending=False)
                        draft_id = save_draft(store, reader.source_id, reader.generation, draft)
                        report = {k: draft[k] for k in ('classification', 'reason', 'auto_sending', 'validation_status', 'claim_category', 'quality_flags', 'generation_attempts')}
                        report['draft_id'] = draft_id
                        if args.show_text:
                            report['reply'] = draft['reply']
                            report['display_name'] = Contacts(store).resolve(chats[target.chat_id].address)['display_name']
                report.update(sampling)
        print(json.dumps(report, sort_keys=True, ensure_ascii=True))
        return 130 if report.get('status') == 'interrupted' else int(report.get('status') == 'failed')
    except (DraftError, MessagesAccessError, SmokeError) as error:
        print(json.dumps({'error': str(error), 'auto_sending': False}))
        return 1
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({'error': 'local_drafting_state_or_input_unavailable', 'auto_sending': False}))
        return 1
    finally:
        if store is not None:
            store.close()
