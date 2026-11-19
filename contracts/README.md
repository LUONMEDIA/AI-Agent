# Shared contracts

These JSON Schemas are the platform boundary between AI clients, the shared core, specialist agents, and execution adapters.

- `task.schema.json`: a requested unit of work and its routing intent.
- `agent.schema.json`: a specialist's identity, capabilities, lifecycle, and execution boundary.
- `artifact.schema.json`: a reference to an output/source artifact with provenance and integrity metadata.
- `approval.schema.json`: an independently issued, narrowly scoped authorization reference.
- `event.schema.json`: a normalized lifecycle/audit event.

## Contract rules

1. Version schemas explicitly; incompatible changes require a new major contract version or a reviewed migration.
2. Pass artifact references and hashes rather than copying sensitive file contents into task payloads.
3. An approval reference is not a permission by itself. The specialist executor must verify issuer trust, exact scope, expiry, revocation, target, and its own account permissions.
4. A task's `requested_by` identifies the caller/runtime. It does not grant the caller authority.
5. Unknown fields may be rejected by a receiving specialist. Do not silently reinterpret unsupported task types or capabilities.
6. These schemas standardize envelopes; they do not replace specialist-specific validation or source-of-truth workflow rules.
