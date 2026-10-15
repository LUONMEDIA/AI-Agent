"""Verify offline Ed25519 operator approvals (executor only has PUBLIC key).

Signer private keys MUST be held outside this repository, the Codex Cloud
environment, GitHub Actions and the runtime host. The public key is trusted
configuration, not obtained from the submitted approval itself.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class ApprovalError(ValueError):
    pass


APPROVAL_KEYS = frozenset({
    "schema_version", "purpose", "case_id", "approval_id", "operator_id",
    "issued_at", "expires_at", "item_actions", "item_fingerprints",
    "allowed_modules", "explicit_news_publish_item_ids", "target_origin", "signature",
})


def canonical_bytes(data: dict[str, Any]) -> bytes:
    """Canonical UTF-8 JSON, excluding the detached signature field."""
    return json.dumps({k: v for k, v in data.items() if k != "signature"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ApprovalError("APPROVAL_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalError("APPROVAL_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise ApprovalError("APPROVAL_TIME_NO_TZ")
    return parsed.astimezone(timezone.utc)


def verify_operator_approval(approval: dict[str, Any]) -> None:
    if not isinstance(approval, dict) or set(approval) != APPROVAL_KEYS:
        raise ApprovalError("APPROVAL_SCHEMA_FIELDS_MISMATCH")
    if approval.get("schema_version") != 2 or approval.get("purpose") != "CMS_STEP1":
        raise ApprovalError("APPROVAL_WRONG_SCHEMA_OR_PURPOSE")
    for key in ("approval_id", "case_id", "operator_id"):
        if not isinstance(approval.get(key), str) or not approval[key].strip():
            raise ApprovalError("APPROVAL_MISSING_" + key.upper())
    for key in ("item_actions", "item_fingerprints"):
        if not isinstance(approval.get(key), dict) or not approval[key]:
            raise ApprovalError("APPROVAL_INVALID_" + key.upper())
    if not isinstance(approval.get("allowed_modules"), list) or not approval["allowed_modules"]:
        raise ApprovalError("APPROVAL_INVALID_MODULES")
    if not isinstance(approval.get("explicit_news_publish_item_ids"), list):
        raise ApprovalError("APPROVAL_INVALID_NEWS_LIST")
    origin = approval.get("target_origin")
    if not isinstance(origin, str) or not origin or origin != origin.rstrip("/"):
        raise ApprovalError("APPROVAL_ORIGIN_INVALID")
    u = urlparse(origin)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        raise ApprovalError("APPROVAL_ORIGIN_INVALID")
    now = datetime.now(timezone.utc)
    issued, expires = _time(approval.get("issued_at")), _time(approval.get("expires_at"))
    if issued > now or expires <= now or expires - issued > timedelta(hours=8):
        raise ApprovalError("APPROVAL_EXPIRED_NOT_YET_VALID_OR_TOO_LONG")
    encoded_key = os.getenv("CMS_AGENT_APPROVAL_PUBLIC_KEY_B64", "")
    if not encoded_key:
        raise ApprovalError("TRUSTED_APPROVAL_PUBLIC_KEY_MISSING")
    try:
        pub_bytes = base64.b64decode(encoded_key, validate=True)
        sig_bytes = base64.b64decode(approval["signature"], validate=True)
        if len(pub_bytes) != 32 or len(sig_bytes) != 64:
            raise ValueError("invalid signature/key length")
        public = Ed25519PublicKey.from_public_bytes(pub_bytes)
        public.verify(sig_bytes, canonical_bytes(approval))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ApprovalError("APPROVAL_SIGNATURE_INVALID") from exc
    # This verifies provenance and integrity, not the CMS account's permissions.
    # Require a separately reviewed trusted approver/role process in production.
