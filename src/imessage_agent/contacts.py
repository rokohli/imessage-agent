"""Read-only native Contacts labels. Canonical send identities never change."""
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from .notifications import application_directory


KEY = 'contacts:cache'


def normalize_handle(handle, country_code=None):
    if '@' in handle:
        # Preserve the local part just as Recipient does; do not guess case aliases.
        local, domain = handle.rsplit('@', 1)
        return local + '@' + domain.casefold()
    if not re.fullmatch(r'[+\d\s().-]+', handle):
        return None
    digits = re.sub(r'\D', '', handle)
    if handle.strip().startswith('00'):
        digits = digits[2:]
        return '+' + digits if 2 <= len(digits) <= 15 and not digits.startswith('0') else None
    if handle.strip().startswith('+'):
        return '+' + digits if 2 <= len(digits) <= 15 and not digits.startswith('0') else None
    if country_code and digits and not digits.startswith('0'):
        return '+' + country_code + digits
    return None  # No suffix matching or implicit country guesses.


def helper_path():
    return application_directory() / 'Contacts.app'


def install_helper():
    if sys.platform != 'darwin':
        raise ValueError('macos_required')
    target = helper_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        raise ValueError('contacts_helper_already_installed')
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        app = Path(directory) / 'Contacts.app'
        binary = app / 'Contents/MacOS/Contacts'
        binary.parent.mkdir(parents=True)
        subprocess.run(['/usr/bin/xcrun', 'swiftc', str(Path(__file__).with_name('Contacts.swift')),
                        '-module-name', 'ContactResolver', '-o', str(binary), '-framework', 'AppKit', '-framework', 'Contacts'],
                       check=True, timeout=120, capture_output=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier='io.github.rokohli.imessage-agent.contacts', CFBundleExecutable='Contacts',
            CFBundleName='iMessage Agent Contacts', CFBundlePackageType='APPL', CFBundleVersion='1',
            LSUIElement=True, NSContactsUsageDescription='Show saved contact names locally for iMessage conversations.')))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)],
                       check=True, timeout=30, capture_output=True)
        app.rename(target)


def native_contacts(action='status'):
    if action not in ('status', 'authorize', 'export'):
        raise ValueError('invalid_contacts_action')
    if sys.platform != 'darwin' or not helper_path().is_dir():
        return dict(status='unavailable', contacts=[])
    try:
        with tempfile.TemporaryDirectory(prefix='imessage-contacts-') as directory:
            output = Path(directory) / 'result.json'
            subprocess.run(['/usr/bin/open', '-W', '-n', str(helper_path()), '--args', action, str(output)],
                           check=True, timeout=90 if action == 'authorize' else 30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = json.loads(output.read_text())
            if result['status'] not in ('authorized', 'denied', 'not_determined', 'unavailable'):
                raise ValueError('invalid_contacts_status')
            return result
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return dict(status='unavailable', contacts=[])


class Contacts:
    def __init__(self, store, *, native=native_contacts, clock=time.time):
        self.db, self.native, self.clock = store.connection, native, clock
        self._cache = None

    def invalidate(self):
        with self.db:
            self.db.execute('DELETE FROM dashboard_preferences WHERE key=?', (KEY,))
        self._cache = None

    def refresh(self, *, authorize=False, country_code=None):
        if country_code is not None and not re.fullmatch(r'[1-9][0-9]{0,2}', country_code):
            raise ValueError('invalid_contact_country_code')
        if authorize:
            self.native('authorize')
        result = self.native('export')
        cache = dict(status=result['status'], refreshed_at=self.clock(), country_code=country_code,
                     contacts=result.get('contacts', []) if result['status'] == 'authorized' else [])
        with self.db:
            self.db.execute('INSERT INTO dashboard_preferences VALUES (?,?) ON CONFLICT(key) '
                            'DO UPDATE SET value=excluded.value', (KEY, json.dumps(cache)))
        self._cache = cache
        return dict(status=cache['status'], auto_sending=False)

    def cache(self):
        if self._cache and self.clock() - self._cache['refreshed_at'] >= 3600:
            self._cache = None
        if self._cache is None:
            row = self.db.execute('SELECT value FROM dashboard_preferences WHERE key=?', (KEY,)).fetchone()
            cache = json.loads(row[0]) if row else {}
            # Check revocation even inside the TTL. Never serve stale names after denial/failure.
            if cache and self.native('status')['status'] != 'authorized':
                self.invalidate()
                cache = {}
            elif cache and self.clock() - cache['refreshed_at'] >= 3600:
                self.refresh(country_code=cache.get('country_code'))
                cache = self._cache
            self._cache = cache
        return self._cache

    def resolve(self, handle):
        cache = self.cache()
        key = normalize_handle(handle, cache.get('country_code'))
        matches = {c['id']: c for c in cache.get('contacts', []) if key and any(
            normalize_handle(h, cache.get('country_code')) == key for h in c['handles'])}
        if len(matches) != 1:
            return dict(display_name=handle, handles=[handle], resolution='ambiguous' if matches else 'unresolved')
        contact = next(iter(matches.values()))
        name = contact['name'].strip()
        duplicates = sum(c['name'].strip().casefold() == name.casefold() for c in cache['contacts']) > 1
        return dict(display_name=(name + ' · ' + handle if duplicates else name) if name else handle,
                    handles=contact['handles'], resolution='resolved')


def add_commands(commands):
    parser = commands.add_parser('contacts', help='Read-only native Contacts; no sending')
    parser.add_argument('action', choices=('install', 'authorize', 'refresh', 'invalidate'))
    parser.add_argument('--state', type=Path)
    parser.add_argument('--country-code', help='Explicit dialing prefix for national numbers, e.g. 1')


def run_command(args):
    from .storage import Store
    from .smoke import _state_path
    store = None
    try:
        if args.action == 'install':
            install_helper()
            result = dict(installed=True)
        else:
            if args.state is None:
                raise ValueError('state_required')
            store = Store(_state_path(Path('/nonexistent-contacts-source'), args.state))
            contacts = Contacts(store)
            if args.action == 'invalidate':
                contacts.invalidate()
                result = dict(invalidated=True)
            else:
                result = contacts.refresh(authorize=args.action == 'authorize', country_code=args.country_code)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"error":"contacts_unavailable","auto_sending":false}')
        return 1
    finally:
        if store:
            store.close()
