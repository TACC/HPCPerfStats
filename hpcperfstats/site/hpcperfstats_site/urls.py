"""Root URL config: admin_monitor, machine app, login/logout/oauth_callback, media, static.

"""
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import include, path
from django.views.static import serve

from hpcperfstats.site.hpcperfstats_site import settings
from hpcperfstats.site.machine.oauth2 import (
    login_oauth,
    login_prompt,
    logout,
    oauth_callback,
)
from hpcperfstats.site.hpcperfstats_site.views import ReactSPAView

admin.autodiscover()

# Serve static files (e.g. built frontend JS/CSS) so the SPA loads when not using runserver
static_root = settings.STATICFILES_DIRS[0] if getattr(settings, "STATICFILES_DIRS", None) else None

urlpatterns = [
    path("api/", include("hpcperfstats.site.machine.api_urls")),
    path("", lambda r: HttpResponseRedirect("/machine/")),
    path("machine/", ReactSPAView.as_view()),
    path("machine/<path:path>", ReactSPAView.as_view()),
    path("admin_monitor/", lambda r: HttpResponseRedirect("/machine/admin_monitor/")),
    path("login/", login_oauth, name="login"),
    path("login_prompt", login_prompt, name="login_prompt"),
    path("logout/", logout, name="logout"),
    path("oauth_callback/", oauth_callback, name="oauth_callback"),
    path("media/<path:path>", serve, {"document_root": settings.MEDIA_ROOT}, name="media"),
]

if static_root:
    urlpatterns.append(
        path("static/<path:path>", serve, {"document_root": static_root}, name="static"),
    )
