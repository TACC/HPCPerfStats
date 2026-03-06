"""Root URL config: admin_monitor, machine app, login/logout/oauth_callback, media.

AI generated.
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
urlpatterns = [
    path("api/", include("hpcperfstats.site.machine.api_urls")),
    path("", lambda r: HttpResponseRedirect("/machine/")),
    path("machine/", ReactSPAView.as_view()),
    path("machine/<path:path>", ReactSPAView.as_view()),
    path("admin_monitor/", lambda r: HttpResponseRedirect("/machine/admin_monitor/")),
    path(r'login/', login_oauth, name='login'),
    path(r'login_prompt', login_prompt, name='login_prompt'),
    path(r'logout/', logout, name='logout'),
    path(r'oauth_callback/', oauth_callback, name='oauth_callback'),
    path(r'media/<path>',
         serve, {'document_root': settings.MEDIA_ROOT},
         name="media"),
]
