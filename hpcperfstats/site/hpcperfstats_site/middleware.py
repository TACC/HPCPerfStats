"""Profiling middleware: add ?prof to a URL to profile the view (DEBUG only).

Optional query params:
- sort: pstats sort key (default "time")
- count: number of rows to show (default 100)
"""
import cProfile
import io
import pstats

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class ProfileMiddleware:
  """Simple profiling middleware for Django views (Django 3+/6+ style).

  Activated only when:
  - settings.DEBUG is True, and
  - the incoming request has a ?prof query parameter.
  """

  def __init__(self, get_response):
    self.get_response = get_response

  def _enabled(self, request: HttpRequest) -> bool:
    """Return True if profiling is enabled for this request."""
    return bool(settings.DEBUG and "prof" in request.GET)

  def __call__(self, request: HttpRequest) -> HttpResponse:
    if not self._enabled(request):
      return self.get_response(request)

    profiler = cProfile.Profile()
    try:
      response = profiler.runcall(self.get_response, request)
    except Exception:
      # Let Django's normal exception handling and middleware chain run.
      raise

    s = io.StringIO()
    stats = pstats.Stats(profiler, stream=s)
    sort_key = request.GET.get("sort", "time")
    try:
      count = int(request.GET.get("count", "100"))
    except (TypeError, ValueError):
      count = 100
    stats.strip_dirs().sort_stats(sort_key).print_stats(count)

    response.content = f"<pre>{s.getvalue()}</pre>"
    response["Content-Type"] = "text/plain; charset=utf-8"
    return response
