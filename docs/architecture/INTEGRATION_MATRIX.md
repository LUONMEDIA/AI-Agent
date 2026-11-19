# Specialist integration matrix

| Module | Current source | Classification | Registry state | What is proven | Remaining gate |
|---|---|---|---|---|---|
| Khánh Hòa CMS Batch Agent | PR #1, `feat/cms-batch-agent-safety-first` | Workflow agent | Candidate / disabled | PR reports 49/49 offline synthetic tests; source lock, signed scope, duplicate checks, readback design | Authorized staging access, actual create/upload/readback/forward/approve test, cross-host idempotency, independent security review, explicit cutover |
| Lượn Photo Batch | PR #3, `luon-photo-batch-v0.1` | Browser application | Candidate / disabled | Standalone browser app and core unit tests are proposed in PR | Review image correctness/performance on representative sets, accessibility, large-batch behavior, format roadmap, packaging and ownership |
| Marketing / Sales | Not yet identified in this repository | Future specialist | Not registered | No implementation evidence in this repository snapshot | Define owner, source-of-truth, task contract, permissions, QA and test suite before registration |
| Audio / Video | Not yet identified in this repository | Future specialist | Not registered | No implementation evidence in this repository snapshot | Same as above |
| SEO / Research | Not yet identified in this repository | Future specialist | Not registered | No implementation evidence in this repository snapshot | Same as above |
| Finance / Business | Not yet identified in this repository | Future specialist | Not registered | No implementation evidence in this repository snapshot | Same as above |

## Integration sequence

1. Merge/release the shared contracts and registry foundation only after review.
2. Rebase PR #1 and resolve its root `README.md` / `AGENTS.md` ownership: move CMS-specific instructions under `agents/cttdt/`; keep the repository root instructions provider-neutral.
3. Complete PR #1 staging acceptance independently. Do not enable the CMS registry entry until the operator and security gates are evidenced.
4. Rebase and review PR #3. Keep Photo Batch as an application adapter until a real task interface and lifecycle are implemented.
5. Add provider adapters and persistent state only after the interface, trust model, and deployment ownership are agreed.
6. Register future specialists only when code, owner, capability, and acceptance tests exist.

No step in this sequence authorizes production access or mutation.
