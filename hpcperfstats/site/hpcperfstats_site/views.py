"""Views for lightweight site endpoints."""
import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# Browsers POST CSP violation reports without a Django CSRF token; cap body size for DoS safety.
_CSP_REPORT_MAX_BYTES = 65536


@csrf_exempt
@require_POST
def csp_report(request):
    """Receive CSP violation reports (Report-Only) for iterative hardening."""
    # Browsers may send either `application/csp-report` or `application/reports+json`.
    # We intentionally keep this lightweight: accept input and return 204.
    if len(request.body or b"") > _CSP_REPORT_MAX_BYTES:
        return HttpResponse(status=204)
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        if raw:
            json.loads(raw)
    except Exception:
        # Ignore malformed reports; do not leak details.
        pass
    return HttpResponse(status=204)
