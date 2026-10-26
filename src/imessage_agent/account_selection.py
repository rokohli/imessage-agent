"""One private local account selection; separate from approvals and attempt state."""

import json
import os
import stat
import tempfile
from pathlib import Path


class AccountSelectionError(Exception):
    """Only fixed, public error codes belong in this exception."""


def selection_path() -> Path:
    return Path.home() / ".local/state/imessage-agent/imessage-account.json"


def validate_account_id(value) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise AccountSelectionError("invalid_account_selection")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AccountSelectionError("invalid_account_selection") from None
    return value


def load_account_id() -> str:
    try:
        fd = os.open(selection_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 8192):
                raise AccountSelectionError("unsafe_account_selection")
            data = json.loads(file.read(8193))
            if not isinstance(data, dict) or set(data) != {"account_id"}:
                raise AccountSelectionError("invalid_account_selection")
            return validate_account_id(data["account_id"])
    except FileNotFoundError:
        raise AccountSelectionError("account_selection_required") from None
    except (OSError, ValueError, UnicodeError):
        raise AccountSelectionError("account_selection_unreadable") from None


def save_account_id(account_id: str) -> None:
    account_id = validate_account_id(account_id)
    path = selection_path()
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_uid != os.getuid()):
            raise AccountSelectionError("unsafe_account_selection_directory")
        # Publish a complete private file atomically, without replacing any existing selection.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".account-") as file:
            json.dump({"account_id": account_id}, file)
            file.flush()
            os.fsync(file.fileno())
            os.link(file.name, path, follow_symlinks=False)
    except FileExistsError:
        raise AccountSelectionError("account_selection_already_exists") from None
    except OSError:
        raise AccountSelectionError("account_selection_unwritable") from None
