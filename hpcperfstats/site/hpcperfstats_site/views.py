"""Views for the main site: React SPA shell and API-key management page."""
import os
import secrets

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.views.generic import View

from hpcperfstats.site.machine.models import ApiKey
from hpcperfstats.site.machine.oauth2 import check_for_tokens


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
            response = HttpResponse(f.read(), content_type="text/html")
            response["Cache-Control"] = "public, max-age=300"
            return response


def api_key_page(request):
    """Simple HTML page to create or view an API key for the logged-in user.

    Requires OAuth2 authentication; if not authenticated, redirects to
    /login_prompt with next set to this page. On first visit a new API key is
    created for the user (or reuses the most recent active key).
    """
    if not check_for_tokens(request):
        return HttpResponseRedirect("/login_prompt?next=/api-key/")

    username = request.session.get("username") or "unknown"
    # Reuse the most recent active key if one exists; otherwise create a new one
    key_obj = (
        ApiKey.objects.filter(username=username, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if key_obj is None:
        # 32 bytes -> 43-44 URL-safe chars; store as hex for readability
        new_key = secrets.token_hex(32)
        key_obj = ApiKey.objects.create(username=username, key=new_key)

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HPCPerfStats API key</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; }}
    code {{ padding: 0.2rem 0.4rem; background: #f5f5f5; border-radius: 4px; }}
    .box {{ border: 1px solid #ddd; border-radius: 6px; padding: 1rem 1.5rem; max-width: 640px; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>HPCPerfStats API key</h1>
    <p>Signed in as: <strong>{username}</strong></p>
    <p>Your API key for programmatic access is:</p>
    <p><code>{key_obj.key}</code></p>
    <p>Store this key securely. You can use it with the <code>jobstats_cli.py</code>
    tool by setting the <code>HPCPERF_API_KEY</code> environment variable or passing
    <code>--api-key</code> on the command line.</p>
  </div>
</body>
</html>
"""
    return HttpResponse(body, content_type="text/html")
