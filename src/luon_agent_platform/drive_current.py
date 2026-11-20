"""Read-only Google Drive CURRENT authority bridge.

Never place authenticated configuration, source text, or receipts in this public
repository. This bridge is not a Drive write adapter or a production executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .execution import AdapterResult, TaskContext


MODULE_KEYS = (
    "CORE", "ROUTER", "CHAT", "CODEX", "INCIDENT",
    "STATE/PROGRESS", "EVOLUTION", "QA",
)
LOAD_PROFILES = {
    "CHAT": ("CORE", "ROUTER", "CHAT", "STATE/PROGRESS", "QA"),
    "CODEX": ("CORE", "ROUTER", "CODEX", "STATE/PROGRESS", "QA"),
    "MIXED": MODULE_KEYS,
    "AUDIT": MODULE_KEYS,
}
_GOOGLE_ID = re.compile(r"^[A-Za-z0-9_-]{20,150}$")


class BridgeError(RuntimeError):
    """A source, credential, or authority gate did not pass."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _title_heading(title: str) -> str:
    return re.sub(r"^\d+\s*—\s*", "", title).removesuffix(" — CANONICAL").strip()


def validate_config(config: dict[str, Any]) -> None:
    """Validate private document map; no real Google IDs are bundled in Git."""
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise BridgeError("INVALID_BRIDGE_CONFIG")
    docs = config.get("modules")
    if not isinstance(docs, dict) or set(docs) != set(MODULE_KEYS):
        raise BridgeError("MODULE_MAP_INCOMPLETE")
    entries = [config.get("manifest"), *docs.values()]
    ids = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "title"}:
            raise BridgeError("INVALID_SOURCE_ENTRY")
        if not isinstance(entry["id"], str) or not _GOOGLE_ID.fullmatch(entry["id"]):
            raise BridgeError("INVALID_SOURCE_ID")
        if not isinstance(entry["title"], str) or not entry["title"].strip():
            raise BridgeError("INVALID_SOURCE_TITLE")
        ids.append(entry["id"])
    if len(set(ids)) != len(ids):
        raise BridgeError("DUPLICATE_SOURCE_ID")
    if "ACTIVE MANIFEST" not in config["manifest"]["title"] or "CURRENT ONLY" not in config["manifest"]["title"]:
        raise BridgeError("INVALID_MANIFEST_AUTHORITY")
    for entry in docs.values():
        if not entry["title"].endswith(" — CANONICAL"):
            raise BridgeError("CANDIDATE_OR_LEGACY_SOURCE")


class _NoRedirect(HTTPRedirectHandler):
    """Do not forward OAuth credentials to an unverified redirect target."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise BridgeError("UNEXPECTED_PROVIDER_REDIRECT")


class DriveReader:
    """OAuth2 Drive v3 GET/export only; injected transport enables offline tests.

    Use a private, short-lived, read-only OAuth access token provided by an
    authorized operator. A ChatGPT/Claude/Codex connector token is not shared.
    """

    def __init__(
        self,
        access_token: str,
        transport: Callable[[str, dict[str, str]], bytes] | None = None,
        timeout: int = 15,
    ):
        if not isinstance(access_token, str) or not access_token.strip():
            raise BridgeError("AUTH_NOT_CONFIGURED")
        if any(char in access_token for char in ("\r", "\n")):
            raise BridgeError("INVALID_AUTH_HEADER")
        self._token = access_token
        self._transport = transport
        self._timeout = timeout

    def _get(self, url: str) -> bytes:
        address = urlsplit(url)
        if address.scheme != "https" or address.hostname not in {
            "www.googleapis.com", "sheets.googleapis.com"
        }:
            raise BridgeError("UNEXPECTED_API_ENDPOINT")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json, text/plain"}
        try:
            if self._transport is not None:
                result = self._transport(url, headers)
            else:
                request = Request(url, headers=headers, method="GET")
                with build_opener(_NoRedirect()).open(request, timeout=self._timeout) as response:
                    result = response.read(2_000_001)
            if not isinstance(result, bytes) or len(result) > 2_000_000:
                raise BridgeError("SOURCE_TOO_LARGE")
            return result
        except BridgeError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            # Never leak HTTP body, access token, or provider URL into logs.
            raise BridgeError("SOURCE_UNAVAILABLE") from None

    def metadata(self, file_id: str) -> dict[str, Any]:
        url = (
            "https://www.googleapis.com/drive/v3/files/"
            + quote(file_id, safe="")
            + "?" + urlencode({"fields": "id,name,mimeType,modifiedTime,version,trashed"})
        )
        try:
            item = json.loads(self._get(url).decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise BridgeError("INVALID_DRIVE_METADATA") from None
        if not isinstance(item, dict) or item.get("id") != file_id:
            raise BridgeError("SOURCE_ID_MISMATCH")
        if item.get("trashed") is not False or item.get("mimeType") != "application/vnd.google-apps.document":
            raise BridgeError("SOURCE_NOT_CURRENT_DOC")
        if not item.get("version") or not item.get("modifiedTime"):
            raise BridgeError("SOURCE_VERSION_UNKNOWN")
        return item

    def document(self, file_id: str, expected_title: str) -> tuple[dict[str, Any], str]:
        meta = self.metadata(file_id)
        if meta.get("name") != expected_title:
            raise BridgeError("SOURCE_TITLE_DRIFT")
        url = (
            "https://www.googleapis.com/drive/v3/files/"
            + quote(file_id, safe="")
            + "/export?" + urlencode({"mimeType": "text/plain"})
        )
        try:
            content = self._get(url).decode("utf-8-sig").replace("\r\n", "\n").strip()
        except UnicodeError:
            raise BridgeError("SOURCE_TEXT_INVALID") from None
        if not content:
            raise BridgeError("SOURCE_EMPTY")
        after = self.metadata(file_id)
        if (str(after["version"]), after["modifiedTime"]) != (str(meta["version"]), meta["modifiedTime"]):
            raise BridgeError("SOURCE_CHANGED_DURING_READ")
        return meta, content


def _manifest_lines(text: str) -> dict[str, str]:
    if not text.startswith("ACTIVE MANIFEST V2.0 — CURRENT ONLY"):
        raise BridgeError("MANIFEST_NOT_CURRENT")
    for marker in ("CURRENT CORE", "CURRENT EXECUTION RUNTIMES", "CURRENT SUPPORT MODULES", "CURRENT-ONLY RULE"):
        if marker not in text:
            raise BridgeError("MANIFEST_INCOMPLETE")
    lines: dict[str, str] = {}
    for key in MODULE_KEYS:
        matches = re.findall(r"^" + re.escape(key) + r":\s*(.+?)\s*$", text, re.MULTILINE)
        if len(matches) != 1:
            raise BridgeError("MANIFEST_ROUTE_MISSING_OR_DUPLICATE")
        name = matches[0].strip()
        if not name.endswith(" — CANONICAL."):
            raise BridgeError("MANIFEST_ROUTE_NOT_CANONICAL")
        lines[key] = name[:-1]
    return lines


@dataclass(frozen=True)
class SourceReceipt:
    role: str
    file_id: str
    revision: str
    modified_time: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role, "file_id": self.file_id, "revision": self.revision,
            "modified_time": self.modified_time, "sha256": self.sha256,
        }


class DriveCurrentBridge:
    """Resolve canonical docs without changing or activating any runtime."""

    def __init__(self, reader: DriveReader, config: dict[str, Any]):
        validate_config(config)
        self.reader = reader
        self.config = config

    def resolve(self, runtime: str = "CODEX") -> dict[str, Any]:
        if runtime not in LOAD_PROFILES:
            raise BridgeError("UNSUPPORTED_RUNTIME")
        root = self.config["manifest"]
        meta, text = self.reader.document(root["id"], root["title"])
        authority = _manifest_lines(text)
        receipts = [self._receipt("MANIFEST", root["id"], meta, text)]
        for key in LOAD_PROFILES[runtime]:
            spec = self.config["modules"][key]
            expected_title_without_number = re.sub(r"^\d+\s*—\s*", "", spec["title"])
            if authority[key] != expected_title_without_number:
                raise BridgeError("MANIFEST_MODULE_CONFLICT")
            source_meta, content = self.reader.document(spec["id"], spec["title"])
            if content.splitlines()[0].lstrip("\ufeff").strip() != _title_heading(spec["title"]):
                raise BridgeError("SOURCE_HEADING_DRIFT")
            receipts.append(self._receipt(key, spec["id"], source_meta, content))
        # Detect manifest races: authority cannot change while modules are read.
        final_meta = self.reader.metadata(root["id"])
        if str(final_meta["version"]) != str(meta["version"]) or final_meta["modifiedTime"] != meta["modifiedTime"]:
            raise BridgeError("MANIFEST_CHANGED_DURING_READ")
        return {
            "authority": "GOOGLE_DRIVE_CURRENT",
            "mode": "LIVE_READ_ONLY",
            "runtime": runtime,
            "readback": "PASS",
            "write_performed": False,
            "execution_authorized": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "sources": [item.to_dict() for item in receipts],
        }

    @staticmethod
    def _receipt(role: str, file_id: str, meta: dict[str, Any], content: str) -> SourceReceipt:
        return SourceReceipt(
            role, file_id, str(meta["version"]), str(meta["modifiedTime"]), _digest(content)
        )


class DriveCurrentReadAdapter:
    """Opt-in workflow-agent adapter: read-only, unregistered/disabled by default."""

    agent_id = "master.drive-current-read"

    def __init__(self, bridge: DriveCurrentBridge):
        self.bridge = bridge

    def execute(self, task: TaskContext) -> AdapterResult:
        if (task.required_capability != "master.authority.read"
                or task.task_type != "authority.read"
                or not isinstance(task.payload, dict)
                or set(task.payload) != {"runtime"}):
            return AdapterResult("blocked", "UNAUTHORIZED_TASK_SHAPE")
        try:
            receipt = self.bridge.resolve(task.payload["runtime"])
        except BridgeError as error:
            return AdapterResult("blocked", error.code)
        return AdapterResult("succeeded", details={"authority_receipt": receipt})
