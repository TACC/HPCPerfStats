# Security audit memo (HPCPerfStats)

Internal reference for security posture review. **Last reviewed:** 2026-08-05 (Burp Suite report disposition + nginx HSTS/OCSP/CSP edge hardening; prior frontend stack posture 2026-07-31; Dependabot npm sweep 2026-07-26).

## Executive summary

HPCPerfStats combines a Django + DRF backend, a **Next.js static-export React SPA** (strict **TypeScript**, **OpenAPI → Orval → Zod** API contract), session-based OAuth (Tapis) and hashed API keys, nginx TLS termination, Redis caching, and PostgreSQL. Transport and cookie flags are generally aligned with production hardening. Highest-priority controls from this memo are now implemented: runtime dependency floor bumps in `pyproject.toml`, production-context audit workflow in CI (`.github/workflows/security-audit.yaml` + `tests/run_security_audit_workflow.sh`), DRF throttling for expensive/staff-ingest APIs, bounded `sacct_ingest` body size, session idle/absolute timeout + token refresh/validation behavior in OAuth helper logic, production CORS fail-fast validation, and SPA-side CSRF fail-closed + runtime response validation (F10–F12).

## Methodology

1. Lightweight threat model: assets (sessions, API keys, DB, ingest payloads), trust boundaries (browser → nginx → Django → data stores), adversaries (anonymous abuse, authenticated users, stolen staff API keys).
2. **pip-audit** inside the production **web** Docker image (`docker run --rm hpcperfstats pip-audit`, 2026-06-05): **no known vulnerabilities** in installed runtime deps (Django 6.0.6, cryptography 48.0.0, requests 2.34.2, pillow 12.2.0). Host `.venv` freeze (dev/test extras) still reports low-severity **idna** / **pip** advisories not present in the production image.
3. **npm audit** in `hpcperfstats/site/frontend` (2026-08-03): **0** reported vulnerabilities after pin refresh — raised floors plus new `overrides` for `hono`, `ip-address`, and `undici` (clears ReDoS / SSRF / undici advisories in the shadcn/jsdom tree). Prior 2026-07-26: **0** after Dependabot sweep — `next@^16.2.11` plus raised/added `overrides` for `dompurify`, `postcss`, `brace-expansion`, `fast-uri`, `sharp`, and `@hono/node-server`. Prior 2026-06-15: **0** after `dompurify` / `js-yaml` overrides.
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

`pyproject.toml` runtime floors: `Django>=6.0.7,<6.1` (cap below 6.1 until DRF ships Django 6.1 `cc_delim_re` compatibility), `requests>=2.34.2`, `cryptography>=49.0.0`, `pillow>=12.2.0`.

**Host dev venv** (not shipped): `idna` 3.13 (CVE-2026-45409 → 3.15+), `pip` 26.1.1 (PYSEC-2026-196 → 26.1.2+). Treat as developer-workstation hygiene only.

**Workflow note:** `tests/run_security_audit_workflow.sh` uses `docker-compose run web`. On machines with a local (gitignored) `docker-compose.app.yaml` but no `/opt/hpcperfstats_data/` bind mount, compose can fail before `pip-audit` runs. CI (no app overlay) is unaffected; direct `docker run --rm hpcperfstats pip-audit` is a valid local fallback.

### npm audit (frontend)

**2026-08-05:** Docker `frontend-builder` upgrades to **npm 12.0.2** before `npm ci` (stock Node 26 image still ships npm 11). npm 12 blocks dependency lifecycle scripts unless listed in `package.json` `allowScripts` (currently pinned `esbuild@0.28.1` only). Aligns with Shai-Hulud / install-time worm defenses. Same day: reverted accidental `js-yaml@^5` override to **`4.3.1`** (Orval default-import breakage in `build:prod`).

**2026-08-03:** 0 vulnerabilities (686 packages audited). Pin refresh raised direct floors (`next@^16.3.0`, `@bokeh/bokehjs@3.9.2`, etc.) and added/raised **`overrides`** for `hono@^4.13.0`, `ip-address@^10.4.0`, `undici@^7.29.0` (plus existing transitive floors). `npm install`, `npm audit`, `typecheck:all`, and Vitest (584) verified. Log: [`test_runs/dependency_pin_refresh_2026-08-03.md`](../test_runs/dependency_pin_refresh_2026-08-03.md).

**2026-07-26:** 0 vulnerabilities (690 packages audited). Remediation for GitHub Dependabot open alerts: direct **`next@^16.2.11`** (lock **16.2.12**) plus **`overrides`** floors — `dompurify@^3.4.12`, `postcss@^8.5.18`, `brace-expansion@^5.0.8`, `fast-uri@^3.1.4`, `sharp@^0.35.0`, `@hono/node-server@^2.0.5` (with existing `esbuild` / `js-yaml` / `lodash`). `npm ci`, `npm audit`, `typecheck:all`, and Vitest (562) verified. Log: [`test_runs/dependabot_npm_security_2026-07-26.md`](../test_runs/dependabot_npm_security_2026-07-26.md).

**2026-06-15:** 0 vulnerabilities (774 packages audited). Remediation: added **`overrides`** for `dompurify@^3.4.10` (Bokeh transitive XSS advisories) and `js-yaml@^4.2.0` (Orval dev-time YAML parse DoS; `orval@7.21.0` still declares `4.1.1`). `npm ci` and `npm run generate:api` verified clean.

**2026-06-12:** 0 vulnerabilities (577 packages audited). Remediation: `npm` **`overrides`** in [`hpcperfstats/site/frontend/package.json`](../hpcperfstats/site/frontend/package.json) to force patched transitive versions without breaking Orval 7.x / Next 15.x majors. Verification log: [`test_runs/npm_audit_remediation_2026-06-12.md`](../test_runs/npm_audit_remediation_2026-06-12.md).

| Override | Reason |
|----------|--------|
| `dompurify@^3.4.13` | GHSA-c2j3-45gr-mqc4 custom-element sanitize bypass; prior XSS / IN_PLACE advisories (`@bokeh/bokehjs`) |
| `js-yaml@4.3.1` | GHSA-h67p-54hq-rp68 merge-key DoS (`orval` codegen). **Stay on 4.x** — Orval uses `import … from "js-yaml"`; js-yaml 5 has no default export and breaks `npm run generate:api` / Docker `build:prod`. |
| `esbuild@^0.28.1` | GHSA-gv7w-rqvm-qjhr, GHSA-g7r4-m6w7-qqqr (Orval + Vitest/Vite tree) |
| `postcss@^8.5.25` | GHSA-r28c-9q8g-f849 source map path traversal (Next nested postcss) |
| `lodash@^4.18.1` | GHSA-r5fr-rjxr-66jc, GHSA-f23m-r3pf-42rh (`@stoplight/spectral-functions` via Orval) |
| `brace-expansion@^5.0.9` | GHSA-mh99-v99m-4gvg / CVE-2026-14257 DoS OOM |
| `fast-uri@^3.1.5` | GHSA-v2hh-gcrm-f6hx / CVE-2026-16221 host confusion |
| `sharp@^0.35.3` | GHSA-f88m-g3jw-g9cj nested libvips CVEs (via `next`) |
| `@hono/node-server@^2.0.12` | GHSA-frvp-7c67-39w9 Windows path traversal (via `@modelcontextprotocol/sdk` / shadcn) |
| `hono@^4.13.0` | GHSA-8j4g-w8fx-2239 CORS ReDoS (via MCP SDK / shadcn) |
| `ip-address@^10.4.0` | GHSA-mwp4-54f8-5fhr / related SSRF trust-boundary issues (express-rate-limit tree) |
| `undici@^7.29.0` | GHSA-8xcm-r25x-g524 and related undici 7.0–7.28 advisories (jsdom / shadcn) |

### bandit

No high-severity issues. Production-code medium B608 locations (2026-06-05): `analysis/metrics/lib/gen/jid_table.py`, `analysis/metrics/live_host_sample_count.py`, `analysis/metrics/update_metrics.py`, `site/lib/machine/artifact_readiness_expressions.py`. Treat as “verify no user-controlled identifiers” when editing those modules.

## Findings table

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| F1 | High | Dependencies | Runtime dependency floor raised (`Django>=6.0.4,<7`, `requests>=2.33.0`, `cryptography>=46.0.7`, `pillow>=12.2.0`) and production-context `pip-audit` + `npm audit` gate added. | **Fixed** |
| F2 | Medium | AuthN | `check_for_tokens` now enforces session idle/absolute limits, periodic token validation, and refresh-token fallback. | **Fixed** |
| F3 | Medium | Abuse | DRF throttles added for authenticated baseline + expensive routes + staff ingest; `sacct_ingest` now enforces app-level request-size limit. | **Fixed** |
| F4 | Medium | Config | `CORS_ALLOWED_ORIGINS` now parses env and fails fast in production if unset or using dev localhost origins. | **Fixed** |
| F5 | Low | CSP | Enforced CSP still includes `unsafe-eval` on Bokeh-heavy **machine** and **pub** SPA HTML; nginx hash-based CSP removes `unsafe-inline` for Next static shells; JSON/redirects use `script-src 'none'; style-src 'none'`. Report-only policy also omits unsafe-inline. CustomJS removed from plot pipeline (Python pre-formatted hovers). | **Mostly mitigated** (Bokeh `unsafe-eval` retained, documented) |
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

**Residual SPA risks (unchanged by the migration):** Bokeh still needs path-scoped `unsafe-eval` CSP on **machine** and **pub** SPA HTML (F5); Zod only helps for **registered** routes — unregistered endpoints skip runtime parse and must not be added casually; OpenAPI/Orval drift remains a process risk if schema regen is skipped.

## Burp Suite report disposition (2026-08-05)

Source: operator scan PDF `2026-08-05-hpcperfstats04.pdf` against the public TLS site (1 Low + 26 Informational instances across ten categories). Disposition below is evidence-based against current code and live headers.

| # | Finding | Security issue? | Evidence / defenses | Remediation (2026-08-05) | Residual risk |
|---|---------|-----------------|---------------------|--------------------------|---------------|
| 1 | Strict transport security not enforced | **Yes (valid)** | Django already sent HSTS on proxied routes; nginx-owned `/machine/`, `/static/`, `/robots.txt` did not. | Edge `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every HTTPS nginx response (`nginx-edge-security-headers.inc`). Preload remains **off**. | Operators must merge TLS/OCSP directives into gitignored `nginx.conf`. |
| 2 | Reflected XSS in `grouping` / `period` | **Not directly exploitable; hardening gap** | Response is `application/json` + `nosniff`; payload was not HTML-executable. Error detail previously echoed rejected values. | Strict lazy-query allowlist; generic non-reflective 400/404 (`public_api.py`). | Future HTML renderers must keep SafeJSON / no reflection. |
| 3 | CSP allows untrusted script execution | **Yes (defense-in-depth)** | Policies allowed `unsafe-inline` (and Bokeh `unsafe-eval`). | Build-time SHA-256 hashes for **script-src**; no `script-src 'unsafe-inline'`; machine/pub keep justified `unsafe-eval` only; JSON/redirects use no-active CSP. | Bokeh `unsafe-eval` until CustomJS-free embed. |
| 4 | CSP allows untrusted style execution | **Yes (defense-in-depth)** | Same `unsafe-inline` style allowance. | Machine/pub SPA use path-justified `style-src 'self' 'unsafe-inline'` (omit style hashes so CSP3 does not ignore it) for BokehJS runtime `<style>` injection; script-src stays hash-only. App styles still prefer classes where possible. | Bokeh requires style `unsafe-inline` on SPA shells until a non-inline stylesheet path exists. |
| 5 | CORS | **No** | Same-origin `Access-Control-Allow-Origin`; production origins fail closed. | None (document only). | Misconfigured `CORS_ALLOWED_ORIGINS` still fails closed in prod. |
| 6 | Input returned in response | **Informational prerequisite** | Same as #2. | Closed by non-reflective validation. | None beyond #2. |
| 7 | Frameable SPA responses | **Yes (valid)** | SPA HTML bypassed Django `X-Frame-Options`. | nginx `X-Frame-Options: SAMEORIGIN` + CSP `frame-ancestors 'self'`. | Nested framing within same origin remains allowed by design. |
| 8 | DOM data manipulation | **False positive for shown flow** | Sink is Next `history.replaceState` via `URLSearchParams`, not `innerHTML`. | Documented; retain URL helper tests. | New sinks that write HTML must stay escaped. |
| 9 | robots.txt | **Not a security issue** | Advertises public `/pub/` shells only; authZ is server-side. | None. | Do not treat robots as access control. |
| 10 | Cacheable HTTPS response | **Not a security issue here** | Anonymous pre-warmed public aggregates; intentional `max-age=120`; loading/errors use `no-store`. | None. | Do not cache authenticated responses publicly. |

### Live TLS / OCSP notes (operator discovery)

- Leaf certificate advertised OCSP AIA (`http://ocsp-c.emsign.com`) but the server previously sent **no** stapled response.
- Completed OCSP contract: `ssl_stapling` + `ssl_stapling_verify` + `ssl_trusted_certificate` (CA bundle) + runtime `resolver` include from `/etc/resolv.conf`.
- Certificates **without** an AIA OCSP URL (many modern Let’s Encrypt leafs) will not staple; that must not take the site offline.

## Positive controls (summary)

- `SECRET_KEY` required when `DEBUG` is false; dev-only default only when `DEBUG` is true.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` off HTTP-only cookies when not `DEBUG`.
- Session hardening includes configured idle/absolute timeout windows and OAuth token refresh/validation checks.
- **nginx** emits HSTS, framing, COOP, Permissions-Policy, Referrer-Policy, and CSP for public traffic; Django middleware remains defense-in-depth for direct Gunicorn.
- OCSP stapling completed when the leaf advertises an AIA responder (trusted CA bundle + runtime resolver).
- API key material hashed at rest; staff ingest requires `_require_staff`.
- API throttling is active for authenticated and expensive API routes, with stricter staff-ingest scope.
- SPA stack controls in **Frontend stack security posture** (static export, TypeScript, OpenAPI/Orval/Zod, CSRF mutator, no CDN, prod build boundary, hash-based edge CSP).
- `robots.txt` defaults to **`Disallow: /`** with explicit **`Allow:`** prefixes only for anonymous **`/pub/`** HTML shell paths (canonical registry in [`publicRobotsAllowPrefixes.js`](../hpcperfstats/site/frontend/src/config/publicRobotsAllowPrefixes.js), emitted by **`scripts/generate-robots-txt.mjs`** during the Next static-export build; nginx serves **`/robots.txt`** from **`STATIC_ROOT`**); **`/api/pub/`** remains uncrawlable by default.
- Anonymous **`GET /api/pub/cluster-dashboard/`** returns only pre-warmed JSON bundles; scoped DRF throttle **`public_cluster_dashboard`** limits abuse (`REST_FRAMEWORK` rate + env override; legacy `API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE` respected as fallback); lazy query params are allowlisted and non-reflective.
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
| 2026-08-03 | Dependency pin refresh: Bokeh 3.9.2, Next 16.3, Redis/Timescale/RabbitMQ image bumps; npm overrides for `hono` / `ip-address` / `undici`; `npm audit` 0 (686 pkgs); Vitest 584 green. |
| 2026-08-05 | Burp 2026-08-05 disposition: nginx-canonical HSTS/framing/CSP; OCSP trusted cert + runtime resolver; hash-based SPA CSP (no unsafe-inline); public dashboard non-reflective validation. |
