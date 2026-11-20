# Google Drive CURRENT bridge — candidate integration (20 November 2026)

**Status: CANDIDATE / NOT_ACTIVE.** This is not a production deployment or a replacement for the Drive Master Release Governor.

## Trust boundaries

Google Drive Active Manifest V2.0 remains the sole CURRENT authority for Master modules. The public GitHub repository stores **code, synthetic tests and instructions only**, never secrets, private documents, production manifests, operational state, or actual case identifiers. Google Drive credentials and private source mappings live in an owner-authorized execution environment, outside Git. A ChatGPT/Claude/Codex connector does NOT automatically authenticate this repository.

The new registry entry (master.drive-current-read) is **candidate, disabled, read-only**, and cannot receive tasks through the existing router. Specialist CMS/Photo candidates remain disabled.

## Implemented

- Resolve a private exact Manifest ID and verify CURRENT / CANONICAL routes against the module list; reject missing, duplicate, Candidate or Legacy routes.
- Read Google Docs metadata and text only via Drive API GET/export with short-lived OAuth2; verify ID, title, heading, MIME type, modified time, version, and manifest non-drift. No document content is stored in Git.
- Produce private readback receipts with SHA-256 content digest; targeted CHAT/CODEX and full MIXED/AUDIT profiles.
- Optionally read one exact Workflow_ID row from an owner-authorized Google Sheet via Sheets API GET, enforce state schema, duplicate/no-source checks, and sheet revision stability.
- Atomically mirror a verified state checkpoint to a private local file outside Git with version continuity and Completed_PASS preservation. The mirror does not write back into Google Drive.
- Adapter compatible with TaskRunner for synthetic tests, but not enabled for real dispatch.
- Offline synthetic regression for authority, adapter, workflow state, negative paths.

## Private configuration

Prepare a JSON file at a private path (example /secure/drive-current.json) **not tracked by Git**:

{
  "schema_version": 1,
  "manifest": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — ACTIVE MANIFEST V2.0 — CURRENT ONLY"},
  "modules": {
    "CORE": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — MASTER AI RELIABILITY & OPTIMIZATION GOVERNOR V2.0 — CANONICAL"},
    "ROUTER": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "02 — MASTER ROUTER & INTERVENTION POLICY V2.0 — CANONICAL"},
    "CHAT": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — CHAT RUNTIME ORCHESTRATOR V2.0 — CANONICAL"},
    "CODEX": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — CODEX RUNTIME EXECUTION GOVERNOR V2.0 — CANONICAL"},
    "INCIDENT": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — INCIDENT TRIAGE & ROOT CAUSE ENGINE V2.0 — CANONICAL"},
    "STATE/PROGRESS": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — PROGRESS, STATE & HANDOFF GOVERNOR V2.0 — CANONICAL"},
    "EVOLUTION": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — SYSTEM EVOLUTION & MIGRATION GOVERNOR V2.0 — CANONICAL"},
    "QA": {"id": "REPLACE_WITH_VERIFIED_ID", "title": "01 — GOLDEN TESTS & RELEASE GATES V2.0 — CANONICAL"}
  },
  "state_source": {
    "spreadsheet_id": "REPLACE_WITH_OWNER_AUTHORIZED_SHEET_ID",
    "range": "STATE!A1:W",
    "workflow_id": "REPLACE_WITH_VERIFIED_WORKFLOW_ID",
    "local_mirror_path": "/secure/workflows/private-checkpoint.json"
  }
}

The IDs are deliberately not shipped. Verify exact titles and IDs against CURRENT before use. Placeholders are not valid IDs.

## State sheet contract

Header row must include Workflow_ID, Objective, Success_Criteria, Runtime, Current_Stage, Status, Priority, Completed_PASS, Next_Actions and State_Version. JSON-valued fields (Completed_PASS, Next_Actions, Blockers and others) must be valid JSON arrays/objects, not informal comma-separated strings. One Workflow_ID must appear exactly once. State_Version must not skip or regress; invalidated completed steps require an explicit Invalidated_Steps list. An existing sheet with different column semantics requires owner-approved mapping, not guessing.

## Operator-run read-only acceptance

Use Python 3.11+ and provision a **short-lived read-only OAuth2 token** with Drive read-only scope and, for state sync, Sheets read-only scope. Do not place a token in GitHub, an issue, or public logs. Set GOOGLE_DRIVE_ACCESS_TOKEN privately and execute:

    PYTHONPATH=src python scripts/drive_current_readback.py --config /secure/drive-current.json --runtime AUDIT
    PYTHONPATH=src python scripts/drive_current_readback.py --config /secure/drive-current.json --runtime MIXED --sync-state

The CLI prints only a safe result summary, not source text or access tokens. All failures are HOLD with a reason and exit code 2; no blind retry, background execution, or Drive mutation occurs.

## Required acceptance evidence

| Gate | Status |
|---|---|
| Offline authority/adapter/state regressions | Run in GitHub CI |
| Real OAuth Drive CURRENT GET/export readback | NOT_RUN in GitHub execution environment |
| Real authorized Sheet row import and private mirror | NOT_RUN in GitHub execution environment |
| Independent Chat/Work/Codex cross-runtime receipts | NOT_RUN |
| Production credentials / identity / retention / owner review | NOT_RUN |
| Drive critical golden tests + live GT-L01..GT-L07 | NOT_READY |
| Manifest cutover, remote writeback or production dispatch | NOT_AUTHORIZED |

A successful live read in an existing ChatGPT connector is not evidence that this code has been authenticated in its own execution runtime. CI synthetic tests do not qualify as LIVE_PASS.

## Rollback

This branch changes neither the Drive CURRENT authority nor production services. Rollback by closing the PR and removing only candidate local pilot files; preserve the existing foundation branch and canonical Drive assets. Never merge/deploy until independent acceptance and an approved release gate.
