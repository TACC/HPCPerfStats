from django.urls import path
from django.views.generic import DetailView, ListView
from hpcperfstats.site.machine.models import job_data
from hpcperfstats.site.machine.views import *

app_name = "hpcperfstats"

urlpatterns = [
                       path(r'', home, name='dates'),
                       path(r'job/<pk>/',
                           job_dataDetailView.as_view(), name='job_data'),
                       path(r'host/<host>/', host_detail, name='host_view'),
                       path(r'date/<end_time__date>', index, name='date_view'),
                       path(r'username/<username>/', index, name = 'username_view'),
                       path(r'account/<account>/'  , index, name = 'account_view'),
                       path(r'job/<jid>/<type_name>/', type_detail, name = 'type_detail'),
                       #path(r'proc/<pk>/<proc_name>/', proc_detail, name = 'proc_detail'),
                       #path(r'exe/<exe__icontains>)/', index, name='exe_view'),                       
                       path(r'search/',search, name='search'),
]

