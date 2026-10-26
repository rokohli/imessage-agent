"""Local direction checks for an owner-selected fictional-message manifest."""

import json
from pathlib import Path

from .macos import MacOSMessagesReader, MessagesAccessError
from .models import Direction
from .smoke import SmokeError


def validate_classification(database: Path, selection: Path) -> dict:
    try:
        with selection.expanduser().open(encoding="utf-8") as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError
        records = json.loads(raw)
        if not isinstance(records, list) or not 1 <= len(records) <= 25:
            raise ValueError
        sequences = set()
        for record in records:
            if (not isinstance(record, dict) or set(record) != {"sequence", "direction"}
                    or type(record["sequence"]) is not int or record["sequence"] <= 0
                    or record["sequence"] in sequences):
                raise ValueError
            Direction(record["direction"])
            sequences.add(record["sequence"])
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SmokeError("invalid_fictional_selection") from None
    matched = 0
    try:
        with MacOSMessagesReader(database) as reader:
            for record in records:
                batch = reader.read_after(record["sequence"] - 1, 1)
                if not batch.messages or batch.messages[0].sequence != record["sequence"]:
                    raise SmokeError("selected_record_missing")
                matched += batch.messages[0].direction.value == record["direction"]
    except MessagesAccessError:
        raise
    return dict(checked=len(records), matched=matched, mismatched=len(records) - matched)
