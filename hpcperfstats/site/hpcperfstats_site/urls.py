"""Root URL config: API, auth, and lightweight site endpoints.

Do **not** add ``django.conf.urls.static.static(..., document_root=..., )`` for
``settings.STATIC_URL`` or introduce **WhiteNoise** (or any middleware/view) to
answer ``/static/*`` in production: **nginx** serves that prefix from the
shared static volume (see ``services-conf/nginx-static-files.conf`` and
``docker-compose.yaml``). Django still runs ``collectstatic`` to populate
``STATIC_ROOT`` on disk; Gunicorn must not duplicate HTTP static serving.

Production **proxy** nginx forwards only an explicit allowlist of URL prefixes to
Gunicorn (same file). When adding a new **top-level** path here, extend that
allowlist or browsers will see **404** from nginx before Django runs. Project rule:
``hpcperfstats/cursor-rules/nginx-django-route-allowlist-sync.mdc``.
"""

from django.http import HttpResponseRedirect
from django.urls import include, path
from django.views.generic import RedirectView
from hpcperfstats.site.lib.machine.oauth2 import (
    login_oauth,
    login_prompt,
    logout,
    oauth_callback,
)
from hpcperfstats.site.hpcperfstats_site.views import (
    csp_report,
)

urlpatterns = [
    path("api/", include("hpcperfstats.site.lib.machine.api_urls")),
    path("csp-report/", csp_report, name="csp_report"),
    path("", lambda r: HttpResponseRedirect("/machine/")),
    path(
        "api-key/",
        RedirectView.as_view(url="/machine/api-key", permanent=False),
        name="api_key_redirect",
    ),
    path("admin_monitor/", lambda r: HttpResponseRedirect("/machine/admin_monitor/")),
    path("login/", login_oauth, name="login"),
    path("login_prompt", login_prompt, name="login_prompt"),
    path("logout/", logout, name="logout"),
    path("oauth_callback/", oauth_callback, name="oauth_callback"),
]
