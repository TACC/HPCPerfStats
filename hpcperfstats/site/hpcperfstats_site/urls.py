"""Root URL config: admin_monitor, machine app, login/logout/oauth_callback, media, static.

"""
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import include, path
from django.views.generic import RedirectView
from hpcperfstats.site.machine.oauth2 import (
    login_oauth,
    login_prompt,
    logout,
    oauth_callback,
)
from hpcperfstats.site.hpcperfstats_site.views import (
    ReactSPAView,
    csp_report,
    robots_txt,
)

admin.autodiscover()

urlpatterns = [
    path("api/", include("hpcperfstats.site.machine.api_urls")),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("csp-report/", csp_report, name="csp_report"),
    path("", lambda r: HttpResponseRedirect("/machine/")),
    path("machine/", ReactSPAView.as_view()),
    path("machine/<path:path>", ReactSPAView.as_view()),
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
