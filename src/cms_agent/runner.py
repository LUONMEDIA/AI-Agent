"""Fail-closed batch coordinator with audit record before and after remote actions."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json
import os
import stat
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from .engine import Batch, CMSAdapter, execute_item, Hold


SYSTEMIC_HOLDS = {
    "WRONG_CASE_APPROVAL", "LIVE_EXECUTION_NOT_ACKNOWLEDGED",
    "TRUSTED_APPROVAL_PUBLIC_KEY_MISSING", "APPROVAL_SIGNATURE_INVALID",
    "CMS_ORIGIN_NOT_APPROVED", "APPROVAL_SCHEMA_FIELDS_MISMATCH",
    "APPROVAL_WRONG_SCHEMA_OR_PURPOSE",
    "APPROVAL_EXPIRED_NOT_YET_VALID_OR_TOO_LONG",
}


class PrivateAudit:
    def __init__(self, path: str | Path):
        file = Path(path)
        if not file.is_absolute():
            raise Hold("AUDIT_PATH_MUST_BE_ABSOLUTE")
        if file.is_symlink():
            raise Hold("AUDIT_SYMLINK_FORBIDDEN")
        self.path = file.resolve()
        if not self.path.parent.is_dir():
            raise Hold("AUDIT_PARENT_MISSING")
        if self.path.is_symlink():
            raise Hold("AUDIT_SYMLINK_FORBIDDEN")
        if (stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077):
            raise Hold("AUDIT_PARENT_PERMISSIONS_TOO_OPEN")
        if self.path.exists() and (stat.S_IMODE(self.path.stat().st_mode) & 0o077):
            raise Hold("AUDIT_FILE_PERMISSIONS_TOO_OPEN")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.path), flags, 0o600)
        os.close(fd)

    def __call__(self, case_id: str, item_id: str, stage: str, status: str, reason: str = "") -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "case_sha256": sha256(case_id.encode()).hexdigest(),
            "item_id": item_id,
            "stage": stage,
            "status": status,
            "reason": reason,
        }
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.path), flags)
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise


def run_batch(
    batch: Batch,
    approval: dict[str, Any],
    adapter: CMSAdapter,
    audit: Callable[[str, str, str, str, str], None],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for item in batch.items:
        audit(batch.case_id, item.item_id, "BEFORE_REMOTE", "START", "")
        result = execute_item(batch, item, approval, adapter)
        audit(batch.case_id, item.item_id, "AFTER_REMOTE", result["status"], result.get("reason", ""))
        results.append(result)
        # STOP on EVERY failure. A Hold can occur after a remote side effect;
        # never advance the batch until a human reconciles current CMS state.
        if result["status"] in {"HOLD", "NEEDS_RECONCILE"}:
            break
    return results



class ExclusiveRunLock(AbstractContextManager):
    """Prevent two CMS workers on the SAME host from writing concurrently.

    Distinct cloud hosts require a server-side uniqueness lock/idempotency API.
    This local lock is explicitly NOT sufficient for production cutover.
    """
    def __init__(self, file: str | Path):
        import fcntl

        self._fcntl = fcntl
        path = Path(file)
        if not path.is_absolute() or not path.parent.is_dir():
            raise Hold("LOCK_PATH_MUST_BE_ABSOLUTE_IN_EXISTING_DIRECTORY")
        if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise Hold("LOCK_PARENT_PERMISSIONS_TOO_OPEN")
        if path.is_symlink():
            raise Hold("LOCK_SYMLINK_FORBIDDEN")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(path, flags, 0o600)
        try:
            if stat.S_IMODE(os.fstat(self.fd).st_mode) & 0o077:
                raise Hold("LOCK_FILE_PERMISSIONS_TOO_OPEN")
            import errno
            try:
                self._fcntl.flock(self.fd, self._fcntl.LOCK_EX | self._fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise Hold("CMS_WORKER_ALREADY_RUNNING_ON_HOST") from error
                raise
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self._fcntl.flock(self.fd, self._fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
        return False
