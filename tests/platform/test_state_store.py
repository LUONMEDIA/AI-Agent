from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from luon_agent_platform import (
    InvalidTransition,
    LeaseExpired,
    StateStore,
    VersionConflict,
    WorkflowConflict,
)


def definition():
    return {
        "schema_version": "1.0",
        "workflow_id": "WF-1",
        "tasks": [
            {
                "task_id": "T-1",
                "task_type": "prepare",
                "required_capability": "test.prepare",
                "payload": {"source": "synthetic"},
                "idempotency_key": "prepare-1",
            },
            {
                "task_id": "T-2",
                "task_type": "review",
                "required_capability": "test.review",
                "payload": {},
                "depends_on": ["T-1"],
            },
        ],
    }


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "state.sqlite3"
        self.store = StateStore(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_workflow_and_initial_readiness(self):
        workflow = self.store.create_workflow(definition(), actor="test")
        self.assertEqual(workflow["status"], "running")
        tasks = {task["task_id"]: task for task in workflow["tasks"]}
        self.assertEqual(tasks["T-1"]["status"], "ready")
        self.assertEqual(tasks["T-2"]["status"], "pending")
        self.assertEqual([item["task_id"] for item in self.store.list_ready_tasks("WF-1")], ["T-1"])

    def test_identical_workflow_creation_is_idempotent(self):
        first = self.store.create_workflow(definition())
        second = self.store.create_workflow(definition())
        self.assertEqual(first["spec_sha256"], second["spec_sha256"])
        self.assertEqual(len(self.store.get_events("WF-1")), len(self.store.get_events("WF-1")))

    def test_workflow_id_reuse_with_changed_spec_is_rejected(self):
        self.store.create_workflow(definition())
        changed = definition()
        changed["tasks"][0]["payload"]["source"] = "different"
        with self.assertRaises(WorkflowConflict):
            self.store.create_workflow(changed)

    def test_dependency_released_only_after_success(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1")
        self.store.start_task("T-1", claimed["lease_token"])
        self.store.finish_task("T-1", claimed["lease_token"], "succeeded")
        self.assertEqual(self.store.get_task("T-2")["status"], "ready")

    def test_task_claim_requires_ready_state(self):
        self.store.create_workflow(definition())
        with self.assertRaises(InvalidTransition):
            self.store.claim_task("T-2", "test.agent", "worker-1")

    def test_stale_version_is_rejected(self):
        self.store.create_workflow(definition())
        task = self.store.get_task("T-1")
        self.store.claim_task("T-1", "test.agent", "worker-1", expected_version=task["version"])
        with self.assertRaises(VersionConflict):
            self.store.claim_task("T-1", "test.agent", "worker-2", expected_version=task["version"])

    def test_invalid_lease_token_is_rejected(self):
        self.store.create_workflow(definition())
        self.store.claim_task("T-1", "test.agent", "worker-1")
        with self.assertRaises(LeaseExpired):
            self.store.start_task("T-1", "not-the-token")

    def test_expired_lease_moves_to_reconciliation_not_ready(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1", lease_seconds=30)
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        affected = self.store.reconcile_expired_leases(now=future)
        self.assertEqual(affected, ["T-1"])
        self.assertEqual(self.store.get_task("T-1")["status"], "needs_reconciliation")
        self.assertEqual(self.store.list_ready_tasks("WF-1"), [])
        with self.assertRaises(LeaseExpired):
            self.store.start_task("T-1", claimed["lease_token"])

    def test_reconciliation_requires_evidence_and_explicit_resolution(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1")
        self.store.start_task("T-1", claimed["lease_token"])
        self.store.finish_task(
            "T-1", claimed["lease_token"], "needs_reconciliation",
            reason_code="REMOTE_WRITE_OUTCOME_UNKNOWN",
        )
        with self.assertRaises(ValueError):
            self.store.resolve_reconciliation("T-1", "ready", "operator", "")
        resolved = self.store.resolve_reconciliation(
            "T-1", "ready", "operator", "evidence://verified-no-write"
        )
        self.assertEqual(resolved["status"], "ready")

    def test_reconciliation_success_releases_dependents(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1")
        self.store.start_task("T-1", claimed["lease_token"])
        self.store.finish_task(
            "T-1", claimed["lease_token"], "needs_reconciliation",
            reason_code="UNKNOWN_OUTCOME",
        )
        self.store.resolve_reconciliation(
            "T-1", "succeeded", "operator", "evidence://remote-readback"
        )
        self.assertEqual(self.store.get_task("T-2")["status"], "ready")

    def test_failed_task_does_not_release_dependents(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1")
        self.store.start_task("T-1", claimed["lease_token"])
        self.store.finish_task(
            "T-1", claimed["lease_token"], "failed", reason_code="VALIDATED_FAILURE"
        )
        self.assertEqual(self.store.get_task("T-2")["status"], "pending")
        self.assertEqual(self.store.get_workflow("WF-1")["status"], "failed")

    def test_blocked_task_requires_evidence_to_release(self):
        self.store.create_workflow(definition())
        claimed = self.store.claim_task("T-1", "test.agent", "worker-1")
        self.store.start_task("T-1", claimed["lease_token"])
        self.store.finish_task(
            "T-1", claimed["lease_token"], "blocked", reason_code="APPROVAL_REQUIRED"
        )
        with self.assertRaises(ValueError):
            self.store.release_blocked_task("T-1", "operator", "")
        self.store.release_blocked_task("T-1", "operator", "evidence://approval-verified")
        self.assertEqual(self.store.get_task("T-1")["status"], "ready")

    def test_event_chain_verifies(self):
        self.store.create_workflow(definition())
        self.assertTrue(self.store.verify_event_chain()["valid"])
        self.assertGreater(self.store.verify_event_chain()["events_checked"], 0)

    def test_event_chain_detects_tampering(self):
        self.store.create_workflow(definition())
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE events SET details_json='{}' WHERE sequence=1")
            connection.commit()
        finally:
            connection.close()
        result = self.store.verify_event_chain()
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_sequence"], 1)

    def test_concurrent_claims_allow_only_one_worker(self):
        self.store.create_workflow(definition())

        def claim(worker):
            try:
                return self.store.claim_task("T-1", "test.agent", worker)["lease_owner"]
            except InvalidTransition:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, ["worker-a", "worker-b"]))
        self.assertEqual(sum(value != "rejected" for value in outcomes), 1)

    def test_workflow_cycle_is_rejected(self):
        cyclic = {
            "schema_version": "1.0",
            "workflow_id": "WF-CYCLE",
            "tasks": [
                {"task_id": "A", "task_type": "a", "required_capability": "a", "depends_on": ["B"]},
                {"task_id": "B", "task_type": "b", "required_capability": "b", "depends_on": ["A"]},
            ],
        }
        with self.assertRaisesRegex(ValueError, "WORKFLOW_CYCLE_DETECTED"):
            self.store.create_workflow(cyclic)

    def test_unknown_dependency_is_rejected(self):
        invalid = {
            "schema_version": "1.0",
            "workflow_id": "WF-BAD",
            "tasks": [
                {"task_id": "A", "task_type": "a", "required_capability": "a", "depends_on": ["MISSING"]}
            ],
        }
        with self.assertRaisesRegex(ValueError, "UNKNOWN_DEPENDENCY"):
            self.store.create_workflow(invalid)

    def test_database_survives_store_reopen(self):
        self.store.create_workflow(definition())
        reopened = StateStore(self.database)
        self.assertEqual(reopened.get_workflow("WF-1")["tasks"][0]["task_id"], "T-1")


if __name__ == "__main__":
    unittest.main()
