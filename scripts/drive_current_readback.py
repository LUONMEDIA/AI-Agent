#!/usr/bin/env python3
"""Operator-run, read-only live CURRENT/Sheets audit (no background or Drive writes).

Run with PYTHONPATH=src. Private config and short-lived token stay outside Git.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from luon_agent_platform.drive_current import BridgeError, DriveCurrentBridge, DriveReader
from luon_agent_platform.drive_state_mirror import DriveWorkflowStateBridge, LocalStateMirror


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CURRENT Google Drive sources (read only)")
    parser.add_argument("--config", required=True, help="Private JSON mapping outside repository")
    parser.add_argument("--runtime", choices=("CHAT", "CODEX", "MIXED", "AUDIT"), default="AUDIT")
    parser.add_argument("--sync-state", action="store_true", help="Read one private Sheet row and mirror locally")
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        reader = DriveReader(os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", ""))
        receipt = DriveCurrentBridge(reader, config).resolve(args.runtime)
        result = {
            "authority": receipt["authority"],
            "runtime": receipt["runtime"],
            "readback": receipt["readback"],
            "sources_verified": len(receipt["sources"]),
            "source_roles": [item["role"] for item in receipt["sources"]],
            "drive_write_performed": False,
            "agent_execution_authorized": False,
        }
        if args.sync_state:
            state = config.get("state_source")
            if not isinstance(state, dict) or set(state) != {
                "spreadsheet_id", "range", "workflow_id", "local_mirror_path"
            }:
                raise BridgeError("STATE_SOURCE_CONFIG_MISSING")
            snapshot = DriveWorkflowStateBridge(
                reader, state["spreadsheet_id"], state["range"]
            ).read_workflow(state["workflow_id"])
            result["local_state"] = LocalStateMirror(
                state["local_mirror_path"]
            ).synchronize(snapshot)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (BridgeError, OSError, json.JSONDecodeError) as error:
        code = error.code if isinstance(error, BridgeError) else "PRIVATE_CONFIG_UNAVAILABLE"
        print(json.dumps({"result": "HOLD", "reason_code": code, "production_mutation": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
