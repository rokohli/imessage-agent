"""Data contracts shared by storage, policy, and adapters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Recipient:
    """An exact address, never a contact name, selector, or group identifier."""

    kind: str
    address: str

    def __post_init__(self) -> None:
        if not isinstance(self.address, str):
            raise ValueError("recipient address must be text")
        if self.kind == "phone":
            if not re.fullmatch(r"\+[1-9][0-9]{1,14}", self.address):
                raise ValueError("phone must use explicit international E.164 syntax")
        elif self.kind == "email":
            if self.address.count("@") != 1:
                raise ValueError("email must be a bare address")
            local, domain = self.address.rsplit("@", 1)
            if (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
                    or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local):
                raise ValueError("email local part must use unquoted ASCII syntax")
            domain = domain.encode("idna").decode().lower()
            if (len(local + "@" + domain) > 254 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")
            )):
                raise ValueError("email domain must contain valid DNS labels")
            # Preserve local-part case: merging addresses must be an explicit decision.
            object.__setattr__(self, "address", local + "@" + domain)
        else:
            raise ValueError("only individual phone and email recipients are supported")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.address}"

    @classmethod
    def parse(cls, value: str) -> Recipient:
        if not isinstance(value, str) or value != value.strip():
            raise ValueError("recipient must not contain surrounding whitespace")
        return cls("email" if "@" in value else "phone", value)


class Direction(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    SYSTEM = "system"


class SendStatus(str, Enum):
    ACCEPTED = "accepted"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SendOutcome:
    status: SendStatus
    detail: str = ""
    provider_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SendStatus):
            raise ValueError("send status must be explicit, never inferred from text")


@dataclass(frozen=True)
class Message:
    sequence: int
    guid: str
    chat_id: str
    direction: Direction
    sender: Recipient | None
    body: str | None
    occurred_at: float

    def __post_init__(self) -> None:
        import math

        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("message sequence must be a positive integer")
        if not self.guid or not self.chat_id:
            raise ValueError("message GUID and chat identity are required")
        if not isinstance(self.direction, Direction):
            raise ValueError("explicit message direction is required")
        if self.direction == Direction.INBOUND and self.sender is None:
            raise ValueError("inbound messages require an exact sender")
        if self.body is not None and not isinstance(self.body, str):
            raise ValueError("body must be text or None")
        if not math.isfinite(self.occurred_at):
            raise ValueError("message timestamp must be finite")


@dataclass(frozen=True)
class Batch:
    source_id: str
    generation: str
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class Cursor:
    source_id: str
    generation: str
    sequence: int


@dataclass(frozen=True)
class Contact:
    recipient: Recipient
    display_name: str
    outbound_allowed: bool = False


@dataclass(frozen=True)
class Approval:
    id: str
    payload_hash: str
    expires_at: float
    consumed_by: str | None


@dataclass(frozen=True)
class Schedule:
    id: str
    recipient: Recipient
    body: str
    window_start: float
    window_end: float
    send_at: float
    timezone: str
    approval_id: str
    status: str = "pending"


@dataclass(frozen=True)
class SendAttempt:
    id: str
    recipient: Recipient
    payload_hash: str
    approval_id: str | None
    outcome: SendOutcome
    created_at: float


def payload_hash(recipient: Recipient, body: str) -> str:
    if type(recipient) is not Recipient:
        raise ValueError("canonical_recipient_required")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("a non-empty text body is required")
    payload = json.dumps([recipient.key, body], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
