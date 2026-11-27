# Security posture

This page lists the open security items, the status of each audit finding, the controls in place, and the scans that check them.
It is for developers and operators who review or change authentication, CSP, CORS, nginx, dependencies, raw SQL or subprocess code.

`security-posture-and-review-triggers.mdc` says which changes require a security review; this page holds the results.

## Threat model

- Assets: sessions, API keys, the database and ingest payloads.
- Trust boundaries: browser -> nginx -> Django -> data stores (PostgreSQL, Valkey, RabbitMQ).
- Adversaries: anonymous abuse, authenticated users, and stolen staff API keys.

## Open items

Items are ranked P0 (patch now), P1 (abuse resistance and operations) and P2 (hardening and hygiene).

| Priority | Item                                 | Status and action                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P0       | Dependency patches                   | None open.                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| P1       | Abuse resistance and operations      | None open.                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| P2       | `npm audit` residuals                | None: `npm audit` and `npm audit --omit=dev` in `hpcperfstats/site/frontend` report 0 findings. The lock holds `picomatch` 4.0.7 and 2.3.2 (dev tooling) and `sprintf-js` 1.1.3 (via `@bokeh/bokehjs`); `npm audit` flags neither. Never run `npm audit fix --force`: it downgrades Bokeh and breaks the Python and JS version lockstep.                                                                                                                                                |
| P2       | `deepmerge@4.3.1` (dev-only, no fix) | Dependabot [#121][dependabot-121] (GHSA-gcqx-w48m-w2h4, CVE-2026-93753, low) has no upstream release to move to. It is a transitive of `shadcn` / `@shadcn/registry` and of `@bokeh/bokehjs` -> `choices.js`, marked `"dev": true` in the lock; the vendored `bokeh.min.js` does not inline it (only `bokeh-widgets*` would). `test_deepmerge_stays_dev_only_for_dependabot_121` fails if it reaches runtime. Revisit if it becomes a runtime dependency or Bokeh widgets get vendored. |
| P2       | API key pepper (F7)                  | Optional: keys are stored as SHA-256 of a high-entropy raw key. Add a pepper only if policy requires one.                                                                                                                                                                                                                                                                                                                                                                               |
| P2       | Raw SQL and subprocess (F8)          | Ongoing review: when you edit a module that bandit flags (see [bandit](#bandit)), confirm that identifiers come from internal constants and arguments are not user-controlled.                                                                                                                                                                                                                                                                                                          |

## Findings

The audit findings F1-F12 and where each control lives:

| ID  | Severity | Area             | Current status                                                                                                                                                                                                                                                                                               |
| --- | -------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| F1  | High     | Dependencies     | Fixed. `pyproject.toml` sets runtime floors `Django>=6.1.3,<6.2`, `requests>=2.34.2`, `cryptography>=50.0.2` and `pillow>=13.0.0`; the [audit workflow](#scan-methods) runs `pip-audit` and `npm audit` in the production context.                                                                           |
| F2  | Medium   | Authentication   | Fixed. `check_for_tokens` in `oauth2.py` enforces `SESSION_IDLE_TIMEOUT_SECONDS` and `SESSION_ABSOLUTE_TIMEOUT_SECONDS`, validates the OAuth token periodically and falls back to the refresh token.                                                                                                         |
| F3  | Medium   | Abuse            | Fixed. DRF throttles cover authenticated requests, expensive reads and staff ingest (see [Throttles](#throttles)); `sacct_ingest` rejects bodies over `SACCT_INGEST_MAX_BODY_BYTES`.                                                                                                                         |
| F4  | Medium   | Configuration    | Fixed. `CORS_ALLOWED_ORIGINS` comes from the environment; outside `DEBUG`, `_validate_cors_allowed_origins` fails startup on an empty list or a dev localhost origin. `CSRF_TRUSTED_ORIGINS` copies the same list.                                                                                           |
| F5  | Low      | CSP              | Mostly mitigated. The `/machine/` and `/pub/` SPA HTML keeps `unsafe-eval` in `script-src` for Bokeh; script hashes replace `unsafe-inline` for scripts. Plots carry no CustomJS (hovers are pre-formatted in Python). Removing `unsafe-eval` needs a Bokeh embed without it.                                |
| F6  | Medium   | Observability    | Fixed. `/csp-report/` is `@csrf_exempt` with a 64 KiB body cap (`_CSP_REPORT_MAX_BYTES`); `test_csp_report_endpoint.py` covers both.                                                                                                                                                                         |
| F7  | Low      | API keys         | Open, optional (see [Open items](#open-items)).                                                                                                                                                                                                                                                              |
| F8  | Low      | Subprocess, SQL  | Ongoing review (see [Open items](#open-items)).                                                                                                                                                                                                                                                              |
| F9  | Low      | Operations       | Mitigated. `tests/run_security_audit_workflow.sh` runs on the test compose project with the settings and test overlay, so it needs no host bind paths from a site overlay.                                                                                                                                   |
| F10 | Medium   | CSRF             | Fixed. `_require_csrf_for_session_post` guards the session POST views `drop_staff_for_session`, `invalidate_cache_for_page`, `sacct_ingest`, `user_api_key_rotate` and `test_login_user`; `session_info` sets `csrftoken` with `@ensure_csrf_cookie`; the SPA fetch mutator fails closed without the cookie. |
| F11 | Medium   | Input validation | Fixed. `customFetch` parses responses with the Orval `@orval/zod` schemas; `bokehJsonItemSchema` checks Bokeh embeds.                                                                                                                                                                                        |
| F12 | Low      | XSS (href)       | Fixed. `isSafeHttpUrl` guards `client_url` and `server_url` on Job Detail; entity paths use `encodeURIComponent`.                                                                                                                                                                                            |

## Backend controls

- `SECRET_KEY` comes from the environment or `[DEFAULT] secret_key`; production needs one.
- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` are on whenever `DEBUG` is off.
- API key material is hashed at rest; staff ingest requires `_require_staff`.
- `SafeJSONRenderer` is the default DRF renderer and never emits non-finite floats.
- `[PORTAL] separate_test_login` (default `no`, development only) exposes the `/test-login/` form and staff `GET` / `POST /api/test-login/user/` to create one hashed singleton user.
  When off, both return 404, not 403.
  Its sessions carry a `test-login:` token prefix, so Tapis userinfo, refresh and revoke are skipped; passwords use Django `make_password`.
- Anonymous `GET /api/pub/cluster-dashboard/` returns pre-warmed JSON bundles only.
  Its lazy query parameters are allowlisted (`public_api.py`), and errors return a generic 400 or 404 that never echoes the rejected value.
  Extend the abuse review when a new `/api/pub/**` route ships.
- `robots.txt` is `Disallow: /` with `Allow:` lines only for the anonymous `/pub/` HTML shells.
  The prefixes live in `publicRobotsAllowPrefixes.ts`; `hpcperfstats/site/frontend/scripts/generate-robots-txt.mjs` emits the file during the static export and nginx serves it from `STATIC_ROOT`.
  `/api/pub/` stays uncrawlable, and robots is never access control.

### Throttles

The DRF rates in `settings.py` `REST_FRAMEWORK`, each overridable by an environment variable:

| Scope                           | Class                               | Environment override                                                                          |
| ------------------------------- | ----------------------------------- | --------------------------------------------------------------------------------------------- |
| `authenticated_user_or_api_key` | `AuthenticatedUserOrApiKeyThrottle` | `API_THROTTLE_AUTHENTICATED_RATE`                                                             |
| `expensive_read`                | `ExpensiveReadThrottle`             | `API_THROTTLE_EXPENSIVE_READ_RATE`                                                            |
| `staff_ingest`                  | `StaffIngestThrottle`               | `API_THROTTLE_STAFF_INGEST_RATE`                                                              |
| `public_cluster_dashboard`      | `PublicClusterDashboardThrottle`    | `API_THROTTLE_PUBLIC_CLUSTER_DASHBOARD_RATE`, else `API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE` |

API-key requests are throttled per key: `_request_api_key_fingerprint` keys the cache on a `salted_hmac` SHA-256 of the key, never the raw key.
When you remove a throttled view, move its 429 regression to the replacement endpoint (`test_api_security_throttles.py`).

## Edge headers and TLS

nginx owns the browser-facing headers on every HTTPS response; Django middleware repeats them as defense in depth for direct Gunicorn access.

- `nginx-edge-security-headers.inc` sends `Strict-Transport-Security: max-age=31536000; includeSubDomains` (preload off), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`, a deny-all `Permissions-Policy` and `X-Frame-Options: SAMEORIGIN`.
- CSP carries `frame-ancestors 'self'`; same-origin framing stays allowed by design.
- SPA shells use build-time SHA-256 hashes for `script-src`, with no `script-src 'unsafe-inline'`; `/machine/` and `/pub/` add `unsafe-eval` for Bokeh.
- `/machine/` and `/pub/` use `style-src 'self' 'unsafe-inline'` without style hashes (CSP3 ignores `unsafe-inline` next to a hash), because BokehJS injects `<style>` at runtime.
- JSON responses and redirects get `script-src 'none'; style-src 'none'`; non-Bokeh HTML shells (login) get a strict policy without `unsafe-eval`.
- Same-origin `Access-Control-Allow-Origin`; production origins fail closed (F4).
- Public aggregates are cacheable (`max-age=120`); loading and error responses use `no-store`. Never cache authenticated responses publicly.
- OCSP stapling uses `ssl_stapling`, `ssl_stapling_verify`, `ssl_trusted_certificate` (the CA bundle) and a runtime `resolver` include built from `/etc/resolv.conf`.
  A leaf without an AIA OCSP URL (many Let's Encrypt certificates) does not staple, and that must not take the site offline.
  Operators merge the TLS and OCSP directives into their gitignored `nginx.conf`.

## Frontend controls

The SPA is a Next.js static export in strict TypeScript, with API clients generated from OpenAPI.

| Control                        | What it does                                                                                                                                                                                                                                                                                          | Where                                                                  |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Static export, no Node in prod | `output: "export"`: nginx serves prebuilt files from `STATIC_ROOT`, so no Next.js server sits in the trust boundary and SSR or RSC server CVE classes do not apply.                                                                                                                                   | `next.config.ts`                                                       |
| Strict TypeScript              | `strict: true`; the production build typechecks `tsconfig.app.json` with `ignoreBuildErrors: false`.                                                                                                                                                                                                  | `tsconfig.json`, `next.config.ts`                                      |
| OpenAPI contract               | The committed `openapi/openapi.yaml` (drf-spectacular) defines the SPA routes; schema drift and wire-contract tests gate it, and hand-written `fetch` paths are discouraged.                                                                                                                          | `openapi-orval-sync.mdc`, `openapi-spa-wire-validation-contract.mdc`   |
| Orval codegen                  | `npm run generate:api` emits TanStack Query clients and Zod schemas (`generated/`, `generated-zod/`); the types are compile-time, the Zod schemas the runtime defense.                                                                                                                                | `orval.config.ts`                                                      |
| Runtime validation (F11)       | `customFetch` -> `parseApiResponse` with `response-schema-registry.ts` rejects unexpected payloads before rendering; Bokeh embeds use `bokehJsonItemSchema`.                                                                                                                                          | `fetch-mutator.ts`, `parse-api-response.ts`                            |
| Session CSRF (F10)             | Mutating requests throw without a `csrftoken` cookie and send `X-CSRFToken`; session calls use `credentials: "include"`.                                                                                                                                                                              | `fetch-mutator.ts`                                                     |
| Public fetch hygiene           | Anonymous public dashboard requests use `credentials: "omit"`, so no session cookie rides along.                                                                                                                                                                                                      | `fetchPubClusterDashboard`                                             |
| Href hygiene (F12)             | External Job Detail links pass `isSafeHttpUrl`; path segments use `encodeURIComponent`.                                                                                                                                                                                                               | `safe-external-url.ts`                                                 |
| No production CDNs             | Scripts, styles, fonts and icons are bundled or self-hosted (`@fontsource`, `lucide-react`, `@bokeh/bokehjs`).                                                                                                                                                                                        | `no-cdn-in-production.mdc`                                             |
| Prod and test build boundary   | `npm run build:prod` omits test-only export directories; `frontend/test/` is dockerignored and never imported from `app/` or `src/`.                                                                                                                                                                  | `frontend-prod-test-build-boundary.mdc`                                |
| Build telemetry off            | Image and rebuild paths set `NEXT_TELEMETRY_DISABLED=1`.                                                                                                                                                                                                                                              | `scripts/rebuild_frontend.sh`, `services-conf/pipeline_web.Dockerfile` |
| Install scripts blocked        | The `frontend-builder` stage pins npm 12+ (`NPM_VERSION`) before `npm ci`, so dependency lifecycle scripts run only for packages in `package.json` `allowScripts` (`esbuild@0.28.2`).                                                                                                                 | `pipeline_web.Dockerfile`, `package.json`                              |
| Dependency floors              | `package.json` `overrides` holds only `@xmldom/xmldom` (`^0.9.12`; it would otherwise resolve 0.9.10, below the 0.9.12 fix) and `dompurify` (`^3.5.0`, the Bokeh floor); lock-floor tests (`_assert_lock_copies_meet_floor`) fail if a fixed advisory package drops below its floor in any lock copy. | `test_dockerfile_frontend_builder_cache.py`                            |
| Build tooling is dev-only      | `shadcn`, the Tailwind and PostCSS chain and the `@bokeh/bokehjs` vendor source live in `devDependencies`.                                                                                                                                                                                            | `test_frontend_build_tooling_lives_in_dev_dependencies`                |

`js-yaml` has no override and stays on 4.x (lock 4.3.2): Orval imports its default export, which js-yaml 5 drops, breaking `npm run generate:api` and `build:prod`.

Residual SPA risks:

- Bokeh needs `unsafe-eval` on the `/machine/` and `/pub/` HTML (F5).
- Zod protects only registered routes; an unregistered endpoint skips runtime parsing, so register every new one.
- Skipping OpenAPI and Orval regeneration lets the contract drift.
- New sinks that write HTML must escape; the URL helpers write through `history.replaceState` and `URLSearchParams`, not `innerHTML`.

## Scan methods

Each scan, what it covers and how to run it:

| Scan                      | Covers                                                                                                                                                                                    | Run                                                                      |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Audit workflow            | `pip-audit` inside the compose `web` image and `npm audit` in the frontend                                                                                                                | `tests/run_security_audit_workflow.sh` (`--skip-build` reuses the image) |
| GitHub Actions            | The audit workflow weekly and on pull requests that touch dependency or audit files                                                                                                       | `.github/workflows/security-audit.yaml`                                  |
| `npm audit`               | Frontend dependency advisories, full tree and runtime only                                                                                                                                | `npm audit` and `npm audit --omit=dev` in `hpcperfstats/site/frontend`   |
| bandit                    | Python static analysis of production code                                                                                                                                                 | See [bandit](#bandit)                                                    |
| Security regression tests | Settings, throttles, API key page, HTTP headers and cache, CSP report, SPA CSP includes                                                                                                   | See [Security regression tests](#security-regression-tests)              |
| Dependabot and CodeQL     | GitHub dependency and code-scanning alerts on the default branch                                                                                                                          | The repository Security tab                                              |
| Manual review             | `settings.py`, `middleware.py`, `oauth2.py`, `api.py` auth and staff gates, `views.py` `csp_report`, nginx templates under `services-conf/`, `subprocess` and `cursor.execute` call sites | On every change `security-posture-and-review-triggers.mdc` lists         |

[Scan dependencies](../how-to/develop/run-tests.md#scan-dependencies) covers the host-only variants and the runner options.
The host `.venv` carries dev and test extras that never ship; treat advisories found only there as workstation hygiene.

### bandit

Run bandit at medium severity and above on production code:

```bash
../.venv/bin/pip install bandit
../.venv/bin/bandit -q -ll -r hpcperfstats \
  -x '*/tests/*,*/migrations/*,*/node_modules/*'
```

The expected findings, each reviewed:

| Test | Severity | Where                                                                                                                                                                                                                                                                    | Disposition                                                                                                 |
| ---- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| B324 | High     | `sync_timedb_job_store.py` `_tie_break_from_identity`                                                                                                                                                                                                                    | SHA-1 as a non-crypto score tie-break, marked `noqa: S324`.                                                 |
| B608 | Medium   | `columnar/copy_fetch.py`, `live_host_sample_count.py`, `metrics_host_data_sql.py`, `sync_timedb_proc_data_insert.py`, `admin_monitor_telemetry_health.py`, `artifact_readiness_expressions.py`, `cache_utils.py`, `host_data_latest.py`, `collapse_percore_host_data.py` | SQL fragment builders; table and column identifiers come from internal constants, never from request input. |
| B310 | Medium   | `listend.py`, `rabbitmq_watcher.py`                                                                                                                                                                                                                                      | `urlopen` on the RabbitMQ management API URL from configuration.                                            |
| B108 | Medium   | `wsgi.py`                                                                                                                                                                                                                                                                | `MPLCONFIGDIR=/tmp/` matplotlib cache path; accepted.                                                       |

A new finding outside this table needs review before merge.

### Security regression tests

These host tests guard the controls above; the compose workflows run the DB-dependent ones.

- `hpcperfstats/site/hpcperfstats_site/tests/test_settings_security.py`
- `hpcperfstats/site/hpcperfstats_site/tests/test_csp_report_endpoint.py`
- `hpcperfstats/site/lib/machine/tests/test_api_security_throttles.py`
- `hpcperfstats/site/lib/machine/tests/test_api_key_page_security.py`
- `hpcperfstats/site/lib/machine/tests/test_http_headers_security_cache.py`
- `hpcperfstats/tests/test_write_nginx_spa_csp_includes.py`
- `hpcperfstats/tests/test_dockerfile_frontend_builder_cache.py` (dependency floors and dev-only guards)

## Related

- [Run tests](../how-to/develop/run-tests.md)
- [Compose services](compose-services.md)
- [Architecture](../explanation/architecture.md)

[dependabot-121]: https://github.com/TACC/HPCPerfStats/security/dependabot/121
