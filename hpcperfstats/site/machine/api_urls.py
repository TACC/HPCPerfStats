"""URL routing for machine REST API."""
from django.urls import path
from . import api

urlpatterns = [
    path("session/", api.session_info),
    path("session/drop-staff/", api.drop_staff_for_session),
    path("cache/invalidate-page/", api.invalidate_cache_for_page),
    path("home/", api.home_options),
    path("search/", api.search_dispatch),
    path("jobs/", api.job_list),
    path("jobs/histograms/", api.job_list_histograms),
    path("jobs/<str:pk>/", api.job_detail),
    path("jobs/<str:pk>/plots/", api.job_plots),
    path("jobs/<str:jid>/<str:type_name>/", api.type_detail),
    path("host_plot/", api.host_plot),
    path("admin_monitor/", api.admin_monitor),
    path("job_monitor/", api.job_monitor),
    path("sacct/ingest/", api.sacct_ingest),
]
