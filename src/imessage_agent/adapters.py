"""Read protocol and mock-only dispatch factory; real reading is in macos.py."""

from __future__ import annotations

from typing import Protocol

from .models import Batch, Message, Recipient, SendOutcome, SendStatus


class MessagesReader(Protocol):
    source_id: str
    generation: str

    def read_after(self, sequence: int, limit: int) -> Batch:
        """Return a strictly sequence-ordered prefix, without side effects.

        Source/generation identify one append-only stream. Readers must detect replacement/reset within their documented limits and
        never substitute timestamps for order.
        """
        ...


class MessagesTransport(Protocol):
    def dispatch(self, recipient: Recipient, body: str) -> SendOutcome:
        """Accept one exact individual text payload; never infer delivery."""
        ...


class MockMessagesAdapter:
    """Synthetic records only. Dispatch records a call; it cannot send anything."""

    def __init__(
        self,
        messages: tuple[Message, ...] = (),
        *,
        source_id: str = "mock",
        generation: str = "synthetic-v1",
        outcome: SendOutcome | None = None,
    ) -> None:
        if len({m.sequence for m in messages}) != len(messages):
            raise ValueError("mock sequences must be unique")
        self.messages = tuple(sorted(messages, key=lambda m: m.sequence))
        self.source_id = source_id
        self.generation = generation
        self.outcome = outcome or SendOutcome(SendStatus.UNKNOWN, "No synthetic outcome configured")
        self.calls: list[tuple[Recipient, str]] = []

    def read_after(self, sequence: int, limit: int) -> Batch:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = tuple(m for m in self.messages if m.sequence > sequence)[:limit]
        return Batch(self.source_id, self.generation, rows)

    def dispatch(self, recipient: Recipient, body: str) -> SendOutcome:
        self.calls.append((recipient, body))
        return self.outcome


def create_adapter(mode: str = "mock") -> MockMessagesAdapter:
    if mode != "mock":
        raise ValueError("factory permits mock only; use MacOSMessagesReader for explicit read-only access")
    return MockMessagesAdapter()
