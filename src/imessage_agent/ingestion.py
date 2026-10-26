"""Persist every raw record and its cursor together; never send during ingestion."""

from .adapters import MessagesReader
from .storage import Store


def ingest_once(store: Store, reader: MessagesReader, limit: int = 100) -> int:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    previous = store.cursor(reader.source_id)
    if previous and previous.generation != reader.generation:
        raise ValueError("source generation changed; explicit reconciliation is required")
    batch = reader.read_after(previous.sequence if previous else 0, limit)
    if batch.source_id != reader.source_id or batch.generation != reader.generation:
        raise ValueError("adapter changed stream identity during read")
    if len(batch.messages) > limit:
        raise ValueError("adapter exceeded batch limit")
    return store.ingest(batch, previous)
