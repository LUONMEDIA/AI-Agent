"""Provider-neutral workflow planning and safe local task claiming."""

from __future__ import annotations

from typing import Any

from .registry import RegistryError, validate_registry
from .router import RouteError, route_task
from .state_store import StateStore


class OrchestrationError(ValueError):
    """A workflow cannot be planned or claimed safely."""


class WorkflowOrchestrator:
    """Coordinates state and routing; it does not invoke external agents."""

    def __init__(self, state_store: StateStore, registry: dict[str, Any]):
        try:
            validate_registry(registry)
        except RegistryError as error:
            raise OrchestrationError(str(error)) from error
        self.state_store = state_store
        self.registry = registry

    def plan_dispatch(self, workflow_id: str) -> dict[str, Any]:
        dispatchable = []
        unroutable = []
        for task in self.state_store.list_ready_tasks(workflow_id):
            request = {
                "task_id": task["task_id"],
                "required_capability": task["required_capability"],
                "target_agent": task["target_agent"],
            }
            try:
                route = route_task(request, self.registry)
            except RouteError as error:
                unroutable.append({
                    "task_id": task["task_id"],
                    "reason": str(error),
                    "status_unchanged": True,
                })
                continue
            dispatchable.append({
                "task_id": task["task_id"],
                "agent_id": route["agent_id"],
                "required_capability": route["required_capability"],
                "task_version": task["version"],
            })
        return {
            "workflow_id": workflow_id,
            "dispatchable": dispatchable,
            "unroutable": unroutable,
            "external_execution_performed": False,
        }

    def claim(
        self,
        task_id: str,
        worker_id: str,
        lease_seconds: int = 300,
        expected_version: int | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        task = self.state_store.get_task(task_id)
        request = {
            "task_id": task["task_id"],
            "required_capability": task["required_capability"],
            "target_agent": task["target_agent"],
        }
        try:
            route = route_task(request, self.registry)
        except RouteError as error:
            raise OrchestrationError(str(error)) from error
        return self.state_store.claim_task(
            task_id=task_id,
            agent_id=route["agent_id"],
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            expected_version=expected_version,
            actor=actor,
        )

    def start(self, task_id: str, lease_token: str, actor: str = "worker") -> dict[str, Any]:
        return self.state_store.start_task(task_id, lease_token, actor)

    def finish(
        self,
        task_id: str,
        lease_token: str,
        outcome: str,
        actor: str = "worker",
        reason_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.state_store.finish_task(
            task_id, lease_token, outcome, actor, reason_code, details
        )
