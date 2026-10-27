"""Explicitly enabled text-only transport. Importing never runs AppleScript."""

import re
import subprocess
import sys

from .account_selection import AccountSelectionError, load_account_id, validate_account_id
from .models import Recipient, SendOutcome, SendStatus, payload_hash


# Codes are from the installed Messages.sdef; raw codes avoid name-based dictionary loading.
_ACCOUNT_LOOKUP = '''
        set lookupStage to "account_lookup"
        set accountList to get every «class icsv»
        set accountCount to count of accountList
        set imessageAccounts to {}
        set lookupStage to "service_type"
        repeat with candidate in accountList
            if («property styp» of candidate) is «constant ****sims» then
                set end of imessageAccounts to contents of candidate
            end if
        end repeat
        set imessageCount to count of imessageAccounts
        if imessageCount is not 1 then return "DENIED_ACCOUNT:" & accountCount & ":" & imessageCount
        set targetAccount to item 1 of imessageAccounts
        set lookupStage to "enabled"
        if not («property enbl» of targetAccount) then return "ACCOUNT_DISABLED"
        set lookupStage to "connection_status"
        if («property ssta» of targetAccount) is not «constant ****conn» then return "ACCOUNT_DISCONNECTED"
        set lookupStage to "account_id"
        set targetAccountID to get «property ID  » of targetAccount
        if targetAccountID is "" then return "ACCOUNT_ID_UNAVAILABLE"
'''

# Match only the saved ID; unrelated service-type properties are never queried.
_SELECTED_ACCOUNT_LOOKUP = '''
        set lookupStage to "account_lookup"
        set savedAccountID to {account_id}
        considering case
            set matches to get every «class icsv» whose «property ID  » is savedAccountID
        end considering
        if (count of matches) is 0 then return "SELECTED_ACCOUNT_MISSING"
        if (count of matches) is not 1 then return "SELECTED_ACCOUNT_AMBIGUOUS"
        set targetAccount to item 1 of matches
        set lookupStage to "account_id"
        set targetAccountID to get «property ID  » of targetAccount
        considering case
            if targetAccountID is not savedAccountID then return "ACCOUNT_ID_MISMATCH"
        end considering
        set lookupStage to "service_type"
        if («property styp» of targetAccount) is not «constant ****sims» then return "SELECTED_ACCOUNT_NOT_IMESSAGE"
        set lookupStage to "enabled"
        set accountEnabled to get «property enbl» of targetAccount
        if (class of accountEnabled) is not boolean then return "ACCOUNT_METADATA_INVALID"
        if not accountEnabled then return "ACCOUNT_DISABLED"
        set lookupStage to "connection_status"
        if («property ssta» of targetAccount) is not «constant ****conn» then return "ACCOUNT_DISCONNECTED"
        set accountCount to 1
        set imessageCount to 1
'''

# Use the dictionary's account-ID:handle participant-ID example; unsupported formats
# fail closed. Independently verify ID, handle and ownership, never display names.
_RECIPIENT_LOOKUP = '''
        set lookupStage to "recipient_lookup"
        set expectedParticipantID to targetAccountID & ":" & {address}
        set targetParticipant to get «class pres» id expectedParticipantID
        set lookupStage to "recipient_identity"
        considering case
            if («property ID  » of targetParticipant) is not expectedParticipantID then return "RECIPIENT_ID_MISMATCH"
        end considering
        set lookupStage to "handle"
        considering case
            if («property hndl» of targetParticipant) is not {address} then return "RECIPIENT_MISMATCH"
        end considering
        set lookupStage to "ownership"
        set participantAccount to get «property icsv» of targetParticipant
        considering case
            if («property ID  » of participantAccount) is not targetAccountID then return "RECIPIENT_ACCOUNT_MISMATCH"
        end considering
        set recipientCount to 1
'''


def _script(recipient: Recipient | None, *, body: str | None = None, account_id: str | None = None) -> str:
    # Diagnostic actions are built separately and contain no send event, even in a dead branch.
    if body is not None and account_id is None:
        account_id = load_account_id()
    lookup = (_SELECTED_ACCOUNT_LOOKUP.format(account_id=_literal(validate_account_id(account_id)))
              if account_id is not None else _ACCOUNT_LOOKUP)
    if recipient is not None:
        lookup += _RECIPIENT_LOOKUP.format(address=_literal(recipient.address))
    action = 'return "READY:" & accountCount & ":" & imessageCount & ":" & recipientCount'
    if body is not None:
        action = ('set dispatchStarted to true\n'
                  '«event ichtsend» ' + _literal(body) + ' given «class TO  »:targetParticipant\n'
                  'return "ACCEPTED"')
    return ('set dispatchStarted to false\nset lookupStage to "application"\nset recipientCount to 0\ntry\n'
            'set messagesApplication to "com.apple.MobileSMS"\ntell application id messagesApplication\n' + lookup + action + '\nend tell\n'
            'on error number errorNumber\n'
            'if dispatchStarted then return "DISPATCH_ERROR:" & errorNumber\n'
            'return "PRE:" & lookupStage & ":" & errorNumber\nend try\n')


_PROPERTY_STAGES = {"service_type", "enabled", "connection_status", "account_id", "recipient_identity", "handle", "ownership"}
_STAGES = _PROPERTY_STAGES | {"application", "account_lookup", "recipient_lookup"}


def _error_code(number: int | None, stage: str) -> str:
    if number in (-2740, -2741):
        return "applescript_syntax_failed"
    if number == -2753:
        return "scripting_terminology_unavailable"
    if number in (-1743, -1744):
        return "automation_not_authorized"
    specific = {
        -1728: "apple_event_object_not_found", -1727: "apple_event_invalid_object",
        -1705: "invalid_object_reference", -1719: "invalid_object_reference",
        -1700: "apple_event_type_mismatch", -1703: "apple_event_type_mismatch",
        -10001: "apple_event_type_mismatch", -1701: "apple_event_descriptor_error",
        -1704: "apple_event_descriptor_error", -1708: "apple_event_not_handled",
        -10000: "apple_event_handler_failed", -1712: "apple_event_timed_out",
        -10004: "read_authorization_failed", -10005: "read_authorization_failed",
    }
    if number in specific:
        return specific[number]
    if number in (-1723, -10009, -10010) and stage in _PROPERTY_STAGES:
        return "unsupported_scripting_property"
    if stage in ("recipient_lookup", "recipient_identity", "handle", "ownership"):
        return "recipient_lookup_failed"
    if stage == "application":
        return "application_unavailable"
    return "account_lookup_failed"


def _execute(script: str):
    return subprocess.run(
        ["/usr/bin/osascript", "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, timeout=15, check=False,
    )


def _result(result) -> dict:
    """Parse only fixed tokens/numbers; never return source text, stderr or identifiers."""
    if result.returncode != 0:
        numbers = re.findall(r"\((-?\d+)\)", result.stderr)
        number = int(numbers[-1]) if numbers else None
        return {"error": _error_code(number, "application"), "stage": "unconfirmed", "apple_error_number": number}
    token = result.stdout.strip()
    match = re.fullmatch(r"PRE:([a-z_]+):(-?\d+)", token)
    if match and match[1] in _STAGES:
        return {"error": _error_code(int(match[2]), match[1]), "stage": match[1], "apple_error_number": int(match[2])}
    if re.fullmatch(r"DISPATCH_ERROR:-?\d+", token):
        return {"error": "dispatch_uncertain", "stage": "dispatch"}
    match = re.fullmatch(r"READY:(\d+):(\d+):(\d+)", token)
    if match:
        return dict(accounts=int(match[1]), imessage_accounts=int(match[2]), recipient_matches=int(match[3]))
    match = re.fullmatch(r"DENIED_ACCOUNT:(\d+):(\d+)", token)
    if match:
        return dict(error="imessage_account_unavailable" if int(match[2]) == 0 else "imessage_account_ambiguous",
                    stage="account_lookup", accounts=int(match[1]), imessage_accounts=int(match[2]))
    match = re.fullmatch(r"DENIED_RECIPIENT:(\d+)", token)
    if match:
        return dict(error="recipient_not_found" if int(match[1]) == 0 else "recipient_ambiguous",
                    stage="recipient_lookup", recipient_matches=int(match[1]))
    fixed = {"ACCOUNT_DISABLED": "imessage_account_disabled", "ACCOUNT_DISCONNECTED": "imessage_account_disconnected",
             "ACCOUNT_ID_UNAVAILABLE": "account_lookup_failed", "RECIPIENT_MISMATCH": "recipient_lookup_failed",
             "RECIPIENT_ACCOUNT_MISMATCH": "recipient_account_mismatch",
             "DENIED_ACCOUNT": "denied_account", "DENIED_RECIPIENT": "denied_recipient",
             "SELECTED_ACCOUNT_MISSING": "selected_account_missing",
             "SELECTED_ACCOUNT_AMBIGUOUS": "selected_account_ambiguous",
             "SELECTED_ACCOUNT_NOT_IMESSAGE": "selected_account_not_imessage",
             "ACCOUNT_ID_MISMATCH": "selected_account_identity_mismatch",
             "ACCOUNT_METADATA_INVALID": "selected_account_metadata_invalid",
             "RECIPIENT_ID_MISMATCH": "recipient_identity_mismatch"}
    if token in fixed:
        return {"error": fixed[token], "stage": "pre_dispatch"}
    return {"error": "transport_uncertain", "stage": "unconfirmed"}


def diagnose_applescript(recipient: Recipient | None = None, *, selected: bool = True, account_id: str | None = None) -> dict:
    """Read-only metadata/availability checks; no state or message database access."""
    if sys.platform != "darwin":
        return {"error": "macos_required"}
    if recipient is not None and type(recipient) is not Recipient:
        return {"error": "canonical_recipient_required"}
    try:
        if selected and account_id is None:
            account_id = load_account_id()
        report = _result(_execute(_script(recipient, account_id=account_id)))
    except AccountSelectionError as error:
        return {"error": str(error)}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"error": "diagnostic_unavailable"}
    if recipient is None:
        report.pop("recipient_matches", None)
    return report


_SETUP_ACCOUNTS = '''
set lookupStage to "application"
try
    set messagesApplication to "com.apple.MobileSMS"
    tell application id messagesApplication
        set lookupStage to "account_lookup"
        set accountList to get every «class icsv»
        if (count of accountList) > 25 then return "SETUP_LIMIT"
        set outputLines to "CATALOG"
        repeat with candidate in accountList
            set lookupStage to "service_type"
            try
                set candidateAccount to contents of candidate
                if («property styp» of candidateAccount) is «constant ****sims» then
                    set lookupStage to "account_id"
                    set candidateID to get «property ID  » of candidateAccount
                    if (class of candidateID) is not text then error number -1700
                    if candidateID is "" or (length of candidateID) > 1024 then error number -1700
                    if candidateID contains linefeed or candidateID contains return or candidateID contains tab then error number -1700
                    set outputLines to outputLines & linefeed & "ACCOUNT:" & candidateID
                end if
            on error number lookupError
                if lookupError is -1743 or lookupError is -1744 then error number lookupError
                -- Unreadable unrelated accounts are not candidates for explicit selection.
            end try
        end repeat
        return outputLines
    end tell
on error number errorNumber
    return "PRE:" & lookupStage & ":" & errorNumber
end try
'''


def list_imessage_accounts() -> list[str]:
    """Private catalog for local setup; IDs must never be printed by the caller."""
    if sys.platform != "darwin":
        raise AccountSelectionError("macos_required")
    try:
        result = _execute(_SETUP_ACCOUNTS)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise AccountSelectionError("account_setup_unavailable") from None
    if result.returncode == 0 and result.stdout.strip() == "SETUP_LIMIT":
        raise AccountSelectionError("account_setup_limit")
    lines = result.stdout.rstrip("\r\n").splitlines()
    if result.returncode != 0 or not lines or lines[0] != "CATALOG":
        raise AccountSelectionError(_result(result).get("error", "invalid_account_catalog"))
    if len(lines) > 26:
        raise AccountSelectionError("invalid_account_catalog")
    accounts = []
    for line in lines[1:]:
        if not line.startswith("ACCOUNT:"):
            raise AccountSelectionError("invalid_account_catalog")
        account_id = validate_account_id(line[len("ACCOUNT:"):])
        if account_id in accounts:
            raise AccountSelectionError("ambiguous_account_catalog")
        accounts.append(account_id)
    if not accounts:
        raise AccountSelectionError("no_verified_imessage_accounts")
    return accounts


# Probe only account metadata; never IDs, descriptions, recipients or conversations.
_PROBE_PROPERTIES = {
    "service_type": ("styp", '''
        if propertyValue is «constant ****sims» then
            set propertyResult to "IMESSAGE"
        else if propertyValue is «constant ****ssms» then
            set propertyResult to "SMS"
        else if propertyValue is «constant ****srcs» then
            set propertyResult to "RCS"
        else
            set propertyResult to "OTHER"
        end if
    '''),
    "enabled": ("enbl", '''
        if (class of propertyValue) is not boolean then
            set propertyResult to "INVALID_TYPE"
        else if propertyValue then
            set propertyResult to "ENABLED"
        else
            set propertyResult to "DISABLED"
        end if
    '''),
    "connection_status": ("ssta", '''
        if propertyValue is «constant ****conn» then
            set propertyResult to "CONNECTED"
        else if propertyValue is «constant ****dcon» then
            set propertyResult to "DISCONNECTED"
        else if propertyValue is «constant ****cong» then
            set propertyResult to "CONNECTING"
        else if propertyValue is «constant ****dcng» then
            set propertyResult to "DISCONNECTING"
        else
            set propertyResult to "OTHER"
        end if
    '''),
    "account_id": ("ID  ", '''
        if (class of propertyValue) is not text then
            set propertyResult to "INVALID_TYPE"
        else if propertyValue is "" then
            set propertyResult to "EMPTY"
        else
            set propertyResult to "PRESENT"
        end if
    '''),
}
_PROBE_OBJECTS = {"list_reference": "accountRef", "dereferenced": "(contents of accountRef)",
                  "direct_index": "(«class icsv» accountIndex)"}
_PROBE_VALUES = {"IMESSAGE", "SMS", "RCS", "OTHER", "INVALID_TYPE", "ENABLED", "DISABLED",
                 "CONNECTED", "DISCONNECTED", "CONNECTING", "DISCONNECTING", "EMPTY", "PRESENT"}


def _account_probe_script(*, correlate: bool = False) -> str:
    script = '''
set lookupStage to "application"
try
    set messagesApplication to "com.apple.MobileSMS"
    tell application id messagesApplication
        set lookupStage to "account_count"
        set accountCount to count of every «class icsv»
        if accountCount > 10 then return "PROBE_LIMIT"
        set lookupStage to "account_enumeration"
        set accountList to get every «class icsv»
        if (count of accountList) is not accountCount then return "PROBE_CHANGED"
        set outputLines to "ACCOUNTS:" & accountCount
        set accountIndex to 0
        repeat with accountRef in accountList
            set accountIndex to accountIndex + 1
    '''
    if correlate:
        script += '''
            set identityStart to missing value
            try
                set identityStart to get «property ID  » of (contents of accountRef)
            end try
        '''
    objects = {"dereferenced": _PROBE_OBJECTS["dereferenced"]} if correlate else _PROBE_OBJECTS
    for style, expression in objects.items():
        for name, (code, classify) in _PROBE_PROPERTIES.items():
            prefix = style + ":" + name + ":"
            if correlate:
                prefix = '" & accountIndex & ":' + prefix
            script += ("\ntry\nset propertyValue to get «property " + code + "» of " + expression + "\n"
                       + classify + '\nset outputLines to outputLines & linefeed & "' + prefix + '" & propertyResult\n'
                       + 'on error number propertyError\nset outputLines to outputLines & linefeed & "'
                       + prefix + 'ERROR:" & propertyError\nend try\n')
    if correlate:
        script += '''
            try
                set identityEnd to get «property ID  » of (contents of accountRef)
                set identityResult to "UNAVAILABLE"
                if (class of identityStart) is text and identityStart is not "" then
                    considering case
                        if identityStart is identityEnd then
                            set identityResult to "STABLE"
                        else
                            set identityResult to "CHANGED"
                        end if
                    end considering
                end if
                set outputLines to outputLines & linefeed & accountIndex & ":dereferenced:identity:" & identityResult
            on error number identityError
                set outputLines to outputLines & linefeed & accountIndex & ":dereferenced:identity:ERROR:" & identityError
            end try
        '''
    return script + '''
        end repeat
        return outputLines
    end tell
on error number errorNumber
    return "PROBE_ERROR:" & lookupStage & ":" & errorNumber
end try
'''


def probe_accounts(*, correlate: bool = False) -> dict:
    """No-send, bounded metadata matrix; never a fallback selection policy."""
    if sys.platform != "darwin":
        return {"error": "macos_required"}
    try:
        result = _execute(_account_probe_script(correlate=correlate))
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"error": "diagnostic_unavailable"}
    if result.returncode != 0:
        return _result(result)
    token = result.stdout.strip()
    match = re.fullmatch(r"PROBE_ERROR:(application|account_count|account_enumeration):(-?\d+)", token)
    if match:
        number = int(match[2])
        return {"error": _error_code(number, match[1]), "stage": match[1], "apple_error_number": number}
    if token in ("PROBE_LIMIT", "PROBE_CHANGED"):
        return {"error": "account_probe_limit" if token == "PROBE_LIMIT" else "accounts_changed_during_probe"}
    lines = token.splitlines()
    header = re.fullmatch(r"ACCOUNTS:([0-9]|10)", lines[0]) if lines else None
    if not header:
        return {"error": "invalid_probe_output"}
    count = int(header[1])
    objects = {"dereferenced"} if correlate else set(_PROBE_OBJECTS)
    properties = set(_PROBE_PROPERTIES) | ({"identity"} if correlate else set())
    if len(lines) != 1 + count * len(objects) * len(properties):
        return {"error": "invalid_probe_output"}
    aggregates = {}
    correlated = {slot: {} for slot in range(1, count + 1)}
    for line in lines[1:]:
        fields = line.split(":")
        slot = None
        if correlate:
            if not fields[0].isdecimal() or len(fields[0]) > 2:
                return {"error": "invalid_probe_output"}
            slot = int(fields.pop(0))
            if slot not in correlated:
                return {"error": "invalid_probe_output"}
        if len(fields) not in (3, 4) or fields[0] not in objects or fields[1] not in properties:
            return {"error": "invalid_probe_output"}
        style, stage, value = fields[:3]
        entry = {"object_access": style, "stage": stage}
        if len(fields) == 4 and value == "ERROR" and re.fullmatch(r"-?\d{1,9}", fields[3]):
            number = int(fields[3])
            entry.update(error=_error_code(number, stage), apple_error_number=number)
        elif len(fields) == 3 and (value in _PROBE_VALUES or (correlate and stage == "identity" and value in ("STABLE", "CHANGED", "UNAVAILABLE"))):
            entry["result"] = value.lower()
        else:
            return {"error": "invalid_probe_output"}
        if correlate:
            if stage in correlated[slot]:
                return {"error": "invalid_probe_output"}
            correlated[slot][stage] = {key: value for key, value in entry.items() if key not in ("object_access", "stage")}
        key = tuple(entry.items())
        if key not in aggregates:
            aggregates[key] = dict(entry, count=0)
        aggregates[key]["count"] += 1
    # A complete matrix is required: malformed output cannot masquerade as compatibility.
    for style in objects:
        for stage in properties:
            if sum(entry["count"] for entry in aggregates.values()
                   if entry["object_access"] == style and entry["stage"] == stage) != count:
                return {"error": "invalid_probe_output"}
    if count == 0:
        return {"accounts": 0, "probes": [], "error": "imessage_account_unavailable"}
    if correlate:
        checks = [dict(sample=slot, **properties) for slot, properties in correlated.items()]
        return _correlated_account_report(checks)
    return {"accounts": count, "probes": list(aggregates.values())}


def _correlated_account_report(checks: list[dict]) -> dict:
    """Assess evidence conservatively; never supply a reference to the transport."""
    imessage = [row for row in checks if row["service_type"].get("result") == "imessage"]
    unknown = sum(row["service_type"].get("result") not in ("imessage", "sms", "rcs") for row in checks)
    report = dict(accounts=len(checks), account_checks=checks, known_imessage_accounts=len(imessage),
                  unknown_service_accounts=unknown, account_selection_verified=False)
    if any(row["identity"].get("result") != "stable" for row in checks):
        report["error"] = "account_identity_unverified"
    elif unknown:
        report["error"] = "unknown_service_types_prevent_unique_selection"
    elif len(imessage) != 1:
        report["error"] = "imessage_account_unavailable" if not imessage else "imessage_account_ambiguous"
    else:
        row = imessage[0]
        if (row["enabled"].get("result") != "enabled"
                or row["connection_status"].get("result") != "connected"
                or row["account_id"].get("result") != "present"):
            report["error"] = "imessage_account_metadata_not_ready"
        else:
            report["account_selection_verified"] = True
    return report


def _literal(value: str) -> str:
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value) or "\x7f" in value:
        raise ValueError("unsupported_text_control_character")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'


class AppleScriptMessagesAdapter:
    """Trusted Outbound callers only; no environment variable enables sending."""

    def __init__(self, *, enabled: bool = False, account_id: str | None = None) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled_requires_boolean")
        self.enabled = enabled
        self.account_id = validate_account_id(account_id) if account_id is not None else None

    def dispatch(self, recipient: Recipient, body: str) -> SendOutcome:
        if not self.enabled:
            return SendOutcome(SendStatus.FAILED, "transport_disabled")
        if sys.platform != "darwin":
            return SendOutcome(SendStatus.FAILED, "macos_required")
        if type(recipient) is not Recipient:
            raise ValueError("canonical_recipient_required")
        payload_hash(recipient, body)
        if len(body) > 4000:
            raise ValueError("text_too_long")
        try:
            account_id = load_account_id()
            if self.account_id is not None and account_id != self.account_id:
                return SendOutcome(SendStatus.FAILED, "schedule_account_selection_changed")
        except AccountSelectionError as error:
            return SendOutcome(SendStatus.FAILED, str(error))
        script = _script(recipient, body=body, account_id=account_id)
        try:
            result = _execute(script)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return SendOutcome(SendStatus.UNKNOWN, "transport_uncertain")
        if result.returncode == 0 and result.stdout.strip() == "ACCEPTED":
            return SendOutcome(SendStatus.ACCEPTED, "transport_accepted_delivery_unverified")
        report = _result(result)
        # Only structured pre-dispatch failures prove that send was never reached.
        # Nonzero process exits/timeouts cannot reliably establish the execution stage.
        if "error" not in report:
            return SendOutcome(SendStatus.UNKNOWN, "transport_uncertain")
        known_pre_dispatch = report.get("stage") not in (None, "dispatch", "unconfirmed")
        return SendOutcome(SendStatus.FAILED if known_pre_dispatch else SendStatus.UNKNOWN,
                           report["error"])
