"""Legacy machine URLs: redirect to SPA so all plot pages use Bokeh embed_item fix."""
from django.http import HttpResponseRedirect
from django.urls import path

app_name = "hpcperfstats"


def _redirect(path_suffix, query=True):
    """Return a view that redirects to /machine/<path_suffix> with optional query string."""

    def view(request, **kwargs):
        base = "/machine/" + path_suffix.format(**kwargs)
        if query and request.GET:
            base = base + ("&" if "?" in base else "?") + request.GET.urlencode()
        return HttpResponseRedirect(base)

    return view


def _redirect_spa_index(request):
    return HttpResponseRedirect("/machine/")


def _redirect_host(request, host):
    """Redirect to SPA host plot page; preserve end_time__gte, end_time__lte."""
    base = f"/machine/host/{host}/plot"
    if request.GET:
        base = base + "?" + request.GET.urlencode()
    return HttpResponseRedirect(base)


urlpatterns = [
    path("", _redirect_spa_index, name="dates"),
    path("job/<str:pk>/", _redirect("job/{pk}", query=False), name="job_data"),
    path("host/<path:host>/", _redirect_host, name="host_view"),
    path("year/<str:year>/", _redirect("year/{year}", query=False), name="year_view"),
    path("date/<str:end_time__date>", _redirect("date/{end_time__date}", query=False), name="date_view"),
    path("username/<str:username>/", _redirect("username/{username}", query=False), name="username_view"),
    path("account/<str:account>/", _redirect("account/{account}", query=False), name="account_view"),
    path("queue/<str:queue>/", _redirect("queue/{queue}", query=False), name="queue_view"),
    path("job/<str:jid>/<str:type_name>/", _redirect("job/{jid}/{type_name}", query=False), name="type_detail"),
    path("search/", _redirect_spa_index, name="search"),
]
