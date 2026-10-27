"""Background lifecycle around the existing scheduler; no AI or new send path."""
import fcntl
import hashlib
import json
import logging
import math
import os
import signal
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .scheduling import Scheduler
from .storage import Store


class WorkerBusy(Exception):
    pass


def lock_path(state):
    return Path(str(state) + '.worker.lock')


@contextmanager
def worker_lock(state):
    # Never unlink: another process could still hold the old inode.
    fd = os.open(lock_path(state), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('unsafe_worker_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerBusy() from None
        yield
    finally:
        os.close(fd)


def lock_held(state):
    if not lock_path(state).exists():
        return False
    try:
        with worker_lock(state):
            return False
    except WorkerBusy:
        return True


def preference(db, key, default):
    row = db.execute('SELECT value FROM dashboard_preferences WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def save_health(db, health):
    with db:
        db.execute('INSERT INTO dashboard_preferences VALUES (?,?) ON CONFLICT(key) '
                   'DO UPDATE SET value=excluded.value', ('worker:health', json.dumps(health)))


def worker_status(store, state, *, now=None):
    now = time.time() if now is None else now
    h = preference(store.connection, 'worker:health', {})
    heartbeat = h.get('heartbeat')
    poll = h.get('last_poll')
    fresh = (heartbeat is not None and 0 <= now - heartbeat <= 90)
    poll_fresh = poll is not None and 0 <= now - poll <= 90
    held = lock_held(state)
    status = 'running' if held and fresh and poll_fresh and not h.get('error') else (
        'attention' if held else 'stopped')
    pending, next_at = store.connection.execute(
        "SELECT COUNT(*),MIN(o.send_at) FROM schedule_occurrences o JOIN schedule_plans p ON p.id=o.plan_id "
        "WHERE o.status='pending' AND p.status='active'").fetchone()
    last = store.connection.execute(
        "SELECT id,status FROM schedule_occurrences WHERE claimed_at IS NOT NULL "
        'ORDER BY claimed_at DESC,id DESC LIMIT 1').fetchone()
    recent = list(h.get('recent_errors', []))
    for row in store.connection.execute(
        "SELECT status,COALESCE(claimed_at,send_at) AS timestamp FROM schedule_occurrences "
        "WHERE status IN ('failed','partial','unknown','missed') ORDER BY timestamp DESC LIMIT 10"):
        recent.append(dict(timestamp=row['timestamp'], category='scheduled_' + row['status']))
    recent = sorted(recent, key=lambda e: e['timestamp'], reverse=True)[:10]
    return dict(status=status, heartbeat=heartbeat, last_poll=poll, pending_count=pending,
                next_execution=next_at, recent_errors=recent,
                last_outcome=last['status'] if last else None, transport=h.get('transport'))


def private_logger(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private_log_directory_required')
    path = directory / 'worker.jsonl'
    # Check all files rotation may touch before opening any of them.
    for item in [path, *(Path(str(path) + '.' + str(n)) for n in range(1, 4))]:
        if item.exists() or item.is_symlink():
            meta = item.lstat()
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                    or meta.st_mode & 0o077 or meta.st_nlink != 1):
                raise ValueError('unsafe_log_file')
    logger = logging.Logger('imessage-worker')
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3)
    os.chmod(path, 0o600)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


def log_event(logger, timestamp, event, *, schedule_id=None, occurrence_id=None, outcome=None, error=None):
    # Callers pass fixed categories only, never exception strings or scheduler detail.
    logger.info(json.dumps(dict(timestamp=timestamp, event=event, schedule_id=schedule_id,
                                occurrence_id=occurrence_id, outcome=outcome, error=error)))


class Worker:
    def __init__(self, state, logger, *, live=False, interval=10, clock=time.time, notify=None):
        if not math.isfinite(interval) or not 1 <= interval <= 60:
            raise ValueError('invalid_worker_interval')
        self.state, self.logger, self.live = Path(state), logger, live
        self.interval, self.clock = interval, clock
        self.stop = threading.Event()
        self.notify = notify
        self.health = dict(heartbeat=None, last_poll=None, error=None, recent_errors=[],
                           transport='live' if live else 'mock')

    def event(self, event, **fields):
        log_event(self.logger, self.clock(), event, **fields)

    def attention(self, category):
        if self.health['error'] == category:
            return
        self.health['error'] = category
        self.health['recent_errors'] = ([dict(timestamp=self.clock(), category=category)]
                                        + self.health['recent_errors'])[:10]
        self.event('attention', error=category)

    def notifications(self, store):
        from .notifications import notify_native
        db = store.connection
        enabled = preference(db, 'settings', {}).get('notifications') is True
        # Persist observation before attempting presentation. Neither crashes nor delivery
        # failures trigger notification-driven retries or any message dispatch.
        rows = db.execute("SELECT o.id,o.plan_id,o.status FROM schedule_occurrences o "
                          "LEFT JOIN worker_notifications n ON n.event_key=o.id||':'||o.status "
                          "WHERE o.status NOT IN ('pending','cancelled') AND n.event_key IS NULL "
                          "ORDER BY o.send_at,o.id LIMIT 5").fetchall()
        events = [(r['id'] + ':' + r['status'], r['status'], r['plan_id'], r['id']) for r in rows]
        for row in db.execute('SELECT id,draft_json FROM reply_drafts'):
            value = json.loads(row['draft_json'])
            if value.get('dashboard_status') == 'pending':
                revision = hashlib.sha256(row['draft_json'].encode()).hexdigest()
                events.append(('approval:' + row['id'] + ':' + revision, 'approval', None, None))
        for error in self.health['recent_errors']:
            events.append(('attention:' + error['category'] + ':' + str(error['timestamp']), 'attention', None, None))
        presented = 0
        for key, outcome, plan, occurrence in events:
            if self.stop.is_set():
                break
            with db:
                inserted = db.execute('INSERT OR IGNORE INTO worker_notifications VALUES (?,?)',
                                      (key, self.clock())).rowcount
            if not inserted:
                continue
            self.event('schedule_outcome' if occurrence else outcome,
                       schedule_id=plan, occurrence_id=occurrence, outcome=outcome)
            if enabled:
                try:
                    (self.notify or notify_native)(outcome)
                except Exception:
                    self.attention('notification_unavailable')
                presented += 1
                if presented == 5:
                    break  # Bound presentation work so backlog banners cannot starve polling.

    def poll(self, store):
        self.health['heartbeat'] = self.clock()
        save_health(store.connection, self.health)
        Scheduler(store, clock=self.clock).tick(dry_run=False, live=self.live, should_stop=self.stop.is_set)
        self.health.update(last_poll=self.clock(), heartbeat=self.clock(), error=None)
        self.notifications(store)
        save_health(store.connection, self.health)

    def run(self):
        # The lock covers connection setup, polls, sleeps and shutdown.
        with worker_lock(self.state):
            self.event('started', outcome='live' if self.live else 'mock')
            try:
                while not self.stop.is_set():
                    store = None
                    try:
                        store = Store(self.state)
                        if self.health['heartbeat'] is None:
                            prior = preference(store.connection, 'worker:health', {}).get('recent_errors', [])
                            self.health['recent_errors'] = (self.health['recent_errors'] + prior)[:10]
                        self.poll(store)
                    except sqlite3.Error:
                        self.attention('sqlite_unavailable')
                    except (OSError, ValueError, KeyError, TypeError):
                        self.attention('local_state_or_policy_unavailable')
                    except Exception:
                        self.attention('poll_failed')
                    finally:
                        if store is not None:
                            try:
                                self.health['heartbeat'] = self.clock()
                                if self.health['error']:
                                    self.notifications(store)
                                save_health(store.connection, self.health)
                            except Exception:
                                pass  # State may be locked; heartbeat must age out.
                            store.close()
                    self.stop.wait(self.interval)
            finally:
                self.event('stopped')
        return 0


def run_worker(args):
    from .smoke import _state_path
    state = _state_path(Path('/nonexistent-worker-source'), args.state)
    if not state.is_file():
        raise ValueError('existing_state_required')
    # Private new SQLite companions and rotated logs inherit this mask.
    os.umask(0o077)
    logger = private_logger(args.logs)
    worker = Worker(state, logger, live=args.execute_live, interval=args.interval)
    previous = {sig: signal.signal(sig, lambda *_: worker.stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return worker.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        for handler in logger.handlers:
            handler.close()
