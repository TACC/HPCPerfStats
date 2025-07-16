from django.urls import include, path
from django.views.static import serve
from hpcperfstats.site.hpcperfstats_site import settings
from django.contrib import admin
from hpcperfstats.site.machine.views import home
from hpcperfstats.site.machine import urls

admin.autodiscover()
urlpatterns = [
    path(r'', home, name="dates"),
    path(r'machine/', include(urls, namespace = "machine"), name = "machine"),
    path(r'media/<path>', serve, {'document_root': settings.MEDIA_ROOT}, name = "media"),                       
]

#try:
#from hpcperfstats.site.machine.agave_auth import login, logout, login_oauth, agave_oauth_callback, login_prompt
#print("starting TACC Stats with agave authentication")
#urlpatterns += [
#    path(r'login/', login_oauth, name='login'),
#path(r'login_prompt', login_prompt, name='login_prompt'),
#    path(r'logout/', logout, name='logout'),
#    path(r'agave_oauth_callback/', agave_oauth_callback, name='agave_oauth_callback')    
#]
#except: 
#    pass
