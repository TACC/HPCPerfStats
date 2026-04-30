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

Build outputs into Django static files:

```bash
npm run build
```

Output: `../hpcperfstats_site/static/frontend/`. After `collectstatic`, nginx serves hashed files under `/static/`; Django’s **`ReactSPAView`** serves only the **`index.html`** shell for `/machine/` and `/machine/<path>`.

**Playwright Vite-bundle check** (`test_bokeh_job_list_embed_browser_e2e.py` third test): emit the extra multipage entry with **`npm run build:with-bokeh-playwright-smoke`** (sets `BUILD_BOKEH_SMOKE=1`). That adds **`bokeh-playwright-smoke.html`** to the same output dir; production Docker builds use default **`npm run build`** only, so the smoke page is not shipped.

## Stack

- **Vite** – build and dev server
- **React 18** – UI
- **React Router 6** – client-side routes
- **Django REST Framework** – API under `/api/`
