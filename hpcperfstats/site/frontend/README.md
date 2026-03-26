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

## Production build

Build outputs into Django static files:

```bash
npm run build
```

Output: `../hpcperfstats_site/static/frontend/`. Django serves the SPA for `/machine/` and `/machine/<path>`.

## Stack

- **Vite** – build and dev server
- **React 18** – UI
- **React Router 6** – client-side routes
- **Django REST Framework** – API under `/api/`
