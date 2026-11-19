# LUON AI-Agent Platform

A provider-neutral foundation for coordinating specialist workflows across ChatGPT, Claude, Codex, and other authorized runtimes.

## Current status

- **Architecture:** V2 foundation plus local workflow orchestration, under review on `feat/agent-platform-v2-foundation`.
- **Default branch:** unchanged; this work is not merged or deployed.
- **CMS Batch Agent:** separate draft PR #1; 49/49 offline synthetic tests reported by that PR. Live CMS staging has not been validated.
- **Lượn Photo Batch:** separate PR #3; a browser-based photo application, not yet an integrated platform agent.
- **Production execution:** not authorized by this foundation.

## What this foundation contains

- Versioned JSON contracts for workflows, tasks, agent registrations, artifacts, scoped approvals, and lifecycle events.
- A standard-library Python core for registry validation, deterministic capability routing, local SQLite workflow state, dependency scheduling, task leases, reconciliation, and a pluggable in-process adapter runner.
- A hash-chained event history that can detect accidental or unauthorized record changes when verified against a trusted checkpoint.
- An explicit registry that records current CMS and Photo work as disabled candidates.
- Architecture, security boundaries, and a migration plan that preserve the existing specialist work.

## Quick start

Python 3.11+; no third-party runtime dependencies:

```bash
PYTHONPATH=src python -m unittest discover -s tests/platform -v
```

Create a private local state store outside this repository:

```python
from pathlib import Path
from luon_agent_platform import StateStore, WorkflowOrchestrator, load_registry

store = StateStore(Path.home() / ".luon-ai-agent" / "workflow.sqlite3")
registry = load_registry(Path("config/agents.json"))
orchestrator = WorkflowOrchestrator(store, registry)
```

Submit a workflow definition using the contract in `contracts/workflow.schema.json`. The orchestrator can plan and claim tasks only for a unique active capability match. A claim creates a time-limited local lease. The TaskRunner invokes only an explicitly injected adapter whose registry entry is active and classified as a workflow agent.

The router intentionally refuses to route to disabled/candidate agents. Expired worker leases move to `needs_reconciliation`; they are never automatically requeued.

## Documentation

- [Architecture V2](docs/architecture/ARCHITECTURE_V2.md)
- [State and orchestration](docs/architecture/STATE_AND_ORCHESTRATION.md)
- [Specialist adapter contract](docs/architecture/AGENT_ADAPTER_CONTRACT.md)
- [Integration matrix](docs/architecture/INTEGRATION_MATRIX.md)
- [Contracts](contracts/README.md)
- [Global agent guidance](AGENTS.md)

## Deliberate limitations

- SQLite state is for a private **single-host** runtime only. It is not a distributed lock or multi-host queue.
- Hash chaining is tamper-evident, not immutable or a substitute for a signed external audit checkpoint.
- There is no ChatGPT / Claude / Codex provider-specific adapter, background worker, remote dispatcher, central identity service, approval-signature verifier, or production connector in this package. The in-process adapter interface is not a sandbox.
- The state store does not grant permission to perform any specialist action.

## Safety

This public repository is for source code, schemas, synthetic fixtures, and non-sensitive documentation only. Never commit credentials, cookies, auth state, private signing keys, personal data, official source documents, production manifests, database files, WAL files, or operational audit logs.
