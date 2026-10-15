"""CLI: plan and test locally, execute only with an explicitly selected adapter."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from .engine import load_batch, dry_run, execute_item, Hold


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CMS batch preparation and gated execution")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--live", action="store_true", help="Explicitly request mutating execution")
    parser.add_argument("--approval-file", type=Path, help="Private, never committed")
    parser.add_argument("--adapter", help="Dotted Class path; never loaded in dry-run")
    args = parser.parse_args(argv)
    try:
        batch = load_batch(args.manifest)
        if not args.live:
            print(json.dumps(dry_run(batch), ensure_ascii=False, indent=2))
            return 0
        if not args.approval_file or not args.adapter:
            raise Hold("LIVE_REQUIRES_APPROVAL_FILE_AND_ADAPTER")
        if args.adapter == "cms_agent.mock.MockCMS":
            raise Hold("MOCK_ADAPTER_FORBIDDEN_FOR_LIVE_CLI")
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        module_name, _, class_name = args.adapter.rpartition(".")
        if not module_name:
            raise Hold("INVALID_ADAPTER_PATH")
        adapter_type = getattr(importlib.import_module(module_name), class_name)
        # Browser adapters should implement context management for cleanup.
        adapter = adapter_type()
        try:
            results = [execute_item(batch, item, approval, adapter) for item in batch.items]
        finally:
            if hasattr(adapter, "close"):
                adapter.close()
        print(json.dumps({"case_id": batch.case_id, "results": results},
                         ensure_ascii=False, indent=2))
        return 2 if any(r["status"] in {"HOLD", "NEEDS_RECONCILE"} for r in results) else 0
    except (Hold, ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
