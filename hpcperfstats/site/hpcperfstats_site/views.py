"""Views for lightweight site endpoints."""
import json

from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST


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
