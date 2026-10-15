# Codex Cloud — pilot setup for LUONMEDIA/AI-Agent

This repository can be attached to Codex Cloud by an authorized user via the Codex Cloud UI. GitHub access alone does NOT create a Codex Cloud environment.

## Environment
- Repository: LUONMEDIA/AI-Agent, branch feat/cms-batch-agent-safety-first while under review.
- Python >=3.11. Setup: python -m pip install -e . (offline tests can need package installs).
- Safe task: python -m unittest discover -s tests -v.
- Default command: cms-batch private/batch.json, which reads local synthetic PDF/manifest only and DOES NOT instantiate browser network.
- Do NOT add CMS production account, session state, cookie, approved PDF, internal admin URL, signing private key or internal case metadata to repository or coding workspace.
- GitHub Actions is an OFFLINE test runner only. LIVE is blocked explicitly in CI.

## Responsibility split
Chat validates/source-locks product cases and prepares private manifests. Cloud Codex develops/tests this open-source framework. A separately authorized operator in a secured execution environment runs an approved adapter once staging proofs, independent signed approval and monitoring are in place.

## Required staging evidence before any production cutover
1. Real admin module identity, official API availability or browser selectors from authorized read-only investigation.
2. An isolated staging CMS with non-sensitive synthetic PDF and fields.
3. Anti-duplicate search exhaustiveness; record ID and origin lock.
4. Save draft then readback signed attachment bytes; transition/approval separately verified.
5. Remote PDF SHA matches uploaded PDF, or documented server transforms with safe alternative proof.
6. Trusted external Ed25519 approval signer and public verification key managed independently of executor.
7. Private audit directory 0700, files 0600, incident and rollback plan.
8. Operator's exact BƯỚC 1 approval, STOP_POINT and State write/readback. No automatic BƯỚC 2 QLVB.
