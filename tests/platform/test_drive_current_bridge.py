"""Offline synthetic cross-system regression for the Drive CURRENT bridge."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import unquote, urlsplit

from luon_agent_platform.drive_current import (
    BridgeError, DriveCurrentBridge, DriveCurrentReadAdapter, DriveReader,
    MODULE_KEYS,
)
from luon_agent_platform.drive_state_mirror import (
    DriveWorkflowStateBridge, LocalStateMirror, validate_state,
)
from luon_agent_platform.execution import TaskRunner
from luon_agent_platform.orchestrator import WorkflowOrchestrator
from luon_agent_platform.state_store import StateStore


def config_fixture():
    manifest = {"id": "SYNTHETIC_MANIFEST_00000000000001", "title": "01 — ACTIVE MANIFEST V2.0 — CURRENT ONLY"}
    modules = {}
    for n, key in enumerate(MODULE_KEYS):
        modules[key] = {
            "id": "SYNTHETIC_SOURCE_" + str(n).zfill(2) + "_000000000001",
            "title": "01 — " + key + " TEST V2.0 — CANONICAL",
        }
    return {"schema_version": 1, "manifest": manifest, "modules": modules}


def manifest_fixture(config):
    lines = [
        "ACTIVE MANIFEST V2.0 — CURRENT ONLY",
        "CURRENT CORE", "CURRENT EXECUTION RUNTIMES",
        "CURRENT SUPPORT MODULES", "CURRENT-ONLY RULE",
    ]
    for key, spec in config["modules"].items():
        lines.append(key + ": " + spec["title"][5:] + ".")
    return "\n".join(lines)


def sample_checkpoint(version=1, completed=None, status="ON_TRACK"):
    return {
        "Workflow_ID": "WF-SYNTHETIC",
        "Objective": "Validate read-only authority",
        "Success_Criteria": "Readback matches source",
        "Runtime": "MIXED",
        "Current_Stage": "VERIFYING",
        "Status": status,
        "Priority": "P1",
        "Completed_PASS": ["source_verified"] if completed is None else completed,
        "Next_Actions": ["review"],
        "State_Version": version,
    }


class FakeGoogle:
    def __init__(self, config):
        self.config = config
        self.by_id = {e["id"]: e for e in [config["manifest"], *config["modules"].values()]}
        self.content = {
            config["manifest"]["id"]: manifest_fixture(config),
        }
        for key, spec in config["modules"].items():
            self.content[spec["id"]] = key + " TEST V2.0\nSOURCE RULE TEST"
        self.version = 1
        self.metadata_titles = {}
        self.calls = []
        self.sheet_id = "SYNTHETIC_SPREADSHEET_000000000001"
        self.sheet_values = []

    def __call__(self, url, headers):
        self.calls.append(url)
        assert headers["Authorization"] == "Bearer synthetic-token"
        parsed = urlsplit(url)
        segments = parsed.path.split("/")
        file_id = unquote(segments[-1])
        if parsed.hostname == "sheets.googleapis.com":
            return json.dumps({"values": self.sheet_values}).encode()
        if segments[-1] == "export":
            file_id = unquote(segments[-2])
            return self.content[file_id].encode()
        if file_id == self.sheet_id:
            return json.dumps({
                "id": file_id, "name": "Synthetic State",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-11-20T00:00:00Z",
                "version": str(self.version), "trashed": False,
            }).encode()
        spec = self.by_id[file_id]
        return json.dumps({
            "id": file_id, "name": self.metadata_titles.get(file_id, spec["title"]),
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-11-20T00:00:00Z",
            "version": str(self.version), "trashed": False,
        }).encode()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = config_fixture()
        self.fake = FakeGoogle(self.config)
        self.reader = DriveReader("synthetic-token", self.fake)
        self.bridge = DriveCurrentBridge(self.reader, self.config)

    def test_audit_verifies_manifest_all_modules_and_rechecks_version(self):
        result = self.bridge.resolve("AUDIT")
        self.assertEqual(result["readback"], "PASS")
        self.assertEqual(len(result["sources"]), 9)
        self.assertFalse(result["write_performed"])
        self.assertTrue(all(x.startswith("https://www.googleapis.com/drive/v3/files/") for x in self.fake.calls))

    def test_codex_targeted_loading(self):
        result = self.bridge.resolve("CODEX")
        self.assertEqual(
            [x["role"] for x in result["sources"]],
            ["MANIFEST", "CORE", "ROUTER", "CODEX", "STATE/PROGRESS", "QA"],
        )

    def test_manifest_drift_blocks_before_module_loading(self):
        self.fake.content[self.config["manifest"]["id"]] = "ACTIVE MANIFEST V1.0 — CURRENT ONLY"
        with self.assertRaisesRegex(BridgeError, "MANIFEST_NOT_CURRENT"):
            self.bridge.resolve()

    def test_candidate_and_wrong_title_block(self):
        spec = self.config["modules"]["CORE"]
        self.config["modules"]["CORE"]["title"] = "01 — CORE TEST V2.0 — CANDIDATE"
        with self.assertRaisesRegex(BridgeError, "CANDIDATE_OR_LEGACY_SOURCE"):
            DriveCurrentBridge(self.reader, self.config)
        spec["title"] = "01 — CORE TEST V2.0 — CANONICAL"
        self.fake.metadata_titles[spec["id"]] = "01 — CORE MODIFIED V3.0 — CANONICAL"
        with self.assertRaisesRegex(BridgeError, "SOURCE_TITLE_DRIFT"):
            self.bridge.resolve()

    def test_missing_or_duplicate_route_blocks(self):
        mid = self.config["manifest"]["id"]
        self.fake.content[mid] = manifest_fixture(self.config).replace("CORE: ", "UNKNOWN: ")
        with self.assertRaisesRegex(BridgeError, "MANIFEST_ROUTE_MISSING_OR_DUPLICATE"):
            self.bridge.resolve()

    def test_source_revision_change_during_export_blocks(self):
        original = self.fake

        def racing_transport(url, headers):
            output = original(url, headers)
            if "/export?" in url:
                original.version += 1
            return output

        bridge = DriveCurrentBridge(DriveReader("synthetic-token", racing_transport), self.config)
        with self.assertRaisesRegex(BridgeError, "SOURCE_CHANGED_DURING_READ"):
            bridge.resolve("CODEX")

    def test_rejects_non_google_api_endpoint(self):
        with self.assertRaisesRegex(BridgeError, "UNEXPECTED_API_ENDPOINT"):
            self.reader._get("https://example.com/drive/v3/files/anything")

    def test_missing_authentication_blocks(self):
        with self.assertRaisesRegex(BridgeError, "AUTH_NOT_CONFIGURED"):
            DriveReader("")

    def test_adapter_executes_read_only_under_synthetic_active_registry(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "workflow.sqlite3")
            definition = {
                "schema_version": "1.0", "workflow_id": "WF-READ",
                "tasks": [
                    {"task_id": "AUTH", "task_type": "authority.read",
                     "required_capability": "master.authority.read", "payload": {"runtime": "CODEX"}},
                    {"task_id": "NEXT", "task_type": "noop",
                     "required_capability": "master.authority.read", "depends_on": ["AUTH"]},
                ],
            }
            store.create_workflow(definition)
            registry = {"schema_version": 1, "agents": [{
                "agent_id": "master.drive-current-read", "display_name": "Synthetic Authority Reader",
                "kind": "workflow_agent", "version": "0.1", "status": "active",
                "enabled": True, "capabilities": ["master.authority.read"],
                "execution_boundary": {"mode": "read_only", "production_mutation_allowed": False},
            }]}
            runner = TaskRunner(WorkflowOrchestrator(store, registry), [DriveCurrentReadAdapter(self.bridge)])
            result = runner.run_task("AUTH", "synthetic-worker")
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(store.get_task("NEXT")["status"], "ready")
            result2 = runner.run_task("NEXT", "synthetic-worker")
            self.assertEqual(result2["status"], "blocked")
            self.assertEqual(result2["last_error_code"], "UNAUTHORIZED_TASK_SHAPE")

    def test_real_registry_keeps_drive_adapter_disabled(self):
        from luon_agent_platform.execution import AdapterRegistry, AdapterRegistryError
        from luon_agent_platform.registry import load_registry
        repo_root = Path(__file__).resolve().parents[2]
        registry = load_registry(repo_root / "config" / "agents.json")
        with self.assertRaises(AdapterRegistryError):
            AdapterRegistry(registry, [DriveCurrentReadAdapter(self.bridge)])


class StateMirrorTests(unittest.TestCase):
    def setUp(self):
        self.config = config_fixture()
        self.fake = FakeGoogle(self.config)
        self.reader = DriveReader("synthetic-token", self.fake)
        self.headers = list(sample_checkpoint())
        self.fake.sheet_values = [self.headers, self.to_row(sample_checkpoint())]

    def to_row(self, checkpoint):
        return [
            json.dumps(checkpoint[key]) if isinstance(checkpoint[key], (list, dict))
            else checkpoint[key]
            for key in self.headers
        ]

    def get_source(self):
        return DriveWorkflowStateBridge(self.reader, self.fake.sheet_id, "State!A1:J").read_workflow("WF-SYNTHETIC")

    def test_sheet_to_private_mirror_idempotent(self):
        source = self.get_source()
        with TemporaryDirectory() as tmp:
            mirror = LocalStateMirror(Path(tmp) / "private" / "state.json")
            result = mirror.synchronize(source)
            self.assertEqual(result["status"], "SYNCED_LOCAL_MIRROR")
            self.assertFalse(result["drive_write_performed"])
            self.assertEqual(mirror.synchronize(source)["status"], "UNCHANGED")

    def test_version_and_completed_steps_are_preserved(self):
        with TemporaryDirectory() as tmp:
            mirror = LocalStateMirror(Path(tmp) / "state.json")
            mirror.synchronize(self.get_source())
            changed = sample_checkpoint(version=2, completed=[])
            self.fake.sheet_values[1] = self.to_row(changed)
            with self.assertRaisesRegex(BridgeError, "COMPLETED_WORK_LOST"):
                mirror.synchronize(self.get_source())
            changed["Invalidated_Steps"] = ["source_verified"]
            self.headers.append("Invalidated_Steps")
            self.fake.sheet_values = [self.headers, self.to_row(changed)]
            self.assertEqual(mirror.synchronize(self.get_source())["state_version"], 2)

    def test_duplicate_workflow_and_unverified_done_block(self):
        self.fake.sheet_values.append(self.to_row(sample_checkpoint()))
        with self.assertRaisesRegex(BridgeError, "STATE_WORKFLOW_MISSING_OR_DUPLICATE"):
            self.get_source()
        sample = sample_checkpoint(status="DONE")
        with self.assertRaisesRegex(BridgeError, "UNVERIFIED_DONE"):
            validate_state(sample)

    def test_missing_state_column_and_malformed_json_block(self):
        self.fake.sheet_values[0] = self.headers[1:]
        with self.assertRaisesRegex(BridgeError, "STATE_SHEET_COLUMNS_INVALID"):
            self.get_source()
        self.fake.sheet_values[0] = self.headers
        self.fake.sheet_values[1][self.headers.index("Completed_PASS")] = "not-json"
        with self.assertRaisesRegex(BridgeError, "STATE_JSON_CELL_INVALID"):
            self.get_source()


if __name__ == "__main__":
    unittest.main()
