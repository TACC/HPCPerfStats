# Security audit memo (HPCPerfStats)

Internal reference for security posture review. **Last reviewed:** 2026-07-31 (frontend stack security posture documented after Mid-2026 SPA migration; prior Dependabot npm sweep 2026-07-26).

## Executive summary

HPCPerfStats combines a Django + DRF backend, a **Next.js static-export React SPA** (strict **TypeScript**, **OpenAPI → Orval → Zod** API contract), session-based OAuth (Tapis) and hashed API keys, nginx TLS termination, Redis caching, and PostgreSQL. Transport and cookie flags are generally aligned with production hardening. Highest-priority controls from this memo are now implemented: runtime dependency floor bumps in `pyproject.toml`, production-context audit workflow in CI (`.github/workflows/security-audit.yaml` + `tests/run_security_audit_workflow.sh`), DRF throttling for expensive/staff-ingest APIs, bounded `sacct_ingest` body size, session idle/absolute timeout + token refresh/validation behavior in OAuth helper logic, production CORS fail-fast validation, and SPA-side CSRF fail-closed + runtime response validation (F10–F12).

## Methodology

1. Lightweight threat model: assets (sessions, API keys, DB, ingest payloads), trust boundaries (browser → nginx → Django → data stores), adversaries (anonymous abuse, authenticated users, stolen staff API keys).
2. **pip-audit** inside the production **web** Docker image (`docker run --rm hpcperfstats pip-audit`, 2026-06-05): **no known vulnerabilities** in installed runtime deps (Django 6.0.6, cryptography 48.0.0, requests 2.34.2, pillow 12.2.0). Host `.venv` freeze (dev/test extras) still reports low-severity **idna** / **pip** advisories not present in the production image.
3. **npm audit** in `hpcperfstats/site/frontend` (2026-07-26): **0** reported vulnerabilities after Dependabot sweep — `next@^16.2.11` plus raised/added `overrides` for `dompurify`, `postcss`, `brace-expansion`, `fast-uri`, `sharp`, and `@hono/node-server` (clears 15 open GitHub Dependabot alerts once the lockfile is pushed). Prior 2026-06-15: **0** after `dompurify` / `js-yaml` overrides.
4. **bandit** (`-ll`, excluding `*/tests/*`) on `hpcperfstats/` (2026-06-05): **no high** findings; **6** medium B608 on SQL fragment builders (same modules as prior review); manual review confirms table/column identifiers come from internal constants, not request input. One B108 on `wsgi.py` `MPLCONFIGDIR=/tmp/` (matplotlib cache path; accepted).
5. **Security regression tests** (host pytest, 2026-06-05): 9 passed (`test_settings_security`, throttles, API-key page, HTTP headers/cache); 5 compose-backed modules skipped/errored on host (`db` hostname). CI and compose workflows remain the gate for DB-dependent security tests.
6. Manual review of [`settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py), [`middleware.py`](../hpcperfstats/site/hpcperfstats_site/middleware.py), [`oauth2.py`](../hpcperfstats/site/lib/machine/oauth2.py), [`api.py`](../hpcperfstats/site/lib/machine/api.py) (auth and staff gates), [`views.py`](../hpcperfstats/site/hpcperfstats_site/views.py) (`csp_report`), nginx templates under [`services-conf/`](../services-conf/), and high-risk patterns (`subprocess`, `cursor.execute`).
7. Frontend stack review (2026-06 migration + 2026-07-31 doc sync): committed OpenAPI schema, Orval client/Zod dual output, `customFetch` mutator, static-export / nginx delivery boundary, and production vs test build split — see **Frontend stack security posture** below.

## Automated scan snapshot (2026-06-05)

### pip-audit (production web image)

| Package      | Version (image) | Result |
|--------------|-----------------|--------|
| django       | 6.0.6           | Clean |
| cryptography | 48.0.0          | Clean |
| requests     | 2.34.2          | Clean |
| pillow       | 12.2.0          | Clean |
| gunicorn     | 26.0.0          | Clean |

`pyproject.toml` runtime floors: `Django>=6.0.6,<7`, `requests>=2.34.2`, `cryptography>=49.0.0`, `pillow>=12.2.0`.

**Host dev venv** (not shipped): `idna` 3.13 (CVE-2026-45409 → 3.15+), `pip` 26.1.1 (PYSEC-2026-196 → 26.1.2+). Treat as developer-workstation hygiene only.

**Workflow note:** `tests/run_security_audit_workflow.sh` uses `docker-compose run web`. On machines with a local (gitignored) `docker-compose.app.yaml` but no `/opt/hpcperfstats_data/` bind mount, compose can fail before `pip-audit` runs. CI (no app overlay) is unaffected; direct `docker run --rm hpcperfstats pip-audit` is a valid local fallback.

### npm audit (frontend)

**2026-07-26:** 0 vulnerabilities (690 packages audited). Remediation for GitHub Dependabot open alerts: direct **`next@^16.2.11`** (lock **16.2.12**) plus **`overrides`** floors — `dompurify@^3.4.12`, `postcss@^8.5.18`, `brace-expansion@^5.0.8`, `fast-uri@^3.1.4`, `sharp@^0.35.0`, `@hono/node-server@^2.0.5` (with existing `esbuild` / `js-yaml` / `lodash`). `npm ci`, `npm audit`, `typecheck:all`, and Vitest (562) verified. Log: [`test_runs/dependabot_npm_security_2026-07-26.md`](../test_runs/dependabot_npm_security_2026-07-26.md).

**2026-06-15:** 0 vulnerabilities (774 packages audited). Remediation: added **`overrides`** for `dompurify@^3.4.10` (Bokeh transitive XSS advisories) and `js-yaml@^4.2.0` (Orval dev-time YAML parse DoS; `orval@7.21.0` still declares `4.1.1`). `npm ci` and `npm run generate:api` verified clean.

**2026-06-12:** 0 vulnerabilities (577 packages audited). Remediation: `npm` **`overrides`** in [`hpcperfstats/site/frontend/package.json`](../hpcperfstats/site/frontend/package.json) to force patched transitive versions without breaking Orval 7.x / Next 15.x majors. Verification log: [`test_runs/npm_audit_remediation_2026-06-12.md`](../test_runs/npm_audit_remediation_2026-06-12.md).

| Override | Reason |
|----------|--------|
| `dompurify@^3.4.12` | GHSA-c2j3-45gr-mqc4 custom-element sanitize bypass; prior XSS / IN_PLACE advisories (`@bokeh/bokehjs`) |
| `js-yaml@^4.2.0` | GHSA-h67p-54hq-rp68 merge-key DoS (`orval` codegen) |
| `esbuild@^0.28.1` | GHSA-gv7w-rqvm-qjhr, GHSA-g7r4-m6w7-qqqr (Orval + Vitest/Vite tree) |
| `postcss@^8.5.18` | GHSA-r28c-9q8g-f849 source map path traversal (Next nested postcss) |
| `lodash@^4.18.1` | GHSA-r5fr-rjxr-66jc, GHSA-f23m-r3pf-42rh (`@stoplight/spectral-functions` via Orval) |
| `brace-expansion@^5.0.8` | GHSA-mh99-v99m-4gvg / CVE-2026-14257 DoS OOM |
| `fast-uri@^3.1.4` | GHSA-v2hh-gcrm-f6hx / CVE-2026-16221 host confusion |
| `sharp@^0.35.0` | GHSA-f88m-g3jw-g9cj nested libvips CVEs (via `next`) |
| `@hono/node-server@^2.0.5` | GHSA-frvp-7c67-39w9 Windows path traversal (via `@modelcontextprotocol/sdk` / shadcn) |

### bandit

No high-severity issues. Production-code medium B608 locations (2026-06-05): `analysis/metrics/lib/gen/jid_table.py`, `analysis/metrics/live_host_sample_count.py`, `analysis/metrics/update_metrics.py`, `site/lib/machine/artifact_readiness_expressions.py`. Treat as “verify no user-controlled identifiers” when editing those modules.

## Findings table

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| F1 | High | Dependencies | Runtime dependency floor raised (`Django>=6.0.4,<7`, `requests>=2.33.0`, `cryptography>=46.0.7`, `pillow>=12.2.0`) and production-context `pip-audit` + `npm audit` gate added. | **Fixed** |
| F2 | Medium | AuthN | `check_for_tokens` now enforces session idle/absolute limits, periodic token validation, and refresh-token fallback. | **Fixed** |
| F3 | Medium | Abuse | DRF throttles added for authenticated baseline + expensive routes + staff ingest; `sacct_ingest` now enforces app-level request-size limit. | **Fixed** |
| F4 | Medium | Config | `CORS_ALLOWED_ORIGINS` now parses env and fails fast in production if unset or using dev localhost origins. | **Fixed** |
| F5 | Low | CSP | Enforced CSP still includes `unsafe-eval` on Bokeh-heavy routes (`/api/jobs/`, `/api/host_plot/`); strict CSP (no `unsafe-eval`) on `/login_prompt`, `/pub/`, and other non-Bokeh API shells. Report-only policy omits `unsafe-eval` to stage tightening. CustomJS removed from plot pipeline (Python pre-formatted hovers). | **Partially mitigated** |
| F6 | Medium | Observability | `/csp-report/` returned **403** for browser-style POSTs when CSRF checks apply (no CSRF token on CSP reports). | **Fixed** (csrf_exempt + body cap; tests) |
| F7 | Low | API keys | Stored as SHA-256 of high-entropy raw key; consider a pepper if policy requires. | Open (optional) |
| F8 | Low | Subprocess/SQL | Ingest/archive uses subprocess and raw SQL with parameters; keep arguments non-user-controlled. | Ongoing review |
| F9 | Low | Operations | Local `run_security_audit_workflow.sh` can fail when gitignored `docker-compose.app.yaml` is present without `hpcperfstatsdata` host path; CI path unaffected. | Open (workflow hardening optional) |
| F10 | Medium | CSRF | Session-authenticated POST endpoints (`drop-staff`, `invalidate-cache`, `sacct_ingest`, `user-api-key/rotate`) now share `_require_csrf_for_session_post`; `session_info` sets `csrftoken` via `@ensure_csrf_cookie`; client mutator fails closed without cookie. | **Fixed** |
| F11 | Medium | Input validation | Orval `@orval/zod` response parsing at `customFetch` boundary; hand-written `bokehJsonItemSchema` at embed boundaries. | **Fixed** |
| F12 | Low | XSS (href) | `isSafeHttpUrl` guards `client_url` / `server_url` in Job Detail; entity paths use `encodeURIComponent`. | **Fixed** |

## Frontend stack security posture (Mid-2026 migration)

In **2026-06** the browser UI moved from a hand-rolled JS / Vite SPA to **Next.js (App Router) + strict TypeScript + OpenAPI-driven Orval clients**. Security value of that stack (not just DX):

| Control | What it does | Where |
|---------|--------------|--------|
| **Static export, no Node in prod** | `next.config.ts` uses `output: "export"`. Production serves prebuilt HTML/JS/CSS from nginx/`STATIC_ROOT` only — **no Next.js Node server** in the trust boundary, so SSR/RSC remote-code and Node CVE classes do not apply to the live site. | `next.config.ts`, nginx SPA shells for `/machine/` and `/pub/` |
| **Strict TypeScript** | `strict: true`; production typecheck via `tsconfig.app.json` (`ignoreBuildErrors: false`). Reduces classes of client bugs that become XSS, wrong-origin navigation, or silent misuse of privileged API fields. | `tsconfig.json`, `next.config.ts` |
| **OpenAPI as the API contract** | Committed [`openapi/openapi.yaml`](../hpcperfstats/site/openapi/openapi.yaml) (drf-spectacular) is the single source of truth for SPA-facing routes/shapes. Drift is gated by `test_openapi_schema_drift.py` and wire-contract tests — hand-written `fetch` paths that bypass auth/CSRF conventions are discouraged. | `openapi-orval-sync`, `openapi-spa-wire-validation-contract` |
| **Orval codegen (TanStack Query + Zod)** | `npm run generate:api` emits typed React Query clients **and** Zod response schemas (`generated/` + `generated-zod/`). Types alone are compile-time; Zod is the **runtime** defense. | `orval.config.ts` |
| **Runtime response validation (F11)** | `customFetch` → `parseApiResponse` + `response-schema-registry.ts` validates success JSON against Zod before UI consumption. Unexpected or hostile payloads fail closed instead of being rendered as trusted data. Bokeh embeds use a separate structural schema (`bokehJsonItemSchema`). | `fetch-mutator.ts`, `parse-api-response.ts` |
| **Session CSRF fail-closed (F10)** | Mutating methods require a `csrftoken` cookie or throw before the request; `X-CSRFToken` is attached when present. Session calls default to `credentials: "include"`. | `fetch-mutator.ts` |
| **Anonymous public fetch hygiene** | Public cluster-dashboard helpers use `credentials: "omit"` so session cookies are not attached to AllowAny public JSON. | `fetchPubClusterDashboard` |
| **Href / open-redirect hygiene (F12)** | External Job Detail links pass `isSafeHttpUrl`; path segments use `encodeURIComponent`. | `safe-external-url.ts` |
| **No production CDNs** | Scripts, styles, fonts, and icons are bundled / self-hosted (`@fontsource`, `lucide-react`, `@bokeh/bokehjs`). Removes third-party script trust and supply-chain CDN takeover risk. | `no-cdn-in-production` rule |
| **Prod vs test build boundary** | `npm run build:prod` omits test-only static export dirs (e.g. Playwright Bokeh smoke); `frontend/test/` is dockerignored and must not be imported from prod `app/`/`src/`. Shrinks what ships and what attackers can probe. | `frontend-prod-test-build-boundary` |
| **Build telemetry opt-out** | Frontend image/rebuild paths set `NEXT_TELEMETRY_DISABLED=1` so build tooling does not phone home. | `scripts/rebuild_frontend.sh`, Docker frontend builder |
| **Dependency floors** | npm `overrides` pin patched transitive packages (Orval/Next/Bokeh trees); CI + Dependabot keep `npm audit` clean (see snapshot above). | `package.json` `overrides` |

**Residual SPA risks (unchanged by the migration):** Bokeh still needs path-scoped `unsafe-eval` CSP on plot API shells (F5); Zod only helps for **registered** routes — unregistered endpoints skip runtime parse and must not be added casually; OpenAPI/Orval drift remains a process risk if schema regen is skipped.

## Positive controls (summary)

- `SECRET_KEY` required when `DEBUG` is false; dev-only default only when `DEBUG` is true.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` off HTTP-only cookies when not `DEBUG`.
- Session hardening includes configured idle/absolute timeout windows and OAuth token refresh/validation checks.
- HSTS, `SECURE_PROXY_SSL_HEADER`, `Permissions-Policy`, `COOP`, CSP + CSP-Report-Only in middleware.
- API key material hashed at rest; staff ingest requires `_require_staff`.
- API throttling is active for authenticated and expensive API routes, with stricter staff-ingest scope.
- SPA stack controls in **Frontend stack security posture** (static export, TypeScript, OpenAPI/Orval/Zod, CSRF mutator, no CDN, prod build boundary).
- `robots.txt` defaults to **`Disallow: /`** with explicit **`Allow:`** prefixes only for anonymous **`/pub/`** HTML shell paths (canonical registry in [`publicRobotsAllowPrefixes.js`](../hpcperfstats/site/frontend/src/config/publicRobotsAllowPrefixes.js), emitted by **`scripts/generate-robots-txt.mjs`** during the Next static-export build; nginx serves **`/robots.txt`** from **`STATIC_ROOT`**); **`/api/pub/`** remains uncrawlable by default.
- Anonymous **`GET /api/pub/cluster-dashboard/`** returns only pre-warmed JSON bundles; scoped DRF throttle **`public_cluster_dashboard`** limits abuse (`REST_FRAMEWORK` rate + env override; legacy `API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE` respected as fallback).
- `SafeJSONRenderer` avoids non-finite JSON floats.

## References

- Ongoing checklist: [SECURITY_REMEDIATION_BACKLOG.md](SECURITY_REMEDIATION_BACKLOG.md)
- Change triggers for developers/agents: [`hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc`](../hpcperfstats/cursor-rules/security-posture-and-review-triggers.mdc)
- Frontend contract rules: `openapi-orval-sync.mdc`, `openapi-spa-wire-validation-contract.mdc`, `frontend-stack-wiring-contract.mdc`, `frontend-prod-test-build-boundary.mdc`, `no-cdn-in-production.mdc`

## History

| Date       | Change |
|------------|--------|
| 2026-05-06 | Initial memo, scans, findings table; CSP report CSRF fix documented. |
| 2026-05-06 | Closed F1/F2/F3/F4: dependency floor bumps, CI audit workflow, API throttles + ingest body cap, OAuth session/token hardening, and production CORS fail-fast checks. |
| 2026-05-06 | Anonymous **`GET /api/pub/monthly-metrics/`** documented with scoped throttle + selective **`robots.txt`** `Allow` entries for **`/pub/`** HTML only. |
| 2026-05-07 | **`/pub/monthly-metrics`** and **`GET /api/pub/monthly-metrics/`** rebranded to **`/pub/cluster-dashboard`** and **`GET /api/pub/cluster-dashboard/`** (throttle scope **`public_cluster_dashboard`**; legacy env **`API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE`** still honored as fallback). |
| 2026-05-07 | **`robots.txt`**: nginx serves a build-emitted static file; Allow-list registry is **`publicRobotsAllowPrefixes.js`** (edge headers in **`nginx-edge-security-headers.inc`**). |
| 2026-06-05 | Scheduled re-audit: production-image **pip-audit** clean; **npm audit** clean; bandit unchanged (6 prod B608); dependency floors bumped in **`pyproject.toml`**; documented local compose-overlay audit workflow caveat (F9). |
| 2026-06-12 | SPA stack migration: Vite/JS → **Next.js static export + TypeScript**; OpenAPI/Orval client introduced; npm audit remediation via **`overrides`**. |
| 2026-06-13 | Frontend + API security remediation: F10 CSRF parity, F11 Orval Zod + Bokeh structural validation, F12 URL href guards; F5 CSP path-scoped + CustomJS removal (partial). |
| 2026-07-26 | Dependabot npm sweep: `next@^16.2.11` + transitive overrides; local `npm audit` 0 (690 pkgs); Vitest 562 green. |
| 2026-07-31 | Documented Mid-2026 frontend stack as security controls (static export, TypeScript, OpenAPI/Orval/Zod, CSRF mutator, no CDN, prod/test build boundary); corrected `robots.txt` build wording (Next export, not Vite). |
