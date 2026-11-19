"""Minimal registry loader and fail-closed validation; standard library only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_STATUSES = {"planned", "candidate", "active", "deprecated"}
REQUIRED_AGENT_FIELDS = {
    "agent_id", "display_name", "kind", "version", "status",
    "enabled", "capabilities", "execution_boundary",
}


class RegistryError(ValueError):
    """The registry is malformed or contains an unsafe registration."""


def validate_registry(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise RegistryError("REGISTRY_NOT_OBJECT")
    if document.get("schema_version") != 1:
        raise RegistryError("UNSUPPORTED_REGISTRY_SCHEMA")
    agents = document.get("agents")
    if not isinstance(agents, list):
        raise RegistryError("AGENTS_NOT_LIST")

    seen: set[str] = set()
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise RegistryError(f"AGENT_NOT_OBJECT:{index}")
        missing = REQUIRED_AGENT_FIELDS - agent.keys()
        if missing:
            raise RegistryError(f"AGENT_MISSING_FIELDS:{index}:{','.join(sorted(missing))}")

        agent_id = agent["agent_id"]
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise RegistryError(f"INVALID_AGENT_ID:{index}")
        if agent_id in seen:
            raise RegistryError(f"DUPLICATE_AGENT_ID:{agent_id}")
        seen.add(agent_id)

        if agent["status"] not in VALID_STATUSES:
            raise RegistryError(f"INVALID_AGENT_STATUS:{agent_id}")
        if not isinstance(agent["enabled"], bool):
            raise RegistryError(f"INVALID_ENABLED_FLAG:{agent_id}")
        if agent["status"] != "active" and agent["enabled"]:
            raise RegistryError(f"NON_ACTIVE_AGENT_MUST_BE_DISABLED:{agent_id}")

        capabilities = agent["capabilities"]
        if (not isinstance(capabilities, list)
                or any(not isinstance(value, str) or not value.strip() for value in capabilities)
                or len(capabilities) != len(set(capabilities))):
            raise RegistryError(f"INVALID_CAPABILITIES:{agent_id}")

        boundary = agent["execution_boundary"]
        if not isinstance(boundary, dict):
            raise RegistryError(f"INVALID_EXECUTION_BOUNDARY:{agent_id}")
        if not isinstance(boundary.get("production_mutation_allowed"), bool):
            raise RegistryError(f"MISSING_PRODUCTION_BOUNDARY:{agent_id}")

    return document


def load_registry(path: str | Path) -> dict[str, Any]:
    registry_path = Path(path)
    try:
        document = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"REGISTRY_READ_FAILED:{registry_path}") from error
    return validate_registry(document)


def find_agent(registry: dict[str, Any], agent_id: str) -> dict[str, Any]:
    validate_registry(registry)
    for agent in registry["agents"]:
        if agent["agent_id"] == agent_id:
            return agent
    raise RegistryError(f"AGENT_NOT_REGISTERED:{agent_id}")
