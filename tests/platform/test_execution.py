from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luon_agent_platform import (
    AdapterExecutionError,
    AdapterNotInstalled,
    AdapterRegistryError,
    AdapterResult,
    OrchestrationError,
    StateStore,
    TaskNotDispatchable,
    TaskRunner,
    WorkflowOrchestrator,
)


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


def task_workflow():
    return {
        "schema_version": "1.0",
        "workflow_id": "WF-RUNNER",
        "tasks": [
            {"task_id": "T-1", "task_type": "run", "required_capability": "test.run"},
            {
                "task_id": "T-2",
                "task_type": "follow-up",
                "required_capability": "test.run",
                "depends_on": ["T-1"],
            },
        ],
    }


class SuccessfulAdapter:
    agent_id = "test.worker"

    def execute(self, task):
        self.context = task
        return AdapterResult("succeeded", details={"synthetic": True})


class RaisingAdapter:
    agent_id = "test.worker"

    def execute(self, task):
        raise RuntimeError("do not persist exception message")


class KnownFailureAdapter:
    agent_id = "test.worker"

    def execute(self, task):
        raise AdapterExecutionError(
            "SOURCE_VALIDATION_FAILED",
            outcome="failed",
            details={"check": "synthetic"},
        )


class InvalidResultAdapter:
    agent_id = "test.worker"

    def execute(self, task):
        return {"outcome": "succeeded"}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tempdir.name) / "state.sqlite3")
        self.store.create_workflow(task_workflow())
        self.orchestrator = WorkflowOrchestrator(self.store, active_registry())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_successful_adapter_completes_task_and_releases_dependency(self):
        adapter = SuccessfulAdapter()
        runner = TaskRunner(self.orchestrator, [adapter])
        result = runner.run_task("T-1", "worker-1")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(adapter.context.task_id, "T-1")
        self.assertEqual(self.store.get_task("T-2")["status"], "ready")

    def test_unexpected_exception_requires_reconciliation(self):
        runner = TaskRunner(self.orchestrator, [RaisingAdapter()])
        result = runner.run_task("T-1", "worker-1")
        self.assertEqual(result["status"], "needs_reconciliation")
        events = self.store.get_events("WF-RUNNER")
        event = next(item for item in events if item["event_type"] == "task.needs_reconciliation")
        self.assertEqual(event["details"], {"exception_type": "RuntimeError"})
        self.assertEqual(self.store.get_task("T-2")["status"], "pending")

    def test_known_failure_is_not_misclassified_as_unknown(self):
        runner = TaskRunner(self.orchestrator, [KnownFailureAdapter()])
        result = runner.run_task("T-1", "worker-1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["last_error_code"], "SOURCE_VALIDATION_FAILED")
        self.assertEqual(self.store.get_task("T-2")["status"], "pending")

    def test_invalid_result_envelope_requires_reconciliation(self):
        runner = TaskRunner(self.orchestrator, [InvalidResultAdapter()])
        result = runner.run_task("T-1", "worker-1")
        self.assertEqual(result["status"], "needs_reconciliation")
        self.assertEqual(result["last_error_code"], "INVALID_ADAPTER_RESULT")

    def test_missing_adapter_does_not_claim_task(self):
        runner = TaskRunner(self.orchestrator, [])
        with self.assertRaises(AdapterNotInstalled):
            runner.run_task("T-1", "worker-1")
        self.assertEqual(self.store.get_task("T-1")["status"], "ready")

    def test_unready_task_does_not_claim(self):
        runner = TaskRunner(self.orchestrator, [SuccessfulAdapter()])
        with self.assertRaises(TaskNotDispatchable):
            runner.run_task("T-2", "worker-1")
        self.assertEqual(self.store.get_task("T-2")["status"], "pending")

    def test_disabled_cms_candidate_cannot_be_loaded_as_adapter(self):
        class CmsAdapter:
            agent_id = "cttdt.cms-batch"

            def execute(self, task):
                return AdapterResult("succeeded")

        from luon_agent_platform import load_registry
        root = Path(__file__).resolve().parents[2]
        registry = load_registry(root / "config" / "agents.json")
        with self.assertRaises(AdapterRegistryError):
            TaskRunner(WorkflowOrchestrator(self.store, registry), [CmsAdapter()])

    def test_photo_application_is_not_a_workflow_agent(self):
        class PhotoAdapter:
            agent_id = "luon.photo-batch"

            def execute(self, task):
                return AdapterResult("succeeded")

        from luon_agent_platform import load_registry
        root = Path(__file__).resolve().parents[2]
        registry = load_registry(root / "config" / "agents.json")
        with self.assertRaisesRegex(AdapterRegistryError, "REGISTRATION_IS_NOT_WORKFLOW_AGENT"):
            TaskRunner(WorkflowOrchestrator(self.store, registry), [PhotoAdapter()])

    def test_duplicate_adapter_ids_are_rejected(self):
        with self.assertRaisesRegex(AdapterRegistryError, "DUPLICATE_ADAPTER"):
            TaskRunner(self.orchestrator, [SuccessfulAdapter(), SuccessfulAdapter()])

    def test_adapter_execution_error_requires_reason_code(self):
        with self.assertRaises(ValueError):
            AdapterExecutionError("")

    def test_invalid_adapter_registry_is_rejected(self):
        with self.assertRaises(OrchestrationError):
            WorkflowOrchestrator(self.store, {"schema_version": 1, "agents": "invalid"})


if __name__ == "__main__":
    unittest.main()
