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

    def get(self, request, *args, **kwargs):
        """Serve the frontend index.html with cache headers."""
        static_dirs = getattr(settings, "STATICFILES_DIRS", ())
        if not static_dirs:
            return HttpResponse(
                "STATICFILES_DIRS not set.",
                status=503,
                content_type="text/plain",
            )
        index_path = os.path.join(static_dirs[0], "frontend", "index.html")
        if not os.path.isfile(index_path):
            return HttpResponse(
                "Frontend not built. Run: cd frontend && npm run build",
                status=503,
                content_type="text/plain",
            )
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
            response = HttpResponse(html, content_type="text/html")
            response["Cache-Control"] = "public, max-age=300"
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
