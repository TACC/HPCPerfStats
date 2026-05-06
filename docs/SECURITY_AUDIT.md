# Security audit memo (HPCPerfStats)

Internal reference for security posture review. **Last reviewed:** 2026-05-06 (post-remediation update).

## Executive summary

HPCPerfStats combines a Django + DRF backend, session-based OAuth (Tapis) and hashed API keys, nginx TLS termination, Redis caching, and PostgreSQL. Transport and cookie flags are generally aligned with production hardening. Highest-priority controls from this memo are now implemented: runtime dependency floor bumps in `pyproject.toml`, production-context audit workflow in CI (`.github/workflows/security-audit.yaml` + `tests/run_security_audit_workflow.sh`), DRF throttling for expensive/staff-ingest APIs, bounded `sacct_ingest` body size, session idle/absolute timeout + token refresh/validation behavior in OAuth helper logic, and production CORS fail-fast validation.

## Methodology

1. Lightweight threat model: assets (sessions, API keys, DB, ingest payloads), trust boundaries (browser → nginx → Django → data stores), adversaries (anonymous abuse, authenticated users, stolen staff API keys).
2. **pip-audit** on host `.venv` `pip freeze` (2026-05-06): 23 known vulnerabilities in 10 packages (includes dev/test tools; see table below).
3. **npm audit** in `hpcperfstats/site/frontend` (2026-05-06): 0 reported vulnerabilities.
4. **bandit** (`-ll`) on `hpcperfstats/`: 8 medium findings, mostly low-confidence B608 on SQL fragment builders; manual review indicates identifiers come from internal constants, not request input.
5. Manual review of [`settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py), [`middleware.py`](../hpcperfstats/site/hpcperfstats_site/middleware.py), [`oauth2.py`](../hpcperfstats/site/machine/oauth2.py), [`api.py`](../hpcperfstats/site/machine/api.py) (auth and staff gates), [`views.py`](../hpcperfstats/site/hpcperfstats_site/views.py) (`csp_report`), nginx templates under [`services-conf/`](../services-conf/), and high-risk patterns (`subprocess`, `cursor.execute`).

## Automated scan snapshot (2026-05-06)

### pip-audit (full dev venv freeze)

Representative rows (upgrade targets depend on your pin policy; **Django** in `pyproject.toml` is `~=6.0` while this host venv showed 5.2.12—**reconcile deploy image with declared constraints**):

| Package        | Version (venv) | Notes |
|----------------|----------------|-------|
| django         | 5.2.12         | Multiple CVEs; fixed in 5.2.13+ / 6.0.4+ per advisory DB |
| cryptography   | 46.0.5         | Upgrade to 46.0.7+ |
| requests       | 2.32.5         | Upgrade to 2.33.0+ |
| pillow         | 12.1.1         | Upgrade to 12.2.0+ |
| black, pytest, pygments, mistune, nbconvert, tornado | various | Mostly dev/docs tooling; reduce risk by scanning **production** install only |

`pip-audit` against `pyproject.toml` alone failed locally when resolving `mysqlclient` (missing `pkg-config` on the audit host). **Recommendation:** run `pip-audit` inside the same Docker build stage that installs production dependencies.

### npm audit (frontend)

No vulnerabilities reported for the audited tree.

### bandit

No high-severity issues; medium B608 on dynamic SQL helpers—treat as “verify no user-controlled identifiers” during changes to those modules.

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

## Positive controls (summary)

- `SECRET_KEY` required when `DEBUG` is false; dev-only default only when `DEBUG` is true.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` off HTTP-only cookies when not `DEBUG`.
- Session hardening includes configured idle/absolute timeout windows and OAuth token refresh/validation checks.
- HSTS, `SECURE_PROXY_SSL_HEADER`, `Permissions-Policy`, `COOP`, CSP + CSP-Report-Only in middleware.
- API key material hashed at rest; staff ingest requires `_require_staff`.
- API throttling is active for authenticated and expensive API routes, with stricter staff-ingest scope.
- `robots.txt` disallows crawlers; `SafeJSONRenderer` avoids non-finite JSON floats.

## References

- Ongoing checklist: [SECURITY_REMEDIATION_BACKLOG.md](SECURITY_REMEDIATION_BACKLOG.md)
- Change triggers for developers/agents: [`hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc`](../hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc)

## History

| Date       | Change |
|------------|--------|
| 2026-05-06 | Initial memo, scans, findings table; CSP report CSRF fix documented. |
| 2026-05-06 | Closed F1/F2/F3/F4: dependency floor bumps, CI audit workflow, API throttles + ingest body cap, OAuth session/token hardening, and production CORS fail-fast checks. |
