"""Explicit per-user launchd management. Importing this module changes nothing."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .notifications import application_directory, notification_app, open_notifier
from .smoke import _state_path
from .worker import private_logger

LABEL = 'io.github.rokohli.imessage-agent'


def plist_path():
    return Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def configuration(state, *, live=False, interval=10):
    if not 1 <= interval <= 60:
        raise ValueError('invalid_worker_interval')
    root = Path(__file__).resolve().parents[2]
    # Do not resolve a venv's interpreter symlink: that loses its site-packages.
    python = str(Path(sys.executable).absolute())
    args = [python, '-m', 'imessage_agent', 'background-worker', '--state', str(Path(state).resolve()),
            '--logs', str(application_directory() / 'logs'), '--interval', str(interval)]
    if live:
        args.append('--execute-live')
    return dict(Label=LABEL, ProgramArguments=args, WorkingDirectory=str(root),
                EnvironmentVariables={'PYTHONPATH': str(root / 'src'), 'PYTHONUNBUFFERED': '1'},
                RunAtLoad=True, KeepAlive={'SuccessfulExit': False}, ThrottleInterval=15,
                ProcessType='Background', LimitLoadToSessionType='Aqua', ExitTimeOut=30,
                Umask=0o077, StandardOutPath='/dev/null', StandardErrorPath='/dev/null')


def launchctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], check=check, timeout=40,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_notifier():
    target = notification_app()
    if target.exists():
        raise ValueError('notification_app_already_installed')
    # Compile before publishing any LaunchAgent configuration.
    with tempfile.TemporaryDirectory(dir=application_directory()) as temporary:
        app = Path(temporary) / 'Notifier.app'
        binary = app / 'Contents/MacOS/Notifier'
        binary.parent.mkdir(parents=True)
        subprocess.run(['/usr/bin/xcrun', 'swiftc', str(Path(__file__).with_name('Notifier.swift')),
                        '-o', str(binary), '-framework', 'AppKit', '-framework', 'UserNotifications'],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier=LABEL + '.notifier', CFBundleExecutable='Notifier',
            CFBundleName='iMessage Agent', CFBundlePackageType='APPL', CFBundleVersion='1',
            LSUIElement=True, NSHighResolutionCapable=True)))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)],
                       check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        app.rename(target)


def manage(args):
    if sys.platform != 'darwin':
        raise ValueError('macos_required')
    os.umask(0o077)
    path = plist_path()
    domain = 'gui/' + str(os.getuid())
    service = domain + '/' + LABEL
    if args.action == 'install':
        state = _state_path(Path('/nonexistent-launchagent-source'), args.state)
        if not state.is_file():
            raise ValueError('existing_state_required')
        if path.exists() or path.is_symlink():
            raise ValueError('launchagent_already_installed')
        payload = plistlib.dumps(configuration(state, live=args.execute_live, interval=args.interval))
        logger = private_logger(application_directory() / 'logs')
        for handler in logger.handlers:
            handler.close()
        build_notifier()
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open('xb') as handle:
                created = True
                handle.write(payload)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            shutil.rmtree(notification_app())
            raise
        return dict(installed=True, started=False, transport='live' if args.execute_live else 'mock')
    if args.action == 'status':
        return dict(installed=path.is_file(), loaded=launchctl('print', service, check=False).returncode == 0)
    if args.action == 'notifications-authorize':
        open_notifier('authorize')
        return dict(notification_permission='requested_check_system_settings')
    if args.action == 'notifications-test':
        open_notifier('attention')
        return dict(notification='requested_verify_delivery_visually')
    if args.action == 'start':
        if not path.is_file():
            raise ValueError('install_launchagent_first')
        if launchctl('print', service, check=False).returncode != 0:
            launchctl('bootstrap', domain, str(path))
        launchctl('kickstart', service)
    elif args.action in ('stop', 'restart', 'uninstall'):
        if launchctl('print', service, check=False).returncode == 0:
            launchctl('bootout', service)
        if args.action == 'restart':
            if not path.is_file():
                raise ValueError('install_launchagent_first')
            launchctl('bootstrap', domain, str(path))
        elif args.action == 'uninstall':
            path.unlink(missing_ok=True)
            if notification_app().exists():
                shutil.rmtree(notification_app())
            # State, approvals, attempts and bounded logs deliberately survive uninstall.
    return dict(ok=True, action=args.action)


def add_commands(commands):
    p = commands.add_parser('background-worker', help='Run existing scheduler until SIGTERM; mock by default')
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--logs', type=Path, default=application_directory() / 'logs')
    p.add_argument('--interval', type=int, default=10)
    p.add_argument('--execute-live', action='store_true', help='Execute previously authorized live schedules')
    p = commands.add_parser('launchagent', help='Explicit per-user LaunchAgent management; no sudo')
    p.add_argument('action', choices=('install', 'start', 'stop', 'restart', 'status', 'uninstall',
                                      'notifications-authorize', 'notifications-test'))
    p.add_argument('--state', type=Path)
    p.add_argument('--interval', type=int, default=10)
    p.add_argument('--execute-live', action='store_true')


def run_command(args):
    from .worker import WorkerBusy, run_worker
    try:
        if args.command == 'background-worker':
            return run_worker(args)
        if args.action == 'install' and args.state is None:
            raise ValueError('state_required')
        if args.action != 'install' and (args.state is not None or args.execute_live or args.interval != 10):
            raise ValueError('configuration_is_install_only')
        print(json.dumps(manage(args)))
        return 0
    except WorkerBusy:
        # A normal exit avoids a launchd crash/restart loop for a duplicate process.
        print('{"error":"worker_already_running"}')
        return 0
    except Exception:
        print('{"error":"background_operation_failed_check_paths_permissions_and_runbook"}')
        return 1
