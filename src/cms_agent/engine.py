"""Offline planning and gated CMS execution; no portal-specific assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
import json
import os
from .approval import ApprovalError, verify_operator_approval


PRODUCTS = {"CSDL", "TIN_VAN", "TIN_BAI", "TIN_SU_KIEN"}
ACTIONS = {"SAVE_DRAFT", "PUBLISH_STEP1"}


class Hold(Exception):
    """Do not guess, retry blindly, or escalate permissions."""


@dataclass(frozen=True)
class Item:
    item_id: str
    product_class: str
    document_number: str
    title: str
    target_module: str
    source_path: Path
    source_sha256: str
    fields: dict[str, str]
    action: str

    @property
    def fingerprint(self) -> str:
        payload = {
            "product": self.product_class,
            "number": self.document_number,
            "item_id": self.item_id,
            "title": self.title,
            "module": self.target_module,
            "source_sha256": self.source_sha256,
            "fields": self.fields,
        }
        return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Batch:
    case_id: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class RemoteRecord:
    record_id: str
    number: str
    module: str
    fingerprint: str
    attachment_sha256: str
    status: str  # DRAFT | PUBLISHED; unknown statuses must block


class CMSAdapter(Protocol):
    def find(self, item: Item) -> list[RemoteRecord]: ...
    def create_draft(self, item: Item) -> str: ...
    def readback(self, record_id: str) -> RemoteRecord: ...
    def publish_step1(self, record_id: str) -> None: ...


def _required_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Hold(f"MISSING_OR_INVALID_{key.upper()}")
    return value.strip()


def _source(root: Path, relative_name: str, expected_sha: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise Hold("SOURCE_PATH_NOT_RELATIVE")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise Hold("SOURCE_OUTSIDE_ALLOWED_ROOT") from error
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise Hold("SOURCE_PDF_MISSING")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise Hold("SOURCE_NOT_PDF")
    digest = sha256(path.read_bytes()).hexdigest()
    if expected_sha.lower() != digest:
        raise Hold("SOURCE_SHA256_MISMATCH")
    return path


def load_batch(manifest_path: str | Path) -> Batch:
    manifest = Path(manifest_path).resolve()
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    if obj.get("schema_version") != 1:
        raise Hold("UNSUPPORTED_SCHEMA_VERSION")
    case_id = _required_str(obj, "case_id")
    source_root_name = _required_str(obj, "source_root")
    source_root = Path(source_root_name)
    root = (source_root if source_root.is_absolute() else manifest.parent / source_root).resolve()
    if not root.is_dir():
        raise Hold("SOURCE_ROOT_MISSING")
    rows = obj.get("items")
    if not isinstance(rows, list) or not rows:
        raise Hold("EMPTY_OR_INVALID_BATCH")
    seen = set()
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise Hold("ITEM_NOT_OBJECT")
        item_id = _required_str(row, "item_id")
        if item_id in seen:
            raise Hold("DUPLICATE_ITEM_ID")
        seen.add(item_id)
        product = _required_str(row, "product_class")
        action = _required_str(row, "action")
        if product not in PRODUCTS or action not in ACTIONS:
            raise Hold("INVALID_PRODUCT_OR_ACTION")
        fields = row.get("fields", {})
        if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
            raise Hold("INVALID_FIELDS")
        digest = _required_str(row, "source_sha256").lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise Hold("INVALID_SHA256")
        items.append(Item(
            item_id=item_id, product_class=product,
            document_number=_required_str(row, "document_number"),
            title=_required_str(row, "title"),
            target_module=_required_str(row, "target_module"),
            source_path=_source(root, _required_str(row, "source_file"), digest),
            source_sha256=digest, fields=fields, action=action,
        ))
    return Batch(case_id=case_id, items=tuple(items))


def check_permission(batch: Batch, item: Item, permit: dict[str, Any], target_origin: str | None) -> None:
    if permit.get("case_id") != batch.case_id:
        raise Hold("WRONG_CASE_APPROVAL")
    approval_id = _required_str(permit, "approval_id")
    if os.getenv("CMS_AGENT_ENABLE_LIVE") != "YES" or os.getenv("CMS_AGENT_APPROVAL_ID") != approval_id:
        raise Hold("LIVE_EXECUTION_NOT_ACKNOWLEDGED")
    try:
        verify_operator_approval(permit)
    except ApprovalError as error:
        raise Hold(str(error)) from error
    actions = permit["item_actions"]
    if actions.get(item.item_id) != item.action:
        raise Hold("ACTION_NOT_APPROVED_EXACT_ITEM")
    if permit["item_fingerprints"].get(item.item_id) != item.fingerprint:
        raise Hold("ITEM_FINGERPRINT_NOT_APPROVED")
    if item.target_module not in permit["allowed_modules"]:
        raise Hold("MODULE_NOT_APPROVED")
    if not isinstance(target_origin, str) or permit["target_origin"] != target_origin:
        raise Hold("CMS_ORIGIN_NOT_APPROVED")
    if item.action == "PUBLISH_STEP1" and item.product_class != "CSDL":
        if item.item_id not in permit["explicit_news_publish_item_ids"]:
            raise Hold("NEWS_PUBLISH_NEEDS_SEPARATE_EXPLICIT_APPROVAL")
    # Verified operator-signed exact item/content/action/module permission.
    # Never treat this as permission for public QA or QLVB Step 2.


def _assert_readback(item: Item, record: RemoteRecord, status: str) -> None:
    if (record.number != item.document_number
            or record.module != item.target_module
            or record.fingerprint != item.fingerprint
            or record.attachment_sha256 != item.source_sha256):
        raise Hold("REMOTE_CONTENT_OR_ATTACHMENT_MISMATCH")
    if record.status != status:
        raise Hold("REMOTE_STATUS_NOT_CONFIRMED")


def execute_item(batch: Batch, item: Item, permit: dict[str, Any], adapter: CMSAdapter) -> dict[str, str]:
    base = {"item_id": item.item_id, "action": item.action}
    try:
        check_permission(batch, item, permit, getattr(adapter, 'origin', None))
        matches = adapter.find(item)
        if len(matches) > 1:
            raise Hold("MULTIPLE_REMOTE_MATCHES")
        if matches:
            record = matches[0]
            # Never overwrite an existing mismatched record.
            _assert_readback(item, adapter.readback(record.record_id), record.status)
            if record.status == "PUBLISHED":
                if item.action == "SAVE_DRAFT":
                    raise Hold("CANNOT_DOWNGRADE_PUBLISHED_RECORD")
                return {**base, "status": "ALREADY_PUBLISHED", "record_id": record.record_id}
            if record.status != "DRAFT":
                raise Hold("UNKNOWN_REMOTE_STATUS")
            if item.action == "SAVE_DRAFT":
                return {**base, "status": "ALREADY_DRAFT", "record_id": record.record_id}
            record_id = record.record_id
        else:
            try:
                record_id = adapter.create_draft(item)
            except Exception:
                return {**base, "status": "NEEDS_RECONCILE", "reason": "CREATE_OUTCOME_UNKNOWN"}
            _assert_readback(item, adapter.readback(record_id), "DRAFT")
            if item.action == "SAVE_DRAFT":
                return {**base, "status": "DRAFT_VERIFIED", "record_id": record_id}
        # Publication authority is exact-scope, and all content must already be verified.
        try:
            adapter.publish_step1(record_id)
        except Exception:
            return {**base, "status": "NEEDS_RECONCILE", "reason": "PUBLISH_OUTCOME_UNKNOWN"}
        _assert_readback(item, adapter.readback(record_id), "PUBLISHED")
        return {**base, "status": "PUBLISHED_STEP1_VERIFIED", "record_id": record_id}
    except Hold as exc:
        return {**base, "status": "HOLD", "reason": str(exc)}
    except Exception:
        return {**base, "status": "NEEDS_RECONCILE", "reason": "REMOTE_READ_OR_STATE_UNKNOWN"}


def dry_run(batch: Batch) -> dict[str, Any]:
    return {
        "case_id": batch.case_id,
        "mode": "DRY_RUN_NO_NETWORK_NO_MUTATION",
        "items": [{"item_id": item.item_id, "product_class": item.product_class,
                   "action": item.action, "target_module": item.target_module,
                   "source_sha256": item.source_sha256, "fingerprint": item.fingerprint}
                  for item in batch.items],
    }
