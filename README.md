# CMS Batch Agent — Safe-first MVP

Framework for preparing and verifying document uploads to a CMS. Designed for Codex Cloud development and offline regression tests. **Not connected to any live CMS yet.**

## What works

- Offline PDF source lock: verify file is PDF, restrict source root, SHA-256 check.
- Separate product classes: CSDL, TIN_VAN, TIN_BAI, TIN_SU_KIEN.
- Ed25519 signed operator approval: bind case, item fingerprint (including title, metadata and PDF hash), action, CMS module, HTTPS target origin and expiry (max 8 hours); executor holds only trusted PUBLIC verification key. The private signing key remains with the independent authorizer.
- TIN_VAN / articles: publish only with an additional exact-item permission; otherwise SAVE_DRAFT.
- CMS adapter contract: remote duplicate lookup before CREATE; compare fingerprint and downloaded attachment SHA; verify DRAFT before proceeding; verify PUBLISHED after step 1.
- HOLD for missing scope/conflicting record, NEEDS_RECONCILE for uncertain write outcome (never blindly retry).
- Mock CMS + unit tests; GitHub Actions offline-only; dry-run CLI does not instantiate a CMS adapter.
- LIVE runner adds private append+fsync audit events before/after each item and stops the batch immediately after any HOLD or uncertain remote write; local audit directory must be owner-only (0700).
- ExclusiveRunLock prevents two workers on the same machine; cross-machine/cloud concurrency requires a separately verified CMS-side idempotency key or distributed lock before production.
- Optional Playwright BrowserCMS adapter with private verified STAGING-only selector profile and auth snapshot. **Untested against target CMS; use only after staging validation.**

## Architecture

Chat / document preparation -> locked Batch manifest -> approval gate -> Codex Cloud execution -> CMS adapter -> remote readback -> scoped result.

Step 1 is about CMS submission/approval when expressly permitted for a specific document batch. Step 2 (public QA/QLVB completion report) is **not implemented** and must be requested separately. The agent does not sign documents or submit QLVB reports.

## Local setup

Install Python >=3.11:

    python -m pip install -e .
    python -m unittest discover -s tests -v

Generate a synthetic PDF (never use real government documents in CI):

    mkdir -p private/source
    printf '%%PDF-1.4\n%%synthetic\n%%%%EOF\n' > private/source/fictional.pdf
    python -c "import hashlib;print(hashlib.sha256(open('private/source/fictional.pdf','rb').read()).hexdigest())"

Copy examples/manifest.example.json into private/batch.json and replace file hash and paths. The source_root in that private manifest should be "source" when it sits inside private. Never commit private/ or .auth/.

Run the safe plan:

    cms-batch private/batch.json

This command is read-only and makes no CMS requests. A file check can fail closed until a real local test PDF and its exact SHA are provided.

## Browser integration (gated, not operational out of the box)

1. Investigate whether CMS has a supported API; prefer an officially supported API adapter if available.
2. Otherwise, inspect a staging CMS with a legitimate operator account. Capture the exact module URLs, selectors, status labels, fingerprint metadata field, and link that downloads the actual PDF.
3. Configure a private profile from examples/profile.example.json. All placeholder REPLACE values must be verified against that CMS. Provide an authenticated Playwright storage-state file **outside Git**. Never share cookies into an untrusted/public workspace.
4. Install optional browser support: pip install -e '.[browser]' and python -m playwright install chromium.
5. Generate an item-scoped Ed25519-signed approval using an INDEPENDENT trusted operator approval service, private key held entirely outside Codex and GitHub. Use canonical UTF-8 JSON per cms_agent.approval.canonical_bytes (sorted keys, compact separators, exclude signature), Ed25519 raw detached signature encoded Base64, schema_version=2. Keep the signed JSON outside Git. Supply only the approved public Ed25519 key as CMS_AGENT_APPROVAL_PUBLIC_KEY_B64 to the executor. A signature proves data integrity/approved key possession but does NOT replace authorized role checks in the CMS.
6. Only after staging regression and independent human sign-off should the operator deliberately enable a live run; the following is a reference command, NOT a recommendation to use it in production immediately:

    export CMS_AGENT_PROFILE=/secure/profile.json
    export CMS_AGENT_AUTH_STATE=/secure/auth-state.json
    export CMS_AGENT_ENABLE_LIVE=YES
    export CMS_AGENT_APPROVAL_ID=AN_EXACT_REVIEWED_APPROVAL_ID
    export CMS_AGENT_APPROVAL_PUBLIC_KEY_B64=OPERATOR_PUBLIC_ED25519_KEY_BASE64
    export CMS_AGENT_AUDIT_PATH=/secure/private-audit/events.jsonl
    cms-batch private/batch.json --live --approval-file /secure/approved-case.json --adapter cms_agent.browser.BrowserCMS

The browser workflow assumes a CMS supports a searchable document number, exact numeric total count matching search rows and a verified pagination control, visible title/metadata readback, a persistent fingerprint field, downloadable original PDF, verified intermediate Forward status and separate Forward and Approve controls. The provided BrowserCMS adapter is STAGING-ONLY and fails closed for production profiles pending a separate approved release. If any are missing, the adapter **must not be used** until adapted and reviewed for that particular CMS.

One profile represents ONE verified CMS module. Split mixed-product batches by module before execution. There is no production API URL/endpoint, selector map, or credential in this repository.

## Codex Cloud

Connect the AI-Agent repository to a Codex Cloud environment. Configure Python/test tools; keep network off for ordinary coding/test tasks. Add no production authentication until a private environment and explicit approval process are established. Cloud sessions do not inherit local Chrome login. For debugging, use synthetic fixtures first.

## Release checklist

- [x] Code + mock tests + fail-closed gate
- [ ] Production/staging API or selector discovery with authorization
- [ ] Real authenticated staging search, upload, readback and PDF byte hash verification
- [x] Ed25519 signature verification for exact case, module, action and item fingerprint (synthetic CI tests)
- [ ] Independent authorized private signing service, trusted key provisioning and revocation operational process
- [ ] Independent CMS Step 1 staging smoke test and independent review of audit retention / reconciliation plan
- [ ] Production allowlist, protected deployment environment and operator approval
- [ ] Separate Step 2 public QA / QLVB workflow

**Default release status: PILOT / NO_PRODUCTION_MUTATION.**

## Approval authority boundary

Signing and execution are separate trust domains. The operator (or secured approval service) reviews the case, exact item fingerprints from dry-run, CSDL/tin-vắn product, actions, module and expiry, and signs the canonical bytes offline using Ed25519. This repository deliberately does not include a production signer. Tests use ephemeral fake Ed25519 keys; those are not production credentials. Store and provision a trusted public key outside GitHub. A compromised signer or deployment environment still requires a separate security incident/revocation process.

An approval is not an authorization to use a CMS account whose permissions were never independently granted, and does not authorize Step 2, QLVB reports or signing administrative PDFs.
