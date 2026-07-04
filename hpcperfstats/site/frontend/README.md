# HPCPerfStats Frontend (React + Vite)

React SPA that talks to the Django REST API. All data is loaded via AJAX (fetch with credentials).

## Setup

```bash
npm install
```

## Development

Run Vite dev server (proxies `/api` and auth URLs to Django):

```bash
npm run dev
```

Open http://localhost:5173/machine/ (or use Django’s URL with proxy). Ensure Django is running on port 8000.

**Note:** On this port, **Vite** serves `/static/frontend/` (dev bundles only). In production and in Docker Compose with **`proxy`**, **`/static/*`** is served by **nginx** from collected static files, not by Django or Gunicorn.

## Production build

Production deploy (Docker image, `rebuild_frontend.sh`) uses **`build:prod`**, which omits test-only static export routes (for example `bokeh-playwright-smoke/`):

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

- **Vite** – build and dev server
- **React 18** – UI
- **React Router 6** – client-side routes
- **Django REST Framework** – API under `/api/`
