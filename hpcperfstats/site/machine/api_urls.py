"""URL routing for machine REST API."""
from django.urls import path
from . import api

urlpatterns = [
    path("session/", api.session_info),
    path("home/", api.home_options),
    path("search/", api.search_dispatch),
    path("jobs/", api.job_list),
    path("jobs/histograms/", api.job_list_histograms),
    path("jobs/<str:pk>/", api.job_detail),
    path("jobs/<str:jid>/<str:type_name>/", api.type_detail),
    path("admin_monitor/", api.admin_monitor),
]
