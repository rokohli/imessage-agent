"""Trusted control-plane gate. Do not expose Store or this module as AI tools."""

from __future__ import annotations

import time
from collections.abc import Callable

from .adapters import MessagesTransport, MockMessagesAdapter, create_adapter
from .applescript import AppleScriptMessagesAdapter
from .models import Recipient, SendAttempt, SendOutcome, SendStatus
from .storage import Store


class Outbound:
    def __init__(
        self,
        store: Store,
        adapter: MessagesTransport | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.adapter = adapter if adapter is not None else create_adapter()
        if type(self.adapter) not in (MockMessagesAdapter, AppleScriptMessagesAdapter):
            raise ValueError("unsupported_outbound_transport")
        self.clock = clock

    def submit(
        self,
        attempt_id: str,
        recipient: Recipient,
        body: str,
        approval_id: str | None = None,
        *, reply_to: tuple[str, str] | None = None,
    ) -> SendAttempt:
        controlled = type(self.adapter) is AppleScriptMessagesAdapter
        if controlled and not self.adapter.enabled:
            raise ValueError("transport_disabled")
        attempt, claimed = self.store.claim_send(
            attempt_id, recipient, body, approval_id, self.clock(), controlled=controlled, reply_to=reply_to
        )
        if not claimed:
            return attempt
        try:
            outcome = self.adapter.dispatch(recipient, body)
        except Exception:
            # Do not store arbitrary exception text that could contain message data.
            outcome = SendOutcome(SendStatus.UNKNOWN, "Adapter raised; do not retry automatically")
        if not isinstance(outcome, SendOutcome):
            outcome = SendOutcome(SendStatus.UNKNOWN, "Adapter returned an unstructured outcome")
        return self.store.finish_send(attempt_id, outcome)
