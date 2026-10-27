"""Trusted terminal authorization; worker execution never invokes a model."""

import getpass
import json
import sqlite3
import sys
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

from .account_selection import AccountSelectionError, load_account_id
from .applescript import diagnose_applescript
from .models import Recipient
from .scheduling import Scheduler, SchedulingError, make_spec
from .smoke import SmokeError, _state_path
from .storage import Store


COMMANDS = ('schedule-create', 'schedule-edit', 'schedule-list', 'schedule-show',
            'schedule-cancel', 'schedule-history', 'scheduler-run')


def add_commands(commands):
    for name in COMMANDS:
        p = commands.add_parser(name, help='Explicitly authorized scheduling; mock by default')
        p.add_argument('--state', type=Path, required=True, help='Private agent SQLite state outside the checkout')
        if name in ('schedule-edit', 'schedule-show', 'schedule-cancel'):
            p.add_argument('--id', required=True)
        if name == 'schedule-history':
            p.add_argument('--id')
        if name == 'schedule-show':
            p.add_argument('--show-text', action='store_true')
        if name in ('schedule-create', 'schedule-edit'):
            p.add_argument('--date', required=True, help='First local date, YYYY-MM-DD')
            p.add_argument('--time', required=True, help='Local time, HH:MM[:SS]')
            p.add_argument('--end-time', help='Random window end on the same local day')
            p.add_argument('--timezone', required=True, help='IANA zone, e.g. America/Chicago')
            p.add_argument('--repeat', choices=('once', 'daily', 'weekly', 'weekdays'), default='once')
            p.add_argument('--weekdays', default='', help='Monday=0 through Sunday=6, comma-separated')
            p.add_argument('--until', help='Inclusive final local recurrence date')
            p.add_argument('--fold', choices=('reject', 'earlier', 'later'), default='reject')
            p.add_argument('--grace', type=int, default=300, help='Seconds late allowed; never past a random window end')
            p.add_argument('--transport', choices=('mock', 'live'), default='mock')
        if name == 'scheduler-run':
            p.add_argument('--id', help='Process only this authorized schedule')
            mode = p.add_mutually_exclusive_group()
            mode.add_argument('--dry-run', action='store_true', help='Default: read-only, no claims or dispatch')
            mode.add_argument('--execute-mock', action='store_true', help='Persist outcomes using synthetic transport only')
            mode.add_argument('--execute-live', action='store_true', help='LIVE: execute previously authorized live schedules')
            p.add_argument('--watch', action='store_true', help='Poll until interrupted; otherwise one tick')
            p.add_argument('--interval', type=int, default=10, help='Polling interval in seconds, 1–60')
            p.add_argument('--limit', type=int, default=100)


def _authorize(args):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SchedulingError('interactive_terminal_required')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        recipient = Recipient.parse(getpass.getpass('Exact individual recipient (hidden): '))
        body = getpass.getpass('Exact authorized text (hidden): ')
    account = load_account_id() if args.transport == 'live' else None
    spec = make_spec(recipient, body, start_date=args.date, start_time=args.time,
                     end_time=args.end_time, timezone_name=args.timezone,
                     recurrence=args.repeat, weekdays=tuple(int(d) for d in args.weekdays.split(',') if d),
                     until=args.until, fold=args.fold, grace=args.grace,
                     transport=args.transport, account_id=account)
    if args.transport == 'live':
        report = diagnose_applescript(recipient, account_id=account)
        if ('error' in report or any(report.get(key) != 1 for key in
                                    ('accounts', 'imessage_accounts', 'recipient_matches'))):
            raise SchedulingError('account_or_recipient_not_verified')
    # JSON escaping prevents pasted terminal controls. Never echo content outside the terminal.
    print(json.dumps({'authorize_exact_schedule': spec, 'replaces': getattr(args, 'id', None)}, ensure_ascii=True))
    print('This authorizes the exact recipient/text, account, recurrence, DST policy and grace above. '
          'Random time is selected once per occurrence. No send-time confirmation or retries. '
          'Exact-time sends may run up to grace seconds late; random windows close at end-time. '
          'Editing cancels pending old occurrences; already reserved attempts may still finish.')
    phrase = 'AUTHORIZE LIVE SCHEDULE' if args.transport == 'live' else 'AUTHORIZE MOCK SCHEDULE'
    if input(f'Type {phrase}: ') != phrase:
        raise SchedulingError('schedule_authorization_cancelled')
    return recipient, spec


def run_command(args):
    store = None
    try:
        # Reuse the outside-repository, private-file, symlink/hardlink checks.
        state = _state_path(Path('/nonexistent-scheduler-source'), args.state)
        if args.command in ('schedule-create', 'schedule-edit'):
            recipient, spec = _authorize(args)
            store = Store(state)
            # Exact terminal authorization also grants recipient opt-in, never model-derived.
            plan_id = Scheduler(store).create(spec, authorized=True,
                                              replaces=args.id if args.command == 'schedule-edit' else None,
                                              authorize_recipient=True)
            report = {'schedule_id': plan_id, 'transport': spec['transport']}
        else:
            if not state.exists():
                raise SchedulingError('existing_schedule_state_required')
            if args.command == 'scheduler-run' and (not 1 <= args.interval <= 60 or not 1 <= args.limit <= 1000):
                raise SchedulingError('invalid_worker_limits')
            dry = args.command == 'scheduler-run' and not (args.execute_mock or args.execute_live)
            if dry:
                connection = sqlite3.connect(state.as_uri() + '?mode=ro', uri=True, timeout=5)
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA query_only=ON')
                connection.execute('PRAGMA trusted_schema=OFF')
                store = SimpleNamespace(connection=connection, close=connection.close)
                if connection.execute('PRAGMA user_version').fetchone()[0] not in (4, 5):
                    raise SchedulingError('scheduler_schema_v4_or_v5_required')
            else:
                store = Store(state)
            scheduler = Scheduler(store)
            if args.command == 'schedule-list':
                report = {'schedules': scheduler.plans()}
            elif args.command == 'schedule-show':
                report = scheduler.details(args.id)
                if args.show_text:
                    if not (sys.stdin.isatty() and sys.stdout.isatty()):
                        raise SchedulingError('interactive_terminal_required')
                else:
                    report['spec'] = {k: v for k, v in report['spec'].items()
                                      if k not in ('recipient', 'body', 'account_id')}
            elif args.command == 'schedule-cancel':
                report = {'cancelled': scheduler.cancel(args.id)}
            elif args.command == 'schedule-history':
                report = {'occurrences': scheduler.history(args.id)}
            else:
                while True:
                    report = {'dry_run': dry, 'results': scheduler.tick(dry_run=dry, live=args.execute_live,
                                                                       limit=args.limit, plan_id=args.id)}
                    if not args.watch:
                        break
                    print(json.dumps(report, ensure_ascii=True), flush=True)
                    time.sleep(args.interval)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except (SchedulingError, AccountSelectionError, SmokeError) as error:
        print(json.dumps({'error': str(error)}))
    except (EOFError, KeyboardInterrupt):
        print(json.dumps({'error': 'scheduler_interrupted_no_retry'}))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, getpass.GetPassWarning):
        print(json.dumps({'error': 'schedule_input_or_state_unavailable'}))
    finally:
        if store is not None:
            store.close()
    return 1
