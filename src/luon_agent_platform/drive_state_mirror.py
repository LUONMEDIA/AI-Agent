"""Read-only Drive/Sheets workflow-state mirror with guarded local checkpoints.

A sheet is the source, the private local file is a verified mirror. This module
never uploads state, modifies Drive, or exposes a public repository as a store.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import quote, urlencode

from .drive_current import BridgeError, DriveReader


STATUS = {"ON_TRACK", "AT_RISK", "BLOCKED", "RECOVERY", "VERIFYING", "DONE"}
REQUIRED = {
    "Workflow_ID", "Objective", "Success_Criteria", "Runtime", "Current_Stage",
    "Status", "Priority", "Completed_PASS", "Next_Actions", "State_Version",
}
JSON_COLUMNS = {
    "Completed_PASS", "Next_Actions", "Blockers", "Approvals_Needed",
    "Current_Artifacts", "Source_Versions", "Locked_Facts",
    "Locked_Decisions", "Open_Issues", "Risks", "Assumptions",
    "Active_Sources", "Invalidated_Steps",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict) or not REQUIRED.issubset(state):
        raise BridgeError("STATE_SCHEMA_INCOMPLETE")
    for key in REQUIRED - {"Completed_PASS", "Next_Actions", "State_Version"}:
        if not isinstance(state[key], str) or not state[key].strip():
            raise BridgeError("STATE_REQUIRED_FIELD_INVALID")
    if state["Status"] not in STATUS:
        raise BridgeError("STATE_STATUS_INVALID")
    if state["Runtime"] not in {"CHAT", "CODEX", "MIXED"}:
        raise BridgeError("STATE_RUNTIME_INVALID")
    if isinstance(state["State_Version"], bool) or not isinstance(state["State_Version"], int) or state["State_Version"] < 1:
        raise BridgeError("STATE_VERSION_INVALID")
    for field in ("Completed_PASS", "Next_Actions"):
        if not isinstance(state[field], list):
            raise BridgeError("STATE_LIST_INVALID")
    for field in JSON_COLUMNS & state.keys():
        if not isinstance(state[field], (dict, list)):
            raise BridgeError("STATE_JSON_INVALID")
    if state["Status"] == "DONE" and (
        state["Next_Actions"] or state.get("Blockers") or state.get("Approvals_Needed")
        or not state.get("Last_Verified")
    ):
        raise BridgeError("UNVERIFIED_DONE")
    _canonical(state)


class DriveWorkflowStateBridge:
    """Import one exact Workflow_ID from an owner-authorized Google Sheet.

    Requires separately supplied OAuth with sheets.readonly + drive.metadata.readonly.
    No remote writes or background polling; identical snapshots are idempotent.
    """

    def __init__(self, reader: DriveReader, spreadsheet_id: str, a1_range: str):
        if not isinstance(spreadsheet_id, str) or len(spreadsheet_id) < 20:
            raise BridgeError("STATE_SHEET_ID_INVALID")
        if (not isinstance(a1_range, str) or "!" not in a1_range
                or len(a1_range) > 256 or any(x in a1_range for x in ("\n", "\r"))):
            raise BridgeError("STATE_RANGE_INVALID")
        self.reader, self.spreadsheet_id, self.a1_range = reader, spreadsheet_id, a1_range

    def _meta(self) -> dict[str, Any]:
        url = (
            "https://www.googleapis.com/drive/v3/files/"
            + quote(self.spreadsheet_id, safe="")
            + "?" + urlencode({"fields": "id,name,mimeType,version,modifiedTime,trashed"})
        )
        try:
            meta = json.loads(self.reader._get(url).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise BridgeError("STATE_SHEET_METADATA_INVALID") from None
        if (not isinstance(meta, dict) or meta.get("id") != self.spreadsheet_id
                or meta.get("mimeType") != "application/vnd.google-apps.spreadsheet"
                or meta.get("trashed") is not False
                or not meta.get("version") or not meta.get("modifiedTime")):
            raise BridgeError("STATE_SHEET_NOT_VERIFIED")
        return meta

    def read_workflow(self, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise BridgeError("WORKFLOW_ID_REQUIRED")
        before = self._meta()
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            + quote(self.spreadsheet_id, safe="")
            + "/values/" + quote(self.a1_range, safe="")
            + "?" + urlencode({"valueRenderOption": "UNFORMATTED_VALUE"})
        )
        try:
            rows = json.loads(self.reader._get(url).decode("utf-8")).get("values", [])
        except (ValueError, AttributeError, UnicodeError):
            raise BridgeError("STATE_SHEET_READ_INVALID") from None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
            raise BridgeError("STATE_SHEET_EMPTY")
        headers = rows[0]
        if (any(not isinstance(item, str) or not item for item in headers)
                or len(headers) != len(set(headers)) or not REQUIRED.issubset(headers)):
            raise BridgeError("STATE_SHEET_COLUMNS_INVALID")
        matches: list[dict[str, Any]] = []
        for row in rows[1:]:
            if not isinstance(row, list):
                raise BridgeError("STATE_ROW_INVALID")
            cells = {name: row[i] if i < len(row) else "" for i, name in enumerate(headers)}
            if cells["Workflow_ID"] != workflow_id:
                continue
            state: dict[str, Any] = {}
            for name, value in cells.items():
                if value == "":
                    continue
                if name in JSON_COLUMNS:
                    if not isinstance(value, str):
                        raise BridgeError("STATE_JSON_CELL_INVALID")
                    try:
                        state[name] = json.loads(value)
                    except ValueError:
                        raise BridgeError("STATE_JSON_CELL_INVALID") from None
                elif name == "State_Version":
                    if type(value) is int:
                        state[name] = value
                    elif isinstance(value, str) and value.isdigit():
                        state[name] = int(value)
                    else:
                        raise BridgeError("STATE_VERSION_INVALID")
                else:
                    state[name] = value
            validate_state(state)
            matches.append(state)
        if len(matches) != 1:
            raise BridgeError("STATE_WORKFLOW_MISSING_OR_DUPLICATE")
        after = self._meta()
        if (after["version"], after["modifiedTime"]) != (before["version"], before["modifiedTime"]):
            raise BridgeError("STATE_SHEET_CHANGED_DURING_READ")
        return {
            "checkpoint": matches[0],
            "source": {
                "spreadsheet_id": self.spreadsheet_id,
                "range": self.a1_range,
                "version": str(before["version"]),
                "modified_time": str(before["modifiedTime"]),
                "row_sha256": hashlib.sha256(_canonical(matches[0])).hexdigest(),
            },
            "readback": "PASS", "write_performed": False,
        }


class LocalStateMirror:
    """Persist verified snapshot only to a private file outside every Git tree."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if any((parent / ".git").exists() for parent in (self.path.parent, *self.path.parents)):
            raise BridgeError("PRIVATE_STATE_INSIDE_REPOSITORY")

    def synchronize(self, source_result: dict[str, Any]) -> dict[str, Any]:
        if source_result.get("readback") != "PASS" or source_result.get("write_performed") is not False:
            raise BridgeError("SOURCE_NOT_VERIFIED")
        state = source_result.get("checkpoint")
        validate_state(state)
        previous: dict[str, Any] | None = None
        if self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))["checkpoint"]
            except (OSError, ValueError, KeyError, TypeError):
                raise BridgeError("PREVIOUS_STATE_UNREADABLE") from None
            if previous["Workflow_ID"] != state["Workflow_ID"]:
                raise BridgeError("MIRROR_WORKFLOW_CONFLICT")
            old = previous["State_Version"]
            new = state["State_Version"]
            if old == new:
                if _canonical(previous) != _canonical(state):
                    raise BridgeError("STATE_SAME_VERSION_DIFFERENT_CONTENT")
                return {"status": "UNCHANGED", "state_version": old, "write_performed": False}
            if new != old + 1:
                raise BridgeError("STATE_VERSION_GAP_OR_ROLLBACK")
            missing = set(previous["Completed_PASS"]) - set(state["Completed_PASS"])
            if missing - set(state.get("Invalidated_Steps", [])):
                raise BridgeError("COMPLETED_WORK_LOST")
        envelope = {
            "checkpoint": state, "source": source_result["source"],
            "mirrored_at": datetime.now(timezone.utc).isoformat(),
            "mirror_type": "READ_ONLY_GOOGLE_SHEET_TO_LOCAL",
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".state-", dir=str(self.path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(_canonical(envelope))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        readback = json.loads(self.path.read_text(encoding="utf-8"))
        if _canonical(readback["checkpoint"]) != _canonical(state):
            raise BridgeError("LOCAL_MIRROR_READBACK_FAILED")
        return {
            "status": "SYNCED_LOCAL_MIRROR",
            "state_version": state["State_Version"],
            "sha256": hashlib.sha256(_canonical(state)).hexdigest(),
            "write_performed": True,
            "drive_write_performed": False,
        }
