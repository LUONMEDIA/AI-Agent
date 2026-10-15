# Khánh Hòa CMS — staged connector readiness checklist

STATUS: NOT_VERIFIED / NO_PRODUCTION_ACCESS. No real CMS URLs, credentials, selectors, document numbers or attachments appear here.

## Site-specific facts to verify, never guess
| Field | Evidence required | Status |
|---|---|---|
| Official/API endpoint and API permission | documented spec or authenticated supported read-only probe | UNKNOWN |
| CMS module for CSDL vs Tin vắn/Tin bài | operator-approved URL, title and known stable selector | UNKNOWN |
| Auth mechanism incl. SSO/MFA/session rotation | site admin + operator-approved integration method | UNKNOWN |
| Duplicate search completeness | search exact doc number, pagination and count evidence | UNKNOWN |
| Create/save draft workflow | staging screenshot/trace and readback | UNKNOWN |
| Upload PDF original bytes | permitted downloadable original and SHA-256 proof | UNKNOWN |
| Fingerprint field or idempotency key | persistent field/API support verified | UNKNOWN |
| Lưu → Chuyển tiếp → Duyệt/Xuất bản states | step-by-step staging readback | UNKNOWN |
| Error/retry reconciliation | timeout after save, re-search, no duplicate CREATE | UNKNOWN |
| Rate limits and bot/access policy | authorized CMS guidance | UNKNOWN |
| Step 2 hậu kiểm / QLVB | Separate executor, NOT in Step 1 | OUT_OF_SCOPE |

Never bypass CAPTCHA, MFA or CMS policy. Use authorized APIs or permitted browser automation; if forbidden, keep the agent for PREP/QA only. No live execution before human security review, trusted signature authority and module-specific test data.
