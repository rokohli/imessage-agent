"""Read-only native Contacts labels. Canonical send identities never change."""
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
from .notifications import application_directory


KEY = 'contacts:settings'


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


def install_helper(*, replace=False):
    if sys.platform != 'darwin':
        raise ValueError('macos_required')
    target = helper_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() and not replace:
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
            CFBundleName='iMessage Agent Contacts', CFBundlePackageType='APPL', CFBundleVersion='2',
            LSUIElement=True, NSContactsUsageDescription='Look up recipient names locally when you search macOS Contacts.')))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)],
                       check=True, timeout=30, capture_output=True)
        previous = Path(directory) / 'PreviousContacts.app'
        if target.exists():
            target.rename(previous)
        try:
            app.rename(target)
        except BaseException:
            if previous.exists():
                previous.rename(target)
            raise


def native_contacts(action='status', query=None):
    if action not in ('status', 'authorize', 'search'):
        raise ValueError('invalid_contacts_action')
    if action == 'search' and (not isinstance(query, str) or not 2 <= len(query.strip()) <= 100):
        raise ValueError('contact_query_must_be_2_to_100_characters')
    if sys.platform != 'darwin' or not helper_path().is_dir():
        return dict(status='unavailable', contacts=[])
    try:
        with tempfile.TemporaryDirectory(prefix='imessage-contacts-') as directory:
            output = Path(directory) / 'result.json'
            args = ['/usr/bin/open', '-W', '-n', str(helper_path()), '--args', action, str(output)]
            if action == 'search':
                query_path = Path(directory) / 'query.json'
                query_path.write_text(json.dumps(query.strip()))
                query_path.chmod(0o600)
                args.append(str(query_path))
            subprocess.run(args,
                           check=True, timeout=90 if action == 'authorize' else 30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = json.loads(output.read_text())
            if result['status'] not in ('authorized', 'denied', 'not_determined', 'unavailable'):
                raise ValueError('invalid_contacts_status')
            return result
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return dict(status='unavailable', contacts=[])


class Contacts:
    def __init__(self, store, *, native=native_contacts):
        self.store, self.db, self.native = store, store.connection, native

    def authorize(self, *, country_code=None):
        if country_code is not None and not re.fullmatch(r'[1-9][0-9]{0,2}', country_code):
            raise ValueError('invalid_contact_country_code')
        result = self.native('authorize')
        with self.db:
            self.db.execute('INSERT INTO dashboard_preferences VALUES (?,?) ON CONFLICT(key) '
                            'DO UPDATE SET value=excluded.value', (KEY, json.dumps(dict(country_code=country_code))))
        return dict(status=result['status'], auto_sending=False)

    def search(self, query):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 100:
            raise ValueError('contact_query_must_be_2_to_100_characters')
        from .models import Recipient
        row = self.db.execute('SELECT value FROM dashboard_preferences WHERE key=?', (KEY,)).fetchone()
        country_code = json.loads(row[0]).get('country_code') if row else None
        result = self.native('search', query.strip())
        matches, seen = [], set()
        if result['status'] == 'authorized':
            for contact in result.get('contacts', [])[:10]:
                for handle in contact['handles']:
                    normalized = normalize_handle(handle, country_code)
                    if not normalized:
                        continue
                    try:
                        recipient = Recipient.parse(normalized)
                    except ValueError:
                        continue
                    name = contact['name'].strip()[:200] or recipient.address
                    if (name, recipient.key) not in seen:
                        matches.append(dict(display_name=name, recipient=recipient.address,
                                            recipient_key=recipient.key))
                        seen.add((name, recipient.key))
        # Search results are never written to state, profiles, or authorizations.
        return dict(status=result['status'], matches=matches[:40])

    def resolve(self, handle):
        from .models import Recipient
        contact = self.store.contact(Recipient.parse(handle))
        name = contact.display_name if contact else handle
        return dict(display_name=name, handles=[handle],
                    resolution='resolved' if name != handle else 'unresolved')


def add_commands(commands):
    parser = commands.add_parser('contacts', help='Read-only native Contacts; no sending')
    parser.add_argument('action', choices=('install', 'authorize'))
    parser.add_argument('--replace', action='store_true', help='Replace an older helper after successfully building the new one')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--country-code', help='Explicit dialing prefix for national numbers, e.g. 1')


def run_command(args):
    from .storage import Store
    from .smoke import _state_path
    store = None
    try:
        if args.action == 'install':
            install_helper(replace=args.replace)
            result = dict(installed=True)
        else:
            if args.state is None:
                raise ValueError('state_required')
            store = Store(_state_path(Path('/nonexistent-contacts-source'), args.state))
            contacts = Contacts(store)
            result = contacts.authorize(country_code=args.country_code)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"error":"contacts_unavailable","auto_sending":false}')
        return 1
    finally:
        if store:
            store.close()
