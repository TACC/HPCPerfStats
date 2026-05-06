# Security remediation backlog

Prioritized follow-ups from [SECURITY_AUDIT.md](SECURITY_AUDIT.md). Update this file when items ship or priorities change.

## P0 — Patch and verify dependencies

- No open P0 items.

## P1 — Abuse resistance and operations

- No open P1 items.

## P2 — Hardening and hygiene

- No open P2 implementation tasks; remaining items are tracked as policy decisions and ongoing review notes in **Done**.

## Done

| Item | Notes |
|------|--------|
| CSP report CSRF | `/csp-report/` uses `@csrf_exempt` with 64 KiB body cap; regression tests in `hpcperfstats_site/tests/test_csp_report_endpoint.py`. |
| Dependency patch floor | `pyproject.toml` runtime constraints now enforce patched minimums for Django/requests/cryptography/pillow. |
| Security audit automation | `tests/run_security_audit_workflow.sh` and `.github/workflows/security-audit.yaml` run production-context `pip-audit` and frontend `npm audit` on PR/schedule. |
| API abuse controls | DRF throttles added (authenticated baseline, expensive reads, staff ingest) and covered by API regression tests. |
| Session/OAuth hardening | `check_for_tokens` now applies idle/absolute timeout plus token refresh/periodic validation logic; regression tests updated. |
| Production CORS guardrail | `CORS_ALLOWED_ORIGINS` now env-driven with production fail-fast checks (no empty list, no dev localhost origins). |
| CSP report storage decision | Keep endpoint lightweight no-op (validation + bounded body only) until retention requirements are formally requested. |
| API key pepper decision | Deferred by policy (optional); current SHA-256 + high-entropy key design retained and documented. |
| Bandit B608 review posture | Keep as ongoing review requirement when SQL helper modules change; no user-controlled identifiers accepted. |

## History

| Date       | Change |
|------------|--------|
| 2026-05-06 | Initial backlog; marked CSP report fix done. |
| 2026-05-06 | Closed all P0/P1 items from initial memo and moved them to Done with implementation notes. |
