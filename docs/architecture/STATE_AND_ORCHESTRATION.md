# Local State and Workflow Orchestration

**Implementation status:** local single-host foundation; candidate for review.  
**Module:** `luon_agent_platform.state_store` and `luon_agent_platform.orchestrator`.

## 1. Purpose

Provide durable task lifecycle and dependency coordination without coupling the shared core to a specific AI provider or business system.

The state store is a coordination primitive. It is not an execution adapter, an authorization authority, or a distributed queue.

## 2. Workflow definition

A workflow uses the versioned schema in `contracts/workflow.schema.json`. Each task declares:
- Stable task ID and task type.
- Required capability and optional exact target agent.
- Specialist-owned JSON payload.
- Zero or more dependencies.
- Optional idempotency key.

The definition is validated before persistence. Unknown dependencies, duplicate task IDs, duplicate idempotency keys, self-dependencies, and dependency cycles are rejected.

Creating the same workflow ID with the same normalized specification is idempotent. Reusing that ID with changed content is a conflict. Task IDs are globally unique in the local database.

## 3. Task lifecycle

| State | Meaning |
|---|---|
| pending | Waiting for one or more dependencies |
| ready | Dependencies succeeded; task may be planned for dispatch |
| claimed | One local worker holds a time-limited lease |
| running | Worker has started under the lease |
| blocked | Specialist/operator gate prevents progress |
| succeeded | Specialist reports success after its own verification |
| failed | Known failure; no automatic retry |
| needs_reconciliation | Outcome or worker state is uncertain; stop and investigate |
| cancelled | Explicitly cancelled by a future approved control path |

Only successful dependencies release downstream tasks. A failed, blocked, or uncertain parent keeps its children pending.

## 4. Local claim and lease semantics

The orchestrator:
1. Lists ready tasks.
2. Resolves exactly one active registry entry for the required capability.
3. Refuses disabled, candidate, missing, or ambiguous registrations.
4. Claims the task with a random lease token, worker ID, assigned agent, expiry, and optimistic task version.
5. Requires the token to start or finish the task.

The token is returned only to the claimant and is not written into event details. The local database stores it so it can be checked. A production system needs stronger worker identity and secret-handling controls.

A lease expires after 1–3600 seconds. Expired claimed/running tasks are moved to NEEDS_RECONCILIATION. They are not reset to ready because a worker may have completed an external write immediately before it disappeared.

## 5. Reconciliation

A task in NEEDS_RECONCILIATION can be resolved only with an evidence reference and an explicit operator/system actor:
- succeeded: independent readback confirms completion; dependencies may then be released.
- failed: evidence confirms a known failure; dependent tasks remain pending.
- ready: evidence confirms no external side effect occurred and a retry is safe.

A reference is stored, not fetched or validated by the core. The executor/operator must verify the evidence and permission before calling this method. The core does not issue approvals.

Blocked tasks can be released only with an evidence reference. The specialist still must recheck its own gates before doing work.

## 6. Event history

Each state mutation appends an event to a global SHA-256 hash chain. The next event includes the previous event hash. The verifier checks sequence linkage and recalculates every event digest.

This is tamper-evident, not immutable. An actor with full database write access could rewrite the entire chain. A production-grade audit service must anchor signed checkpoints outside the database or write to an independently protected append-only sink.

## 7. Storage and concurrency boundary

- Store the database in a private local path outside the repository.
- SQLite WAL and BEGIN IMMEDIATE serialize writers using the same local database.
- Do not place the file on a network share or treat this implementation as safe across hosts.
- No credentials, source documents, approval payloads, or production audit records belong in GitHub.
- Backups, retention, encryption-at-rest, key management, and disaster recovery are deployment responsibilities and are not implemented here.

## 8. Not implemented

- Background worker process or external agent invocation.
- ChatGPT, Claude, Codex, or other provider adapters.
- Cross-host queue, distributed leases, and distributed idempotency.
- Centralized identity and authorization.
- Signed approval verification or revocation service.
- Remote CMS, Drive, Meta, Canva, QLVB, or other connector.
- Automatic retries, compensation, or production deployment.

These must be designed and accepted separately. A task claim does not imply that a provider or specialist has been invoked.
