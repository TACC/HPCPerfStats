# HPCPerfStats Frontend (Next.js + React)

Next.js 16 static-export SPA that talks to the Django REST API.

## Setup

On Rocky Linux, use the DNF-managed Node 24 stream. Keep npm downloads, global
tools, Playwright browsers, and temporary files under `/data`:

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

Persist those exports in the login environment before running npm, npx, or
Playwright. `node_modules` stays in this checkout under `/data/HPCPerfStats`;
do not create growth-prone caches or tool installs under `$HOME` or `/tmp`.

## Development

Run the Next development server (proxies `/api` and auth URLs to Django):

```bash
npm run dev
```

Open http://localhost:3000/machine/ (or use Django’s URL with proxy). Ensure
Django is running on port 8000.

**Note:** In production and the Podman Compose stack with **`proxy`**,
`/static/*` is served by nginx from collected static files, not Django or
Gunicorn.

## Production build

Production container builds use **`build:prod`**, which omits test-only static
export routes (for example `bokeh-playwright-smoke/`):

```bash
npm run build:prod
```

Full static export (local dev, CI before Playwright):

```bash
npm run build
```

Output: `../hpcperfstats_site/static/frontend/`. After `collectstatic`, nginx serves hashed files under `/static/`; Django’s **`ReactSPAView`** serves only the **`index.html`** shell for `/machine/` and `/machine/<path>`.

**Playwright Next-bundle check** (`test_bokeh_job_list_embed_browser_e2e.py` third test): run **`npm run build`** (not `build:prod`) so **`bokeh-playwright-smoke/index.html`** is copied to **`../hpcperfstats_site/static/frontend/`**. The smoke route is a Next App Router page used only for regression testing; it is not linked from production UI and is excluded from production deploy. See **`frontend-prod-test-build-boundary.mdc`**.

## Stack

- **Next.js 16 App Router** – build, development server, and static export
- **React 19** – UI
- **TanStack Query + Orval/Zod** – typed API state and validation
- **Django REST Framework** – API under `/api/`
