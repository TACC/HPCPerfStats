# Security audit memo (HPCPerfStats)

Internal reference for security posture review. **Last reviewed:** 2026-06-05 (scheduled re-audit).

## Executive summary

HPCPerfStats combines a Django + DRF backend, session-based OAuth (Tapis) and hashed API keys, nginx TLS termination, Redis caching, and PostgreSQL. Transport and cookie flags are generally aligned with production hardening. Highest-priority controls from this memo are now implemented: runtime dependency floor bumps in `pyproject.toml`, production-context audit workflow in CI (`.github/workflows/security-audit.yaml` + `tests/run_security_audit_workflow.sh`), DRF throttling for expensive/staff-ingest APIs, bounded `sacct_ingest` body size, session idle/absolute timeout + token refresh/validation behavior in OAuth helper logic, and production CORS fail-fast validation.

## Methodology

1. Lightweight threat model: assets (sessions, API keys, DB, ingest payloads), trust boundaries (browser → nginx → Django → data stores), adversaries (anonymous abuse, authenticated users, stolen staff API keys).
2. **pip-audit** inside the production **web** Docker image (`docker run --rm hpcperfstats pip-audit`, 2026-06-05): **no known vulnerabilities** in installed runtime deps (Django 6.0.6, cryptography 48.0.0, requests 2.34.2, pillow 12.2.0). Host `.venv` freeze (dev/test extras) still reports low-severity **idna** / **pip** advisories not present in the production image.
3. **npm audit** in `hpcperfstats/site/frontend` (2026-06-05): 0 reported vulnerabilities.
4. **bandit** (`-ll`, excluding `*/tests/*`) on `hpcperfstats/` (2026-06-05): **no high** findings; **6** medium B608 on SQL fragment builders (same modules as prior review); manual review confirms table/column identifiers come from internal constants, not request input. One B108 on `wsgi.py` `MPLCONFIGDIR=/tmp/` (matplotlib cache path; accepted).
5. **Security regression tests** (host pytest, 2026-06-05): 9 passed (`test_settings_security`, throttles, API-key page, HTTP headers/cache); 5 compose-backed modules skipped/errored on host (`db` hostname). CI and compose workflows remain the gate for DB-dependent security tests.
5. Manual review of [`settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py), [`middleware.py`](../hpcperfstats/site/hpcperfstats_site/middleware.py), [`oauth2.py`](../hpcperfstats/site/machine/oauth2.py), [`api.py`](../hpcperfstats/site/machine/api.py) (auth and staff gates), [`views.py`](../hpcperfstats/site/hpcperfstats_site/views.py) (`csp_report`), nginx templates under [`services-conf/`](../services-conf/), and high-risk patterns (`subprocess`, `cursor.execute`).

## Automated scan snapshot (2026-06-05)

### pip-audit (production web image)

| Package      | Version (image) | Result |
|--------------|-----------------|--------|
| django       | 6.0.6           | Clean |
| cryptography | 48.0.0          | Clean |
| requests     | 2.34.2          | Clean |
| pillow       | 12.2.0          | Clean |
| gunicorn     | 26.0.0          | Clean |

`pyproject.toml` runtime floors: `Django>=6.0.5,<7`, `requests>=2.34.2`, `cryptography>=48.0.0`, `pillow>=12.2.0`.

**Host dev venv** (not shipped): `idna` 3.13 (CVE-2026-45409 → 3.15+), `pip` 26.1.1 (PYSEC-2026-196 → 26.1.2+). Treat as developer-workstation hygiene only.

**Workflow note:** `tests/run_security_audit_workflow.sh` uses `docker-compose run web`. On machines with a local (gitignored) `docker-compose.app.yaml` but no `/opt/hpcperfstats_data/` bind mount, compose can fail before `pip-audit` runs. CI (no app overlay) is unaffected; direct `docker run --rm hpcperfstats pip-audit` is a valid local fallback.

### npm audit (frontend)

No vulnerabilities reported for the audited tree.

### bandit

No high-severity issues. Production-code medium B608 locations (2026-06-05): `analysis/gen/jid_table.py`, `analysis/metrics/live_host_sample_count.py`, `analysis/metrics/update_metrics.py`, `site/machine/artifact_readiness_expressions.py`. Treat as “verify no user-controlled identifiers” when editing those modules.

## Findings table

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| F1 | High | Dependencies | Runtime dependency floor raised (`Django>=6.0.4,<7`, `requests>=2.33.0`, `cryptography>=46.0.7`, `pillow>=12.2.0`) and production-context `pip-audit` + `npm audit` gate added. | **Fixed** |
| F2 | Medium | AuthN | `check_for_tokens` now enforces session idle/absolute limits, periodic token validation, and refresh-token fallback. | **Fixed** |
| F3 | Medium | Abuse | DRF throttles added for authenticated baseline + expensive routes + staff ingest; `sacct_ingest` now enforces app-level request-size limit. | **Fixed** |
| F4 | Medium | Config | `CORS_ALLOWED_ORIGINS` now parses env and fails fast in production if unset or using dev localhost origins. | **Fixed** |
| F5 | Low | CSP | Enforced CSP includes `unsafe-inline` / `unsafe-eval` for Bokeh; accepted tradeoff—documented in project rules. | Accepted risk |
| F6 | Medium | Observability | `/csp-report/` returned **403** for browser-style POSTs when CSRF checks apply (no CSRF token on CSP reports). | **Fixed** (csrf_exempt + body cap; tests) |
| F7 | Low | API keys | Stored as SHA-256 of high-entropy raw key; consider a pepper if policy requires. | Open (optional) |
| F8 | Low | Subprocess/SQL | Ingest/archive uses subprocess and raw SQL with parameters; keep arguments non-user-controlled. | Ongoing review |
| F9 | Low | Operations | Local `run_security_audit_workflow.sh` can fail when gitignored `docker-compose.app.yaml` is present without `hpcperfstatsdata` host path; CI path unaffected. | Open (workflow hardening optional) |

## Positive controls (summary)

- `SECRET_KEY` required when `DEBUG` is false; dev-only default only when `DEBUG` is true.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` off HTTP-only cookies when not `DEBUG`.
- Session hardening includes configured idle/absolute timeout windows and OAuth token refresh/validation checks.
- HSTS, `SECURE_PROXY_SSL_HEADER`, `Permissions-Policy`, `COOP`, CSP + CSP-Report-Only in middleware.
- API key material hashed at rest; staff ingest requires `_require_staff`.
- API throttling is active for authenticated and expensive API routes, with stricter staff-ingest scope.
- `robots.txt` defaults to **`Disallow: /`** with explicit **`Allow:`** prefixes only for anonymous **`/pub/`** HTML shell paths (canonical registry in [`publicRobotsAllowPrefixes.js`](../hpcperfstats/site/frontend/src/config/publicRobotsAllowPrefixes.js), emitted at Vite build into static files; nginx serves **`/robots.txt`** from **`STATIC_ROOT`**); **`/api/pub/`** remains uncrawlable by default.
- Anonymous **`GET /api/pub/cluster-dashboard/`** returns only pre-warmed JSON bundles; scoped DRF throttle **`public_cluster_dashboard`** limits abuse (`REST_FRAMEWORK` rate + env override; legacy `API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE` respected as fallback).
- `SafeJSONRenderer` avoids non-finite JSON floats.

## References

- Ongoing checklist: [SECURITY_REMEDIATION_BACKLOG.md](SECURITY_REMEDIATION_BACKLOG.md)
- Change triggers for developers/agents: [`hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc`](../hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc)

## History

| Date       | Change |
|------------|--------|
| 2026-05-06 | Initial memo, scans, findings table; CSP report CSRF fix documented. |
| 2026-05-06 | Closed F1/F2/F3/F4: dependency floor bumps, CI audit workflow, API throttles + ingest body cap, OAuth session/token hardening, and production CORS fail-fast checks. |
| 2026-05-06 | Anonymous **`GET /api/pub/monthly-metrics/`** documented with scoped throttle + selective **`robots.txt`** `Allow` entries for **`/pub/`** HTML only. |
| 2026-05-07 | **`/pub/monthly-metrics`** and **`GET /api/pub/monthly-metrics/`** rebranded to **`/pub/cluster-dashboard`** and **`GET /api/pub/cluster-dashboard/`** (throttle scope **`public_cluster_dashboard`**; legacy env **`API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE`** still honored as fallback). |
| 2026-05-07 | **`robots.txt`**: nginx serves a Vite-built static file; Allow-list registry is **`publicRobotsAllowPrefixes.js`** (edge headers in **`nginx-edge-security-headers.inc`**). |
| 2026-06-05 | Scheduled re-audit: production-image **pip-audit** clean; **npm audit** clean; bandit unchanged (6 prod B608); dependency floors bumped in **`pyproject.toml`**; documented local compose-overlay audit workflow caveat (F9). |
