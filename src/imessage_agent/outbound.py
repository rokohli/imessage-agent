"""Trusted control-plane gate. Do not expose Store or this module as AI tools."""

from __future__ import annotations

import time
from collections.abc import Callable

from .adapters import MockMessagesAdapter, create_adapter
from .models import Recipient, SendAttempt, SendOutcome, SendStatus
from .storage import Store


class Outbound:
    def __init__(
        self,
        store: Store,
        adapter: MockMessagesAdapter | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.adapter = adapter if adapter is not None else create_adapter()
        if type(self.adapter) is not MockMessagesAdapter:
            raise ValueError("Phase 1 permits only the synthetic mock adapter")
        self.clock = clock

    def submit(
        self,
        attempt_id: str,
        recipient: Recipient,
        body: str,
        approval_id: str | None = None,
    ) -> SendAttempt:
        attempt, claimed = self.store.claim_send(
            attempt_id, recipient, body, approval_id, self.clock()
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
