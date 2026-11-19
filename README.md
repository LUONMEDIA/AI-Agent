# LUON AI-Agent Platform

A provider-neutral foundation for coordinating specialist workflows across ChatGPT, Claude, Codex, and other authorized runtimes.

## Current status

- **Architecture:** V2 foundation, under review on `feat/agent-platform-v2-foundation`.
- **Default branch:** unchanged; this work is not merged or deployed.
- **CMS Batch Agent:** separate draft PR #1; 49/49 offline synthetic tests reported by that PR. Live CMS staging has not been validated.
- **Lượn Photo Batch:** separate PR #3; a browser-based photo application, not yet an integrated platform agent.
- **Production execution:** not authorized by this foundation.

## What this foundation contains

- Shared JSON contracts for tasks, agent registrations, artifacts, approvals, and audit events.
- A small standard-library Python core for validating the agent registry and deterministic capability routing.
- An explicit registry that records current CMS and Photo work as disabled candidates.
- Architecture, security boundaries, and a migration plan that preserve the existing specialist work.

## Quick start

Python 3.11+; no third-party runtime dependencies:

```bash
PYTHONPATH=src python -m unittest discover -s tests/platform -v
```

Load and inspect the registry:

```python
from pathlib import Path
from luon_agent_platform import load_registry

registry = load_registry(Path("config/agents.json"))
print([agent["agent_id"] for agent in registry["agents"]])
```

The router intentionally refuses to route to disabled/candidate agents. No provider SDK, credentials, CMS URL, browser session, or production connector is configured by this package.

## Documentation

- [Architecture V2](docs/architecture/ARCHITECTURE_V2.md)
- [Integration matrix](docs/architecture/INTEGRATION_MATRIX.md)
- [Contracts](contracts/README.md)
- [Global agent guidance](AGENTS.md)

## Safety

This public repository is for source code, schemas, synthetic fixtures, and non-sensitive documentation only. Never commit credentials, cookies, auth state, private signing keys, personal data, official source documents, production manifests, or operational audit logs.
