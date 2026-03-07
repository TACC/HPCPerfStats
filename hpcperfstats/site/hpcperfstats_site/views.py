"""SPA view: serve React app index.html for /machine and /admin_monitor."""
import os
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.views.generic import View


class ReactSPAView(View):
    """Serve the built React app index.html so the SPA handles routing."""

    def get(self, request, *args, **kwargs):
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
            response = HttpResponse(f.read(), content_type="text/html")
            response["Cache-Control"] = "public, max-age=300"
            return response
