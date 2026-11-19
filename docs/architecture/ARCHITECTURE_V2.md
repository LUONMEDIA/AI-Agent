# LUON AI-Agent Platform — Architecture V2

**Status:** Foundation implementation / candidate architecture  
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
- Validate the shared task envelope.
- Resolve a specialist from an explicit agent ID or a unique capability match.
- Enforce lifecycle and policy gates before dispatch.
- Correlate task, artifact, approval, and audit references.
- Provide consistent error states and stop conditions.

The first foundation implementation provides registry validation and deterministic capability routing only. Persistent state, distributed locks, provider invocation, authorization services, and dispatch workers are not implemented by this PR.

### C. Specialist agents and applications

A specialist owns its domain semantics, source-of-truth rules, QA, and action-specific permissions. Examples:
- CTTĐT/CMS workflow: document class, CMS module, source hash, signed item scope, duplicate search, readback, and QLVB boundary.
- Lượn Photo Batch: local image editing and export. This is an application, not automatically an autonomous agent.
- Future Marketing, Audio/Video, SEO, Sales, or Finance modules must be registered only when their implementation and owner are real and reviewed. No placeholder agent is enabled by inference.

### D. Connectors and execution adapters

Adapters translate a specialist's approved contract into a concrete system integration: GitHub, Google Drive, CMS, QLVB, Canva, Meta, local filesystem, or a supported AI runtime. Each adapter must declare its target, data scope, authentication model, read/write behavior, and verification method.

A connector is not a permission grant. Credentials and session state stay outside this public repository.

## 3. Routing policy

1. Every task declares `task_id` and `required_capability`.
2. A caller may set `target_agent` when it knows the exact specialist.
3. Without an explicit target, the router may select only one enabled, active registration advertising the required capability.
4. Zero matches, disabled candidates, and multiple matches are hard errors. The router never guesses.
5. Routing does not authorize an action. The specialist performs its own permission, approval, target, and source checks.
6. The AI runtime/provider is selected by the execution environment, not by the domain capability router.

## 4. Shared contracts

Contracts are versioned JSON Schema files:
- Task: request envelope and routing intent.
- Agent: registration, capability, lifecycle, and execution boundary.
- Artifact: URI, SHA-256, provenance, and sensitivity.
- Approval: issuer, exact subject/actions/scope, validity window, revocation reference, signature.
- Event: normalized lifecycle and audit event.

Specialist-specific payloads remain owned by their specialist. Do not force CMS metadata and photo-editing settings into one generic schema.

## 5. Security and trust boundaries

- Public GitHub stores source, schemas, synthetic fixtures, and non-sensitive docs only.
- Secrets, auth states, private source files, signed approval payloads, production manifests, and operational audit logs stay in private execution environments.
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

- Provider-specific ChatGPT/Claude/Codex adapters.
- Persistent task database, durable queue, retries, or distributed scheduler.
- Central identity, policy decision point, signing service, or key lifecycle.
- Cross-host idempotency/distributed locking.
- Google Drive/CMS/QLVB/Meta/Canva connectors.
- Production executor or deployment pipeline.
- Full end-to-end integration tests against real external systems.

These require explicit technical choices, external credentials/permissions, and independently observed acceptance evidence. The foundation must not simulate them and call them complete.

## 9. Acceptance criteria for this foundation

- Shared contracts are versioned and parse as JSON.
- Registry rejects malformed entries, duplicate IDs, and enabled non-active agents.
- Router routes only a unique active capability match and fails closed otherwise.
- CMS and Photo candidates are registered but disabled.
- CI is offline, secret-free, and runs the platform unit tests.
- Existing specialist PRs and main remain unchanged until review.
