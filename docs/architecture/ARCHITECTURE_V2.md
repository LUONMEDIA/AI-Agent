# LUON AI-Agent Platform — Architecture V2

**Status:** Foundation plus local orchestration implementation / candidate architecture  
**Date:** 2026-11-19  
**Target repository:** `LUONMEDIA/AI-Agent`  
**Release posture:** no merge, deployment, production connection, or production mutation authorized by this work.

## 1. Objective

Provide one shared, provider-neutral platform contract for multiple AI runtimes and specialist workflows without forcing them to share domain logic, credentials, sessions, or execution authority.

The platform coordinates work. It is not itself a universal agent and it does not turn every connected AI into an authorized executor.

## 2. Four distinct layers

### A. AI client / runtime layer

ChatGPT, Claude, Codex, and future authorized runtimes are clients that can submit or help prepare tasks. Each client has its own identity, tools, environment, and permissions. The platform must not assume that a GitHub connection grants access to Codex Cloud, a local Chrome session, or a private business system.

Provider-specific SDKs and credentials belong in separately managed adapters/environments, never in shared core.

### B. Shared core

Responsibilities:
- Validate versioned workflow and task envelopes.
- Resolve a specialist from an explicit agent ID or a unique capability match.
- Persist local workflow/task state and dependency relationships.
- Claim work through short-lived local leases and enforce task lifecycle transitions.
- Correlate task, artifact, approval, and audit references.
- Detect event-history tampering and expose explicit reconciliation states.

The current implementation includes registry validation, deterministic routing, a local SQLite state store, dependency scheduling, local task leases, and a hash-chained event history. It does not include distributed coordination, provider invocation, centralized authorization, or remote dispatch workers.

### C. Specialist agents and applications

A specialist owns its domain semantics, source-of-truth rules, QA, and action-specific permissions. Examples:
- CTTĐT/CMS workflow: document class, CMS module, source hash, signed item scope, duplicate search, readback, and QLVB boundary.
- Lượn Photo Batch: local image editing and export. This is an application, not automatically an autonomous agent.
- Future Marketing, Audio/Video, SEO, Sales, or Finance modules must be registered only when their implementation and owner are real and reviewed. No placeholder agent is enabled by inference.

### D. Connectors and execution adapters

Adapters translate a specialist's approved contract into a concrete system integration: GitHub, Google Drive, CMS, QLVB, Canva, Meta, local filesystem, or a supported AI runtime. Each adapter must declare its target, data scope, authentication model, read/write behavior, and verification method.

A connector is not a permission grant. Credentials and session state stay outside this public repository.

## 3. Routing and orchestration policy

1. Every workflow has a stable workflow ID and one or more task IDs.
2. Every task declares a task type and required capability; optional dependencies form a directed acyclic graph.
3. A caller may set a target agent when it knows the exact specialist.
4. Without an explicit target, the router may select only one enabled, active registration advertising the required capability.
5. Zero matches, disabled candidates, and multiple matches are hard errors. The router never guesses.
6. A claim records a local worker lease and assigned agent; it does not call that agent.
7. Only successful completion releases dependent tasks. Failed, blocked, or unknown-outcome tasks do not release downstream work.
8. Expired leases move to NEEDS_RECONCILIATION. There is no automatic retry after a worker disappears.
9. Routing and leasing do not authorize a specialist action. The specialist must perform its own permission, approval, target, source, and readback checks.
10. The AI runtime/provider is selected by the execution environment, not by the domain capability router.

## 4. Shared contracts

Contracts are versioned JSON Schema files:
- Workflow: task graph and dependency envelope.
- Task: request envelope and routing intent.
- Agent: registration, capability, lifecycle, and execution boundary.
- Artifact: URI, SHA-256, provenance, and sensitivity.
- Approval: issuer, exact subject/actions/scope, validity window, revocation reference, signature.
- Event: normalized workflow/task lifecycle and audit event.

Specialist-specific payloads remain owned by their specialist. Do not force CMS metadata and photo-editing settings into one generic schema.

## 5. State, audit, and trust boundaries

- The SQLite store is a private local development/runtime component and must live outside the public repository.
- SQLite WAL and BEGIN IMMEDIATE serialize local writes; this does not establish cross-host locking or safe network-filesystem semantics.
- The event chain is tamper-evident against changes detectable from the stored chain. It is not immutable against an attacker who can rewrite the database and recompute the chain; production audit requires an independently protected signed checkpoint or external append-only sink.
- Lease expiry never requeues a task. The task is moved to NEEDS_RECONCILIATION and requires evidence plus an explicit resolution.
- Secrets, auth states, private source files, signed approval payloads, production manifests, database files, and operational audit logs stay in private execution environments.
- A signature establishes integrity and signer identity only when the verifier trusts the key. It does not replace target-system account permissions or operator approval.
- Staging and production are separate targets. A staging test cannot be represented as production acceptance.
- Unknown remote write outcome, failed readback, duplicate ambiguity, expired/revoked approval, or target mismatch => stop and reconcile. No blind retry.
- CMS Step 1 and QLVB/public QA Step 2 remain distinct workflows.
- Human approval must be scoped to exact resource fingerprints, action, target/module, and validity period.

## 6. Registry lifecycle

`planned` → `candidate` → `active` → `deprecated`

- `planned`: design only; no executable integration.
- `candidate`: code exists or is proposed, but acceptance is incomplete; must remain disabled.
- `active`: reviewed, tested, owned, and explicitly enabled within a declared execution boundary.
- `deprecated`: no new tasks; migration path documented.

The foundation registry currently records:
- `cttdt.cms-batch`: candidate, disabled; references draft PR #1.
- `luon.photo-batch`: candidate, disabled; references PR #3 and is classified as an application.

Neither is dispatchable through the new router.

## 7. Current repository facts and integration constraints

At the time of this architecture change:
- `main` contains only a minimal README.
- PR #1 is an open draft CMS specialist implementation. Its 49/49 result is reported for offline synthetic tests; staging has not been run and production is explicitly blocked.
- Issue #2 records CMS staging, permissions, readback, and cross-host idempotency blockers.
- Issue #4 requires alignment with the current Khánh Hòa workflow authorities and explicitly prohibits production cutover.
- PR #3 is an open standalone photo application proposal; its own analysis documents missing RAW/HEIC support, heuristic skin masking, no ICC workflow, and large-batch memory limitations.

This foundation deliberately does not merge, rebase, or rewrite those PRs. Their code and their CI evidence remain separate until an integration review.

## 8. Deliberately not implemented yet

- Provider-specific ChatGPT/Claude/Codex adapters and actual remote dispatch.
- Shared multi-host database/queue, distributed locks, or distributed scheduler.
- Central identity, policy decision point, approval-signature verification service, or key lifecycle.
- External append-only audit sink and independently signed audit checkpoints.
- Google Drive/CMS/QLVB/Meta/Canva connectors.
- Production executor or deployment pipeline.
- Full end-to-end integration tests against real external systems.

These require explicit technical choices, external credentials/permissions, and independently observed acceptance evidence. The foundation must not simulate them and call them complete.

## 9. Acceptance criteria for this increment

- Shared contracts are versioned and parse as JSON.
- Registry rejects malformed entries, duplicate IDs, and enabled non-active agents.
- Router routes only a unique active capability match and fails closed otherwise.
- Workflow definitions reject unknown dependencies and cycles.
- Workflow creation is idempotent for an identical workflow specification and rejects changed content under the same workflow ID.
- Local task claims are serialized, lease-bound, and protected by optimistic versions.
- Successful dependencies release downstream tasks; failed/blocked/unknown outcomes do not.
- Expired leases enter NEEDS_RECONCILIATION and are never automatically retried.
- Event-chain verification detects modified event records.
- CMS and Photo candidates remain registered but disabled.
- CI is offline, secret-free, and runs the platform unit tests.
- Existing specialist PRs and main remain unchanged until review.
