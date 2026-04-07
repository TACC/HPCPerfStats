"""Views for the main site: React SPA shell and lightweight endpoints."""
import json
import os

from django.conf import settings
from django.http import HttpResponse
from django.views.generic import View
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST


class ReactSPAView(View):
    """Serve the built React app index.html so the SPA handles routing."""

    @staticmethod
    def _index_path_candidates():
        """Return SPA index lookup order, preferring STATIC_ROOT artifact output."""
        candidates = []
        static_root = getattr(settings, "STATIC_ROOT", "")
        if static_root:
            candidates.append(os.path.join(static_root, "frontend", "index.html"))
        static_dirs = getattr(settings, "STATICFILES_DIRS", ())
        if static_dirs:
            candidates.append(os.path.join(static_dirs[0], "frontend", "index.html"))
        return candidates

    def get(self, request, *args, **kwargs):
        """Serve the frontend index.html with cache headers."""
        candidates = self._index_path_candidates()
        if not candidates:
            return HttpResponse(
                "Static paths are not set.",
                status=503,
                content_type="text/plain",
            )
        index_path = next((p for p in candidates if os.path.isfile(p)), None)
        if not index_path:
            return HttpResponse(
                "Frontend not built. Run: cd frontend && npm run build",
                status=503,
                content_type="text/plain",
            )
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
            response = HttpResponse(html, content_type="text/html")
            # Never cache SPA shell HTML: it references hashed static asset names.
            # Cached stale index.html causes 404s for removed hash files after deploy.
            response["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response["Pragma"] = "no-cache"
            return response


@require_GET
def robots_txt(request):
    """Disallow all automated crawlers; this app is not meant to be indexed."""
    lines = [
        "User-agent: *",
        "Disallow: /",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_POST
def csp_report(request):
    """Receive CSP violation reports (Report-Only) for iterative hardening."""
    # Browsers may send either `application/csp-report` or `application/reports+json`.
    # We intentionally keep this lightweight: accept input and return 204.
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        if raw:
            json.loads(raw)
    except Exception:
        # Ignore malformed reports; do not leak details.
        pass
    return HttpResponse(status=204)
