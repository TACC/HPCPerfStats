# HPCPerfStats Frontend (Next.js + React)

This directory holds the Next.js 16 static-export SPA (React 19) that talks to the Django REST API under `/api/`.
It is for developers who build or change the web UI.

To build the full static export from this directory:

```bash
npm ci && npm run build
```

Tests, the production export, API client regeneration and the stack are in [Run tests](../../../docs/how-to/develop/run-tests.md#run-frontend-tests).

## Setup

On Rocky Linux, use the DNF-managed Node 24 stream.
Keep npm downloads, global tools, Playwright browsers and temporary files under `/data`:

```bash
sudo dnf module reset -y nodejs
sudo dnf module enable -y nodejs:24
sudo dnf install -y nodejs npm

export XDG_CACHE_HOME=/data/user/$USER/cache
export TMPDIR=/data/user/$USER/tmp
export npm_config_cache=/data/user/$USER/cache/npm
export npm_config_prefix=/data/user/$USER/tools/npm
export PLAYWRIGHT_BROWSERS_PATH=/data/user/$USER/cache/ms-playwright
npm ci
```

Persist those exports in the login environment before you run npm, npx or Playwright.
`node_modules` stays in this checkout under `/data/HPCPerfStats`.
Do not create growth-prone caches or tool installs under `$HOME` or `/tmp`.

## Development

Run the Next development server on port 3000:

```bash
npm run dev
```

Open <http://localhost:3000/machine/>.
`next.config.ts` defines no rewrites, so the development server does not proxy `/api` to Django; API calls use relative URLs on the page's origin.

In production and in the podman-compose stack with `proxy`, nginx serves `/static/*` from collected static files, not Django or Gunicorn.

## Production build

Production container builds use `npm run build:prod`, which omits test-only static export routes such as `bokeh-playwright-smoke/`.
The full export (`npm run build`) is for local development and CI before Playwright.
Both commands are in [Build the static export](../../../docs/how-to/develop/run-tests.md#build-the-static-export).

Output goes to `../hpcperfstats_site/static/frontend/`.
After `collectstatic`, nginx serves hashed files under `/static/` and the `index.html` shells for `/machine/` and `/pub/`.
Deep links fall back to the segment `index.html` (`services-conf/nginx-static-files.conf`); Django serves no SPA shell.

The Playwright Next-bundle check (third test in `test_bokeh_job_list_embed_browser_e2e.py`) needs `npm run build`, not `build:prod`, so `bokeh-playwright-smoke/index.html` is copied to `../hpcperfstats_site/static/frontend/`.
The smoke route is a Next App Router page used only for regression testing.
It is not linked from the production UI and is excluded from production deploy (`frontend-prod-test-build-boundary.mdc`).
