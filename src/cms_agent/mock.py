"""Deterministic fake CMS for tests; never connects to production."""

from __future__ import annotations

from .engine import Item, RemoteRecord


class MockCMS:
    origin = 'https://synthetic.invalid'

    def __init__(self):
        self.records: dict[str, RemoteRecord] = {}
        self.created = 0
        self.published = 0

    def find(self, item: Item) -> list[RemoteRecord]:
        return [r for r in self.records.values()
                if r.number == item.document_number and r.module == item.target_module]

    def create_draft(self, item: Item) -> str:
        self.created += 1
        record_id = f"fake-{self.created}"
        self.records[record_id] = RemoteRecord(
            record_id, item.document_number, item.target_module,
            item.fingerprint, item.source_sha256, "DRAFT", item.title, dict(item.fields))
        return record_id

    def readback(self, record_id: str) -> RemoteRecord:
        return self.records[record_id]

    def publish_step1(self, record_id: str) -> None:
        record = self.records[record_id]
        if record.status != "DRAFT":
            raise RuntimeError("unexpected status")
        self.published += 1
        self.records[record_id] = RemoteRecord(
            record.record_id, record.number, record.module, record.fingerprint,
            record.attachment_sha256, "PUBLISHED", record.title, record.fields)
