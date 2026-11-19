"""In-process adapter contract and fail-closed task runner.

Adapters are trusted local code, not sandboxed plugins. The runner never grants
permissions or supplies credentials; specialist adapters enforce their own gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .orchestrator import WorkflowOrchestrator
from .registry import RegistryError, find_agent, validate_registry
from .state_store import StateStore


class AdapterRegistryError(ValueError):
    """An adapter does not match an active workflow-agent registration."""


class AdapterNotInstalled(RuntimeError):
    """The routed workflow agent has no installed adapter implementation."""


class TaskNotDispatchable(RuntimeError):
    """The task is not ready or has no unambiguous active route."""


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    workflow_id: str
    task_type: str
    required_capability: str
    payload: dict[str, Any]
    attempt: int
    worker_id: str


@dataclass(frozen=True)
class AdapterResult:
    outcome: str
    reason_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in {"succeeded", "failed", "blocked", "needs_reconciliation"}:
            raise ValueError(f"INVALID_ADAPTER_OUTCOME:{self.outcome}")
        if self.outcome != "succeeded" and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("ADAPTER_REASON_CODE_REQUIRED")
        if not isinstance(self.details, dict):
            raise ValueError("ADAPTER_DETAILS_MUST_BE_OBJECT")


class AgentAdapter(Protocol):
    """A specialist-owned implementation for one registered workflow agent."""

    agent_id: str

    def execute(self, task: TaskContext) -> AdapterResult:
        """Execute one task and return a verified, explicit outcome."""


class AdapterExecutionError(RuntimeError):
    """A specialist can raise this only when it can classify the outcome."""

    def __init__(
        self,
        reason_code: str,
        outcome: str = "failed",
        details: dict[str, Any] | None = None,
    ):
        if outcome not in {"failed", "blocked", "needs_reconciliation"}:
            raise ValueError(f"INVALID_ADAPTER_ERROR_OUTCOME:{outcome}")
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise ValueError("ADAPTER_REASON_CODE_REQUIRED")
        if details is not None and not isinstance(details, dict):
            raise ValueError("ADAPTER_DETAILS_MUST_BE_OBJECT")
        super().__init__(reason_code)
        self.outcome = outcome
        self.reason_code = reason_code
        self.details = details or {}


class AdapterRegistry:
    """Binds trusted local adapters to active workflow-agent registrations."""

    def __init__(self, registry: dict[str, Any], adapters: Iterable[AgentAdapter]):
        try:
            validate_registry(registry)
        except RegistryError as error:
            raise AdapterRegistryError(str(error)) from error

        self._adapters: dict[str, AgentAdapter] = {}
        for adapter in adapters:
            agent_id = getattr(adapter, "agent_id", None)
            execute = getattr(adapter, "execute", None)
            if not isinstance(agent_id, str) or not agent_id.strip() or not callable(execute):
                raise AdapterRegistryError("INVALID_ADAPTER_IMPLEMENTATION")
            if agent_id in self._adapters:
                raise AdapterRegistryError(f"DUPLICATE_ADAPTER:{agent_id}")
            try:
                registration = find_agent(registry, agent_id)
            except RegistryError as error:
                raise AdapterRegistryError(str(error)) from error
            if registration["kind"] != "workflow_agent":
                raise AdapterRegistryError(f"REGISTRATION_IS_NOT_WORKFLOW_AGENT:{agent_id}")
            if registration["status"] != "active" or not registration["enabled"]:
                raise AdapterRegistryError(f"AGENT_NOT_ACTIVE:{agent_id}")
            self._adapters[agent_id] = adapter

    def get(self, agent_id: str) -> AgentAdapter | None:
        return self._adapters.get(agent_id)


class TaskRunner:
    """Runs one synchronously claimed task through an explicitly injected adapter.

    Unexpected adapter exceptions and invalid result envelopes are classified as
    NEEDS_RECONCILIATION because the runner cannot prove whether a side effect occurred.
    There are no automatic retries.
    """

    def __init__(self, orchestrator: WorkflowOrchestrator, adapters: Iterable[AgentAdapter]):
        self.orchestrator = orchestrator
        self.state_store: StateStore = orchestrator.state_store
        self.adapters = AdapterRegistry(orchestrator.registry, adapters)

    def run_task(
        self,
        task_id: str,
        worker_id: str,
        lease_seconds: int = 300,
        actor: str = "task-runner",
    ) -> dict[str, Any]:
        task = self.state_store.get_task(task_id)
        plan = self.orchestrator.plan_dispatch(task["workflow_id"])
        route = next((item for item in plan["dispatchable"] if item["task_id"] == task_id), None)
        if route is None:
            reason = next(
                (item["reason"] for item in plan["unroutable"] if item["task_id"] == task_id),
                f"TASK_NOT_READY_OR_NOT_IN_DISPATCH_PLAN:{task_id}",
            )
            raise TaskNotDispatchable(reason)

        adapter = self.adapters.get(route["agent_id"])
        if adapter is None:
            raise AdapterNotInstalled(f"ADAPTER_NOT_INSTALLED:{route['agent_id']}")

        claimed = self.orchestrator.claim(
            task_id,
            worker_id,
            lease_seconds=lease_seconds,
            expected_version=task["version"],
            actor=actor,
        )
        self.orchestrator.start(task_id, claimed["lease_token"], actor=worker_id)

        context = TaskContext(
            task_id=task["task_id"],
            workflow_id=task["workflow_id"],
            task_type=task["task_type"],
            required_capability=task["required_capability"],
            payload=task["payload"],
            attempt=claimed["attempt"],
            worker_id=worker_id,
        )

        try:
            result = adapter.execute(context)
        except AdapterExecutionError as error:
            result = AdapterResult(
                outcome=error.outcome,
                reason_code=error.reason_code,
                details=error.details,
            )
        except Exception as error:
            result = AdapterResult(
                outcome="needs_reconciliation",
                reason_code="UNEXPECTED_ADAPTER_EXCEPTION",
                details={"exception_type": type(error).__name__},
            )

        if not isinstance(result, AdapterResult):
            result = AdapterResult(
                outcome="needs_reconciliation",
                reason_code="INVALID_ADAPTER_RESULT",
                details={"returned_type": type(result).__name__},
            )

        return self.orchestrator.finish(
            task_id,
            claimed["lease_token"],
            result.outcome,
            actor=worker_id,
            reason_code=result.reason_code,
            details=result.details,
        )
