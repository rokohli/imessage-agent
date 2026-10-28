"""Fixed, content-free events delivered by a bundled native notification app."""
import subprocess
from pathlib import Path

EVENTS = frozenset(('accepted', 'sent', 'delivered', 'failed', 'partial',
                    'unknown', 'missed', 'approval', 'attention'))


def application_directory():
    return Path.home() / 'Library/Application Support/iMessage Agent'


def notification_app():
    return application_directory() / 'Notifier.app'


def notify_native(event, display_name=None):
    if event not in EVENTS:
        raise ValueError('invalid_notification_event')
    open_notifier(event, display_name)


def open_notifier(event, display_name=None):
    app = notification_app()
    if not app.is_dir():
        raise OSError('notification_helper_not_installed')
    args = ['/usr/bin/open', '-W', '-n', str(app), '--args', event]
    if display_name:
        args.append(display_name[:200])
    subprocess.run(args,
                   check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
