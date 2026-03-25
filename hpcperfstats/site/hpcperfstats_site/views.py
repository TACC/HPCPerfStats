"""Views for the main site: React SPA shell and API-key management page."""
import json
import os

import bokeh
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.views.generic import View
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from hpcperfstats.site.machine.models import ApiKey
from hpcperfstats.site.machine.oauth2 import check_for_tokens


class ReactSPAView(View):
    """Serve the built React app index.html so the SPA handles routing."""

    BOKEH_VERSION_TOKEN = "{{ BOKEH_VERSION }}"

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
            # Keep JS CDN links aligned with the backend Bokeh package version.
            html = html.replace(self.BOKEH_VERSION_TOKEN, bokeh.__version__)
            response = HttpResponse(html, content_type="text/html")
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
    # Persist the user's staff status at key-creation time so API-key auth can
    # reliably reproduce staff vs non-staff behavior without re-running the
    # domain-based heuristic.
    is_staff = bool(request.session.get("is_staff", False))

    generated_api_key = None
    if request.method == "POST":
        # Invalidate all existing active keys for this (username, is_staff) pair
        # and create a fresh one.
        ApiKey.objects.filter(username=username, is_active=True, is_staff=is_staff).update(
            is_active=False
        )
        key_obj, generated_api_key = ApiKey.create_from_raw_key(
            username=username,
            is_staff=is_staff,
        )
    else:
        # Reuse the most recent active key if one exists; otherwise create a new one.
        key_obj = (
            ApiKey.objects.filter(username=username, is_active=True, is_staff=is_staff)
            .order_by("-created_at")
            .first()
        )
        if key_obj is None:
            key_obj, generated_api_key = ApiKey.create_from_raw_key(
                username=username,
                is_staff=is_staff,
            )

    csrf_token = get_token(request)
    if generated_api_key:
        key_message = (
            "<p>Your API key for programmatic access is:</p>"
            '<div class="api-key-row">'
            f'<code id="api-key-value">{generated_api_key}</code>'
            '<button type="button" id="copy-api-key" class="copy-api-key-button" aria-label="Copy API key">'
            "Copy"
            "</button>"
            "</div>"
            '<div id="api-key-copy-status" class="api-key-copy-status" aria-live="polite"></div>'
            "<p><strong>This key is shown only once.</strong> Store it securely now.</p>"
        )
    else:
        key_message = (
            "<p>You already have an active API key, and for security it cannot be shown again.</p>"
            f"<p>Active key prefix: <code>{key_obj.key_prefix}</code></p>"
            "<p>Use your saved copy, or rotate to generate a new key.</p>"
        )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HPCPerfStats API key</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; }}
    code {{
      padding: 0.2rem 0.4rem;
      background: #f5f5f5;
      border-radius: 4px;
      word-break: break-all;
      overflow-wrap: anywhere;
    }}
    .box {{ border: 1px solid #ddd; border-radius: 6px; padding: 1rem 1.5rem; max-width: 640px; width: 100%; }}
    .api-key-row {{ display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; margin-top: 0.5rem; }}
    .copy-api-key-button {{
      padding: 0.35rem 0.7rem;
      border: 1px solid #ced4da;
      border-radius: 6px;
      background: #fff;
      cursor: pointer;
      font-weight: 600;
    }}
    .copy-api-key-button[disabled] {{ opacity: 0.6; cursor: not-allowed; }}
    .api-key-copy-status {{ margin-top: 0.35rem; color: #444; font-size: 0.95rem; min-height: 1.25em; }}
    @media (max-width: 480px) {{ body {{ margin: 0.75rem; }} .box {{ padding: 1rem; }} }}
  </style>
</head>
<body>
  <div class="box">
    <h1>HPCPerfStats API key</h1>
    <p>Signed in as: <strong>{username}</strong></p>
    {key_message}
    <p>Store this key securely. You can use it with the <code>hpcperfstats-jobstats</code>
    and <code>hpcperfstats-sacct-gen</code> tools (from the hpcperfstats-tools package)
    by passing <code>--api-key</code> or using the cached key in <code>~/.hpcperfstats-api</code>.</p>
    <form method="post" style="margin-top: 1.5rem;">
      <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}" />
      <button type="submit">Invalidate and Create New Key</button>
    </form>
  </div>
  <script>
    (function () {{
      const copyBtn = document.getElementById("copy-api-key");
      const apiKeyEl = document.getElementById("api-key-value");
      const statusEl = document.getElementById("api-key-copy-status");
      if (!copyBtn || !apiKeyEl || !statusEl) return;

      async function writeToClipboard(text) {{
        if (navigator && navigator.clipboard && navigator.clipboard.writeText) {{
          await navigator.clipboard.writeText(text);
          return;
        }}
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }}

      copyBtn.addEventListener("click", async function () {{
        const key = (apiKeyEl.textContent || apiKeyEl.innerText || "").trim();
        if (!key) return;

        copyBtn.disabled = true;
        statusEl.textContent = "";
        try {{
          await writeToClipboard(key);
          statusEl.textContent = "Copied";
        }} catch (e) {{
          console.error("Failed to copy API key", e);
          statusEl.textContent = "Copy failed";
        }} finally {{
          copyBtn.disabled = false;
        }}
      }});
    }})();
  </script>
</body>
</html>
"""
    return HttpResponse(body, content_type="text/html")


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

