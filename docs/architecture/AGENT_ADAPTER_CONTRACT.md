# Specialist Agent Adapter Contract

**Status:** in-process interface implemented; no real provider or specialist adapter enabled.

## 1. Purpose

Define the narrow boundary between the shared workflow runner and a specialist workflow agent. The shared runner handles task state, capability routing, leases, and normalized outcomes. The specialist adapter owns all domain rules and external-system behavior.

## 2. Adapter interface

A workflow adapter exposes:
- `agent_id`: exact ID of an active `workflow_agent` registry entry.
- `execute(TaskContext)`: synchronous execution of one task.
- A normalized `AdapterResult` outcome: succeeded, failed, blocked, or needs_reconciliation.

The registry rejects duplicate adapters, unknown IDs, disabled/candidate agents, and registrations classified as applications rather than workflow agents.

## 3. Runner sequence

1. Read the task and ask the orchestrator for a dispatch plan.
2. Require a unique active capability route.
3. Require an installed adapter before claiming the task.
4. Claim with a local lease and optimistic task version.
5. Mark the task running under the lease.
6. Call the adapter in-process with task metadata and specialist-owned payload.
7. Persist the adapter's normalized result.

No external call is made by the shared runner itself.

## 4. Failure classification

- AdapterExecutionError with an explicit known outcome: persist failed, blocked, or needs_reconciliation as supplied by the specialist.
- Any unexpected exception: NEEDS_RECONCILIATION. The runner cannot prove whether the adapter performed a side effect before failing.
- Invalid result envelope: NEEDS_RECONCILIATION.
- Lease expiry or process crash: reconciler moves the task to NEEDS_RECONCILIATION.
- No automatic retry, fallback adapter, or alternate provider selection.

The exception message is deliberately not copied into the event details because it may contain source data or secrets. Only the exception type and a stable reason code are recorded.

## 5. Trust and security boundary

Adapters are trusted local code, not sandboxed plugins. Loading an adapter is not an authorization grant. The adapter must independently enforce:
- source-of-truth and input integrity checks;
- exact target, module, product class, and action mapping;
- operator approval and signature/revocation checks where required;
- service-account permissions and allowed environment;
- duplicate search, idempotency, remote readback, and stop/reconciliation rules;
- data minimization and secure audit storage.

The shared TaskContext contains no provider credentials, cookies, browser profile, private signing key, or approval token. Task payloads should carry references and non-secret metadata, not raw secrets or private documents.

## 6. Deliberate limitations

- Synchronous in-process execution only; no sandbox, subprocess isolation, heartbeat supervisor, cancellation, or async runtime.
- No ChatGPT, Claude, Codex, CMS, Drive, Meta, Canva, QLVB, or photo-editing adapter is shipped.
- Current registry has no active workflow agent, so the runner cannot execute a real specialist task.
- Photo Batch remains an application registration, not a workflow agent.
- CMS Batch remains candidate/disabled pending the separate staging and authority gates.

A production runner requires process isolation, worker identity, lease renewal/heartbeat, scoped authorization, and independent security review.
