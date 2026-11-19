from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from luon_agent_platform import (
    RegistryError,
    RouteError,
    find_agent,
    load_registry,
    route_task,
    validate_registry,
)


def sample_registry():
    return {
        "schema_version": 1,
        "registry_version": "test",
        "agents": [
            {
                "agent_id": "test.alpha",
                "display_name": "Test Alpha",
                "kind": "workflow_agent",
                "version": "1.0.0",
                "status": "active",
                "enabled": True,
                "capabilities": ["test.run"],
                "execution_boundary": {
                    "mode": "read_only",
                    "production_mutation_allowed": False,
                },
            }
        ],
    }


class RegistryTests(unittest.TestCase):
    def test_load_repository_registry(self):
        root = Path(__file__).resolve().parents[2]
        registry = load_registry(root / "config" / "agents.json")
        self.assertEqual(len(registry["agents"]), 2)
        self.assertTrue(all(not agent["enabled"] for agent in registry["agents"]))

    def test_duplicate_ids_are_rejected(self):
        registry = sample_registry()
        registry["agents"].append(dict(registry["agents"][0]))
        with self.assertRaisesRegex(RegistryError, "DUPLICATE_AGENT_ID"):
            validate_registry(registry)

    def test_candidate_cannot_be_enabled(self):
        registry = sample_registry()
        registry["agents"][0]["status"] = "candidate"
        with self.assertRaisesRegex(RegistryError, "NON_ACTIVE_AGENT_MUST_BE_DISABLED"):
            validate_registry(registry)

    def test_find_agent(self):
        agent = find_agent(sample_registry(), "test.alpha")
        self.assertEqual(agent["display_name"], "Test Alpha")

    def test_load_invalid_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "REGISTRY_READ_FAILED"):
                load_registry(path)


class RouterTests(unittest.TestCase):
    def test_routes_one_active_capability_match(self):
        result = route_task(
            {"task_id": "T-1", "required_capability": "test.run"},
            sample_registry(),
        )
        self.assertEqual(result["agent_id"], "test.alpha")

    def test_candidate_agents_are_not_routable(self):
        root = Path(__file__).resolve().parents[2]
        registry = load_registry(root / "config" / "agents.json")
        with self.assertRaisesRegex(RouteError, "MATCHING_AGENT_DISABLED_OR_NOT_ACTIVE"):
            route_task(
                {"task_id": "T-2", "required_capability": "cms.document.prepare"},
                registry,
            )

    def test_unknown_capability_is_not_guessed(self):
        with self.assertRaisesRegex(RouteError, "NO_AGENT_FOR_CAPABILITY"):
            route_task(
                {"task_id": "T-3", "required_capability": "unknown.operation"},
                sample_registry(),
            )

    def test_ambiguous_capability_is_rejected(self):
        registry = sample_registry()
        second = dict(registry["agents"][0])
        second["agent_id"] = "test.beta"
        registry["agents"].append(second)
        with self.assertRaisesRegex(RouteError, "AMBIGUOUS_AGENT_ROUTE"):
            route_task(
                {"task_id": "T-4", "required_capability": "test.run"},
                registry,
            )

    def test_explicit_target_must_have_capability(self):
        registry = sample_registry()
        with self.assertRaisesRegex(RouteError, "TARGET_AGENT_LACKS_REQUIRED_CAPABILITY"):
            route_task(
                {
                    "task_id": "T-5",
                    "target_agent": "test.alpha",
                    "required_capability": "test.other",
                },
                registry,
            )

    def test_task_id_is_required(self):
        with self.assertRaisesRegex(RouteError, "TASK_ID_REQUIRED"):
            route_task({"required_capability": "test.run"}, sample_registry())


if __name__ == "__main__":
    unittest.main()
