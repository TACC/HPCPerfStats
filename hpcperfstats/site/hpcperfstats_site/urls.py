from django.urls import include, path
from django.views.static import serve
from hpcperfstats.site.hpcperfstats_site import settings
from django.contrib import admin
from hpcperfstats.site.machine.views import home
from hpcperfstats.site.machine import urls
from hpcperfstats.site.machine.oauth2 import logout, login_oauth, oauth_callback, login_prompt

admin.autodiscover()
urlpatterns = [
    path(r'', home, name="dates"),
    path(r'machine/', include(urls, namespace = "machine"), name = "machine"),
    path(r'login/', login_oauth, name='login'),
    path(r'login_prompt', login_prompt, name='login_prompt'),
    path(r'logout/', logout, name='logout'),
    path(r'oauth_callback/', oauth_callback, name='oauth_callback'),
    path(r'media/<path>', serve, {'document_root': settings.MEDIA_ROOT}, name = "media"),                       
]

