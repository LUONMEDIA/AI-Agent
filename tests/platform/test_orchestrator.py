from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luon_agent_platform import OrchestrationError, StateStore, WorkflowOrchestrator, load_registry


def workflow():
    return {
        "schema_version": "1.0",
        "workflow_id": "WF-ORCH",
        "tasks": [
            {"task_id": "CMS-1", "task_type": "prepare", "required_capability": "cms.document.prepare"},
            {"task_id": "PHOTO-1", "task_type": "edit", "required_capability": "photo.batch.edit"},
        ],
    }


def active_registry():
    return {
        "schema_version": 1,
        "agents": [
            {
                "agent_id": "test.worker",
                "display_name": "Test Worker",
                "kind": "workflow_agent",
                "version": "1.0.0",
                "status": "active",
                "enabled": True,
                "capabilities": ["test.run"],
                "execution_boundary": {"mode": "read_only", "production_mutation_allowed": False},
            }
        ],
    }


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tempdir.name) / "state.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_plan_does_not_dispatch_to_disabled_specialists(self):
        root = Path(__file__).resolve().parents[2]
        registry = load_registry(root / "config" / "agents.json")
        self.store.create_workflow(workflow())
        orchestrator = WorkflowOrchestrator(self.store, registry)
        plan = orchestrator.plan_dispatch("WF-ORCH")
        self.assertEqual(plan["dispatchable"], [])
        self.assertEqual(len(plan["unroutable"]), 2)
        self.assertTrue(all(item["status_unchanged"] for item in plan["unroutable"]))
        self.assertFalse(plan["external_execution_performed"])
        self.assertEqual(self.store.get_task("CMS-1")["status"], "ready")

    def test_claim_routes_only_registered_active_capability(self):
        definition = {
            "schema_version": "1.0",
            "workflow_id": "WF-ACTIVE",
            "tasks": [{"task_id": "T", "task_type": "run", "required_capability": "test.run"}],
        }
        self.store.create_workflow(definition)
        orchestrator = WorkflowOrchestrator(self.store, active_registry())
        claimed = orchestrator.claim("T", "worker-1")
        self.assertEqual(claimed["assigned_agent_id"], "test.worker")
        self.assertTrue(claimed["lease_token"])

    def test_unregistered_capability_cannot_be_claimed(self):
        definition = {
            "schema_version": "1.0",
            "workflow_id": "WF-UNKNOWN",
            "tasks": [{"task_id": "T", "task_type": "run", "required_capability": "unknown.run"}],
        }
        self.store.create_workflow(definition)
        orchestrator = WorkflowOrchestrator(self.store, active_registry())
        with self.assertRaises(OrchestrationError):
            orchestrator.claim("T", "worker-1")
        self.assertEqual(self.store.get_task("T")["status"], "ready")

    def test_orchestrated_execution_lifecycle(self):
        definition = {
            "schema_version": "1.0",
            "workflow_id": "WF-LIFECYCLE",
            "tasks": [{"task_id": "T", "task_type": "run", "required_capability": "test.run"}],
        }
        self.store.create_workflow(definition)
        orchestrator = WorkflowOrchestrator(self.store, active_registry())
        claimed = orchestrator.claim("T", "worker-1")
        orchestrator.start("T", claimed["lease_token"])
        finished = orchestrator.finish("T", claimed["lease_token"], "succeeded")
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(self.store.get_workflow("WF-LIFECYCLE")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
