# Security remediation backlog

Prioritized follow-ups from [SECURITY_AUDIT.md](SECURITY_AUDIT.md). Update this file when items ship or priorities change.

## P0 — Patch and verify dependencies

- No open P0 items.

## P1 — Abuse resistance and operations

- No open P1 items.

## P2 — Hardening and hygiene

- **Audit workflow compose overlay (F9):** optional hardening for `tests/run_security_audit_workflow.sh` — run `pip-audit` via `docker run --rm` on the built `hpcperfstats` image (no `hpcperfstatsdata` volume) so local machines with gitignored `docker-compose.app.yaml` do not block audits. CI behavior unchanged.

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
| Anonymous public metrics JSON | **`GET /api/pub/cluster-dashboard/`** is AllowAny + **`PublicClusterDashboardThrottle`** (`public_cluster_dashboard` rate; legacy env `API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE` falls back if unset); payloads are pre-warmed aggregates only—extend abuse review if new `/api/pub/**` routes ship. |
| CSRF server parity (F10) | `_require_csrf_for_session_post` on staff/session POST routes; `@ensure_csrf_cookie` on `session_info`; `CSRF_TRUSTED_ORIGINS` aligned with CORS; client mutator fail-closed; Django + Vitest regression tests. |
| Runtime API validation (F11) | Orval `@orval/zod` dual output (`generated-zod/`) + `parseApiResponse` in `customFetch`; `bokehJsonItemSchema` at Bokeh embed boundaries. |
| URL href safety (F12) | `isSafeHttpUrl` + `encodeURIComponent` on Job Detail entity/external links; Vitest coverage. |
| Bokeh CSP staging (F5) | Path-scoped CSP middleware; report-only without `unsafe-eval`; CustomJS hovers/ticks replaced with Python pre-formatting in analysis plots. |

## History

| Date       | Change |
|------------|--------|
| 2026-05-06 | Initial backlog; marked CSP report fix done. |
| 2026-05-06 | Closed all P0/P1 items from initial memo and moved them to Done with implementation notes. |
| 2026-05-07 | Anonymous public metrics Done row aligned with **`/api/pub/cluster-dashboard/`** throttle + legacy env fallback. |
| 2026-06-13 | Done rows for F10–F12 and partial F5 Bokeh CSP remediation. |
