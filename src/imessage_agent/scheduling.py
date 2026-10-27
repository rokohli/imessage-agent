"""Trusted, Ollama-independent scheduling. No imports start a worker or transport."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
import uuid
from datetime import date, datetime, time as daytime, timedelta
from zoneinfo import ZoneInfo

from .account_selection import AccountSelectionError, validate_account_id
from .adapters import MockMessagesAdapter
from .applescript import AppleScriptMessagesAdapter, _literal
from .models import Recipient, SendOutcome, SendStatus, payload_hash
from .outbound import Outbound




class SchedulingError(ValueError):
    """Only fixed public codes, never text or addresses."""


def canonical(spec):
    return json.dumps(spec, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def authorization_hash(spec):
    return hashlib.sha256(canonical(spec).encode()).hexdigest()


def local_instant(day, clock, zone, fold):
    """Reject gaps; folds require the stored earlier/later policy, never inference."""
    naive = datetime.combine(day, daytime.fromisoformat(clock))
    if naive.tzinfo is not None:
        raise SchedulingError('local_time_must_not_have_offset')
    possibilities = sorted({naive.replace(tzinfo=zone, fold=f).timestamp() for f in (0, 1)
                            if datetime.fromtimestamp(naive.replace(tzinfo=zone, fold=f).timestamp(), zone)
                            .replace(tzinfo=None) == naive})
    if not possibilities:
        raise SchedulingError('nonexistent_local_time')
    if len(possibilities) > 1 and fold == 'reject':
        raise SchedulingError('ambiguous_local_time_choose_earlier_or_later')
    return possibilities[-1] if fold == 'later' else possibilities[0]


def make_spec(recipient, body, *, start_date, start_time, timezone_name,
              end_time=None, recurrence='once', weekdays=(), until=None,
              fold='reject', grace=300, transport='mock', account_id=None):
    payload_hash(recipient, body)
    if len(body) > 4000:
        raise SchedulingError('text_too_long')
    _literal(body)
    first = date.fromisoformat(start_date)
    last = date.fromisoformat(until) if until else None
    zone = ZoneInfo(timezone_name)
    if recurrence not in ('once', 'daily', 'weekly', 'weekdays'):
        raise SchedulingError('invalid_recurrence')
    days = sorted(set(weekdays))
    if any(type(d) is not int or not 0 <= d <= 6 for d in days):
        raise SchedulingError('invalid_weekdays')
    if (recurrence == 'weekdays') != bool(days):
        raise SchedulingError('weekdays_required_only_for_weekdays_recurrence')
    if fold not in ('reject', 'earlier', 'later'):
        raise SchedulingError('invalid_fold_policy')
    if type(grace) is not int or not 0 <= grace <= 86400:
        raise SchedulingError('invalid_grace_seconds')
    if last and (last < first or recurrence == 'once'):
        raise SchedulingError('invalid_recurrence_end')
    if transport not in ('mock', 'live') or (transport == 'mock' and account_id is not None):
        raise SchedulingError('invalid_schedule_transport')
    if transport == 'live':
        try:
            validate_account_id(account_id)
        except AccountSelectionError:
            raise SchedulingError('invalid_schedule_account') from None
    start = daytime.fromisoformat(start_time)
    end = daytime.fromisoformat(end_time) if end_time else start
    if start.tzinfo or end.tzinfo or end < start or (end_time is not None and end == start):
        raise SchedulingError('same_day_local_window_required')
    # Validate the requested first day even when weekdays would exclude it.
    a = local_instant(first, start.isoformat(), zone, fold)
    b = local_instant(first, end.isoformat(), zone, fold)
    if b < a:
        raise SchedulingError('invalid_window')
    return dict(recipient=recipient.address, body=body, start_date=first.isoformat(),
                start_time=start.isoformat(), end_time=end.isoformat(), window=end_time is not None,
                timezone=timezone_name, recurrence=recurrence, weekdays=days,
                until=last.isoformat() if last else None, fold=fold, grace=grace,
                transport=transport, account_id=account_id)


def validate_spec(spec):
    rebuilt = make_spec(Recipient.parse(spec['recipient']), spec['body'],
                        start_date=spec['start_date'], start_time=spec['start_time'],
                        timezone_name=spec['timezone'], end_time=spec['end_time'] if spec['window'] else None,
                        recurrence=spec['recurrence'], weekdays=spec['weekdays'], until=spec['until'],
                        fold=spec['fold'], grace=spec['grace'], transport=spec['transport'],
                        account_id=spec['account_id'])
    if rebuilt != spec:
        raise SchedulingError('invalid_schedule_spec')


def verified_plan(connection, plan_id):
    row = connection.execute('SELECT * FROM schedule_plans WHERE id=?', (plan_id,)).fetchone()
    if row is None:
        raise SchedulingError('schedule_not_found')
    spec = json.loads(row['spec'])
    validate_spec(spec)
    if authorization_hash(spec) != row['authorization_hash']:
        raise SchedulingError('schedule_authorization_mismatch')
    return row, spec


def eligible_day(spec, day):
    first = date.fromisoformat(spec['start_date'])
    if day < first or (spec['until'] and day > date.fromisoformat(spec['until'])):
        return False
    return (spec['recurrence'] == 'once' and day == first or spec['recurrence'] == 'daily' or
            spec['recurrence'] == 'weekly' and day.weekday() == first.weekday() or
            spec['recurrence'] == 'weekdays' and day.weekday() in spec['weekdays'])


def validate_occurrence(connection, occurrence_id, recipient, body, now):
    """Called inside the outbound transaction; no second approval UI is needed."""
    row = connection.execute('SELECT * FROM schedule_occurrences WHERE id=?', (occurrence_id,)).fetchone()
    if not row:
        raise SchedulingError('occurrence_not_found')
    plan, spec = verified_plan(connection, row['plan_id'])
    if (plan['status'] != 'active' or row['status'] != 'unknown'
            or row['claimed_at'] is None or not row['send_at'] <= now <= row['deadline']
            or Recipient.parse(spec['recipient']) != recipient or spec['body'] != body):
        raise SchedulingError('occurrence_not_authorized')
    day = date.fromisoformat(row['local_date'])
    zone = ZoneInfo(spec['timezone'])
    start = local_instant(day, spec['start_time'], zone, spec['fold'])
    end = local_instant(day, spec['end_time'], zone, spec['fold'])
    deadline = min(row['send_at'] + spec['grace'], end) if spec['window'] else row['send_at'] + spec['grace']
    if (not eligible_day(spec, day) or row['window_start'] != start or row['window_end'] != end
            or not start <= row['send_at'] <= end or row['deadline'] != deadline):
        raise SchedulingError('occurrence_window_mismatch')
    return row, spec


class Scheduler:
    """One Store connection per worker; callers own its lifecycle."""

    def __init__(self, store, *, clock=time.time, rng=None):
        self.store = store
        self.clock = clock
        self.rng = rng if rng is not None else random.SystemRandom()

    def create(self, spec, *, authorized=False, replaces=None, authorize_recipient=False):
        if type(authorize_recipient) is not bool:
            raise SchedulingError('explicit_recipient_authorization_required')
        if authorized is not True:
            raise SchedulingError('explicit_schedule_authorization_required')
        validate_spec(spec)
        now = self.clock()
        if not math.isfinite(now):
            raise SchedulingError('invalid_clock')
        first = date.fromisoformat(spec['start_date'])
        start = local_instant(first, spec['start_time'], ZoneInfo(spec['timezone']), spec['fold'])
        if start <= now:
            raise SchedulingError('schedule_must_start_in_future')
        recipient = Recipient.parse(spec['recipient'])
        contact = self.store.contact(recipient)
        if not authorize_recipient and (not contact or not contact.outbound_allowed):
            raise SchedulingError('recipient_not_allowlisted')
        plan_id = uuid.uuid4().hex
        db = self.store.connection
        with db:
            db.execute('BEGIN IMMEDIATE')
            if replaces:
                old, _ = verified_plan(db, replaces)
                if old['status'] != 'active':
                    raise SchedulingError('schedule_already_cancelled')
                self._cancel(replaces)
            if authorize_recipient is True:
                db.execute('INSERT INTO contacts VALUES (?,?,?,?,1) ON CONFLICT(recipient_key) '
                           'DO UPDATE SET outbound_allowed=1',
                           (recipient.key, recipient.kind, recipient.address, 'Scheduled recipient'))
            db.execute('INSERT INTO schedule_plans VALUES (?,?,?,?,?,?,?)',
                       (plan_id, canonical(spec), authorization_hash(spec), now,
                        'active', first.isoformat(), replaces))
            self._materialize_locked(plan_id)
        return plan_id

    def _cancel(self, plan_id):
        db = self.store.connection
        changed = db.execute("UPDATE schedule_plans SET status='cancelled' WHERE id=? AND status='active'",
                             (plan_id,)).rowcount
        db.execute("UPDATE schedule_occurrences SET status='cancelled' WHERE plan_id=? AND status='pending'",
                   (plan_id,))
        return bool(changed)

    def cancel(self, plan_id):
        with self.store.connection:
            self.store.connection.execute('BEGIN IMMEDIATE')
            return self._cancel(plan_id)

    def materialize(self, plan_id):
        with self.store.connection:
            self.store.connection.execute('BEGIN IMMEDIATE')
            self._materialize_locked(plan_id)

    def _materialize_locked(self, plan_id):
        db = self.store.connection
        plan, spec = verified_plan(db, plan_id)
        if plan['status'] != 'active':
            return
        zone = ZoneInfo(spec['timezone'])
        horizon = datetime.fromtimestamp(self.clock(), zone).date() + timedelta(days=7)
        first = date.fromisoformat(spec['start_date'])
        day = date.fromisoformat(plan['next_date'])
        last = date.fromisoformat(spec['until']) if spec['until'] else date.max
        if spec['recurrence'] == 'once':
            last = first
            horizon = max(horizon, first)
        # shortcut: backlogs advance at most 366 days per tick, run more ticks after long downtime.
        for _ in range(366):
            if day > min(horizon, last):
                break
            if eligible_day(spec, day):
                try:
                    a = local_instant(day, spec['start_time'], zone, spec['fold'])
                    b = local_instant(day, spec['end_time'], zone, spec['fold'])
                except SchedulingError:
                    # DST gaps/rejected folds are recorded as missed; never silently shift a send.
                    a = b = datetime.combine(day, daytime(), zone).timestamp()
                    status, detail = 'missed', 'dst_local_time_unavailable'
                else:
                    status, detail = 'pending', ''
                send_at = a if a == b else self.rng.uniform(a, b)
                deadline = min(send_at + spec['grace'], b) if spec['window'] else send_at + spec['grace']
                db.execute('INSERT OR IGNORE INTO schedule_occurrences '
                           '(id,plan_id,local_date,window_start,window_end,send_at,deadline,status,detail) '
                           'VALUES (?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, plan_id, day.isoformat(), a, b, send_at, deadline, status, detail))
            if day == date.max:
                break
            day += timedelta(days=1)
        db.execute('UPDATE schedule_plans SET next_date=? WHERE id=?', (day.isoformat(), plan_id))

    def plans(self):
        return [dict(row) for row in self.store.connection.execute(
            'SELECT id,status,authorized_at,next_date,replaces FROM schedule_plans ORDER BY authorized_at,id')]

    def details(self, plan_id):
        plan, spec = verified_plan(self.store.connection, plan_id)
        return dict(id=plan_id, status=plan['status'], spec=spec, replaces=plan['replaces'])

    def history(self, plan_id=None):
        return [dict(row) for row in self.store.connection.execute(
            'SELECT o.*,a.status AS transport_status FROM schedule_occurrences o '
            'LEFT JOIN send_attempts a ON a.id=o.id WHERE (? IS NULL OR o.plan_id=?) ORDER BY o.send_at,o.id',
            (plan_id, plan_id))]

    def tick(self, *, dry_run=True, live=False, limit=100, plan_id=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise SchedulingError('invalid_worker_limit')
        if plan_id is not None:
            verified_plan(self.store.connection, plan_id)
        if dry_run:
            # No materialization, claims, approvals, attempts or clock-triggered status writes.
            now = self.clock()
            return [dict(row) for row in self.store.connection.execute(
                "SELECT id,plan_id,send_at,deadline FROM schedule_occurrences WHERE status='pending' "
                'AND send_at<=? AND (? IS NULL OR plan_id=?) ORDER BY send_at,id LIMIT ?',
                (now, plan_id, plan_id, limit))]
        active_ids = []
        for plan in self.plans():
            if plan['status'] == 'active' and (plan_id is None or plan['id'] == plan_id):
                _, spec = verified_plan(self.store.connection, plan['id'])
                if spec['transport'] == ('live' if live else 'mock'):
                    self.materialize(plan['id'])
                    active_ids.append(plan['id'])
        if not active_ids:
            return []
        placeholders = ','.join('?' for _ in active_ids)
        db = self.store.connection
        results = []
        for _ in range(limit):
            now = self.clock()
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT o.* FROM schedule_occurrences o JOIN schedule_plans p ON p.id=o.plan_id "
                                 "WHERE o.status='pending' AND p.status='active' AND o.send_at<=? "
                                 f'AND p.id IN ({placeholders}) ORDER BY o.send_at,o.id LIMIT 1',
                                 (now, *active_ids)).fetchone()
                if row is None:
                    break
                _, spec = verified_plan(db, row['plan_id'])
                if spec['transport'] != ('live' if live else 'mock'):
                    raise SchedulingError('worker_transport_does_not_match_schedule')
                if now > row['deadline']:
                    db.execute("UPDATE schedule_occurrences SET status='missed',detail='grace_or_window_expired' WHERE id=?",
                               (row['id'],))
                    results.append(dict(id=row['id'], status='missed'))
                    continue
                db.execute("UPDATE schedule_occurrences SET status='unknown',detail='reserved_do_not_retry',claimed_at=? WHERE id=?",
                           (now, row['id']))
            # Crash after reservation means UNKNOWN forever, even if dispatch never started.
            recipient = Recipient.parse(spec['recipient'])
            adapter = (AppleScriptMessagesAdapter(enabled=True, account_id=spec['account_id'])
                       if live else MockMessagesAdapter(outcome=SendOutcome(SendStatus.ACCEPTED, 'mock_transport_accepted')))
            try:
                attempt = Outbound(self.store, adapter, clock=self.clock).submit(
                    row['id'], recipient, spec['body'], schedule_occurrence=row['id'])
            except SchedulingError:
                if self.store.attempt(row['id']) is not None:
                    raise
                status = 'missed' if self.clock() > row['deadline'] else 'failed'
                with db:
                    db.execute('UPDATE schedule_occurrences SET status=?,detail=? WHERE id=?',
                               (status, 'pre_dispatch_policy_denied', row['id']))
                results.append(dict(id=row['id'], status=status))
                continue
            except Exception:
                # The reservation is already durable. Never turn this into a retry.
                results.append(dict(id=row['id'], status='unknown'))
                continue
            with db:
                db.execute('UPDATE schedule_occurrences SET status=?,detail=? WHERE id=?',
                           (attempt.outcome.status.value, attempt.outcome.detail, row['id']))
            results.append(dict(id=row['id'], status=attempt.outcome.status.value))
        return results
