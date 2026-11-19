from __future__ import annotations

import json
import unittest
from pathlib import Path


class ContractFileTests(unittest.TestCase):
    def test_all_contracts_are_valid_json_and_have_draft(self):
        root = Path(__file__).resolve().parents[2]
        contract_files = sorted((root / "contracts").glob("*.schema.json"))
        self.assertGreaterEqual(len(contract_files), 6)
        for path in contract_files:
            with self.subTest(contract=path.name):
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertIn("type", schema)
                self.assertIn("properties", schema)

    def test_registry_json_is_valid(self):
        root = Path(__file__).resolve().parents[2]
        registry = json.loads((root / "config" / "agents.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual(len(registry["agents"]), 2)


if __name__ == "__main__":
    unittest.main()
