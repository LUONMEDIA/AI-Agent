"""CLI: offline dry-run by default; privileged live run only after review."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

from .engine import load_batch, dry_run, Hold
from .runner import PrivateAudit, run_batch


APPROVED_LIVE_ADAPTERS = frozenset({"cms_agent.browser.BrowserCMS"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CMS batch preparation and gated execution")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--live", action="store_true", help="Explicitly request mutating execution")
    parser.add_argument("--approval-file", type=Path, help="Signed permit; private/outside repo")
    parser.add_argument("--adapter", help="Explicit reviewed adapter ID")
    args = parser.parse_args(argv)
    try:
        batch = load_batch(args.manifest)
        if not args.live:
            print(json.dumps(dry_run(batch), ensure_ascii=False, indent=2))
            return 0
        if os.getenv("GITHUB_ACTIONS") or os.getenv("CI", "").lower() == "true":
            raise Hold("LIVE_FORBIDDEN_IN_CI")
        if not args.approval_file or not args.adapter:
            raise Hold("LIVE_REQUIRES_APPROVAL_FILE_AND_ADAPTER")
        if args.adapter not in APPROVED_LIVE_ADAPTERS:
            raise Hold("UNREVIEWED_LIVE_ADAPTER_BLOCKED")
        audit_path = os.getenv("CMS_AGENT_AUDIT_PATH")
        if not audit_path:
            raise Hold("PRIVATE_AUDIT_PATH_REQUIRED")
        audit = PrivateAudit(audit_path)
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        module_name, _, class_name = args.adapter.rpartition(".")
        adapter_type = getattr(importlib.import_module(module_name), class_name)
        adapter = adapter_type()
        try:
            results = run_batch(batch, approval, adapter, audit)
        finally:
            if hasattr(adapter, "close"):
                adapter.close()
        print(json.dumps({"case_id": batch.case_id, "results": results},
                         ensure_ascii=False, indent=2))
        return 2 if len(results) < len(batch.items) or any(
            r["status"] in {"HOLD", "NEEDS_RECONCILE"} for r in results
        ) else 0
    except (Hold, ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False))
        return 2
    except Exception as error:
        print(json.dumps({"status": "BLOCKED_RUNTIME_ERROR",
                          "error_type": type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
