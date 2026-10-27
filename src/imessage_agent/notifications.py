"""Fixed, content-free events delivered by a bundled native notification app."""
import subprocess
from pathlib import Path

EVENTS = frozenset(('accepted', 'sent', 'delivered', 'failed', 'partial',
                    'unknown', 'missed', 'approval', 'attention'))


def application_directory():
    return Path.home() / 'Library/Application Support/iMessage Agent'


def notification_app():
    return application_directory() / 'Notifier.app'


def notify_native(event):
    if event not in EVENTS:
        raise ValueError('invalid_notification_event')
    open_notifier(event)


def open_notifier(event):
    app = notification_app()
    if not app.is_dir():
        raise OSError('notification_helper_not_installed')
    subprocess.run(['/usr/bin/open', '-W', '-n', str(app), '--args', event],
                   check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
