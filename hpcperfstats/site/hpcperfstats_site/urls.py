"""Root URL config: admin_monitor, machine app, login/logout/oauth_callback, media.

AI generated.
"""
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve

from hpcperfstats.site.hpcperfstats_site import settings
from hpcperfstats.site.machine import urls
from hpcperfstats.site.machine.oauth2 import (
    login_oauth,
    login_prompt,
    logout,
    oauth_callback,
)
from hpcperfstats.site.machine.views import admin_monitor, home

admin.autodiscover()
urlpatterns = [
    path(r'', home, name="dates"),
    path(r'admin_monitor/', admin_monitor, name='admin_monitor'),
    path(r'machine/', include(urls, namespace = "machine"), name = "machine"),
    path(r'login/', login_oauth, name='login'),
    path(r'login_prompt', login_prompt, name='login_prompt'),
    path(r'logout/', logout, name='logout'),
    path(r'oauth_callback/', oauth_callback, name='oauth_callback'),
    path(r'media/<path>', serve, {'document_root': settings.MEDIA_ROOT}, name = "media"),
]

