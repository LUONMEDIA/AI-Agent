"""Deterministic capability router. It never selects an AI provider or grants authority."""

from __future__ import annotations

from typing import Any

from .registry import RegistryError, find_agent, validate_registry


class RouteError(ValueError):
    """A task cannot be routed unambiguously and safely."""


def route_task(task: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise RouteError("TASK_NOT_OBJECT")
    task_id = task.get("task_id")
    capability = task.get("required_capability")
    if not isinstance(task_id, str) or not task_id.strip():
        raise RouteError("TASK_ID_REQUIRED")
    if not isinstance(capability, str) or not capability.strip():
        raise RouteError("REQUIRED_CAPABILITY_REQUIRED")

    try:
        validate_registry(registry)
        target_agent = task.get("target_agent")
        if target_agent:
            agent = find_agent(registry, target_agent)
            candidates = [agent]
        else:
            candidates = [
                agent for agent in registry["agents"]
                if capability in agent["capabilities"]
            ]
    except RegistryError as error:
        raise RouteError(str(error)) from error

    enabled = [
        agent for agent in candidates
        if agent["status"] == "active" and agent["enabled"]
    ]
    if not enabled:
        if candidates:
            raise RouteError("MATCHING_AGENT_DISABLED_OR_NOT_ACTIVE")
        raise RouteError(f"NO_AGENT_FOR_CAPABILITY:{capability}")

    if len(enabled) != 1:
        raise RouteError(f"AMBIGUOUS_AGENT_ROUTE:{capability}")

    selected = enabled[0]
    if capability not in selected["capabilities"]:
        raise RouteError("TARGET_AGENT_LACKS_REQUIRED_CAPABILITY")

    return {
        "task_id": task_id,
        "agent_id": selected["agent_id"],
        "required_capability": capability,
        "routing_policy": "explicit-capability-single-match",
    }
