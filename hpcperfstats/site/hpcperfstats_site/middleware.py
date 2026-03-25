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


DEFAULT_PERMISSIONS_POLICY = (
  "accelerometer=(), autoplay=(), bluetooth=(), camera=(), clipboard-read=(), "
  "clipboard-write=(), display-capture=(), encrypted-media=(), fullscreen=(), "
  "gamepad=(), geolocation=(), gyroscope=(), interest-cohort=(), magnetometer=(), "
  "microphone=(), midi=(), payment=(), picture-in-picture=(), "
  "publickey-credentials-get=(), screen-wake-lock=(), sync-xhr=(), usb=(), "
  "xr-spatial-tracking=()"
)

DEFAULT_COOP = "same-origin"

# Start with CSP Report-Only to avoid breaking existing pages. This policy is
# intentionally conservative (permits inline script/style) because some pages
# render inline assets (e.g. the API key page). Tighten over time using reports.
DEFAULT_CSP_REPORT_ONLY = (
  "default-src 'self'; "
  "base-uri 'self'; "
  "object-src 'none'; "
  "frame-ancestors 'self'; "
  "form-action 'self'; "
  "img-src 'self' data:; "
  "font-src 'self' data: https://fonts.gstatic.com; "
  "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.pydata.org; "
  "connect-src 'self'; "
  "upgrade-insecure-requests; "
  "report-uri /csp-report/;"
)


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


class DefaultCacheControlMiddleware:
  """Apply a consistent default cache policy.

  nginx currently overrides `Cache-Control` for all proxied requests. By moving
  the default behavior into Django, responses from gunicorn (direct or behind
  nginx) stay consistent, while views can still opt-in by explicitly setting
  `Cache-Control`.
  """

  def __init__(self, get_response):
    self.get_response = get_response

  def __call__(self, request: HttpRequest) -> HttpResponse:
    response = self.get_response(request)
    # If a view (or Django itself, e.g. some static handlers) has already set
    # Cache-Control, respect that decision.
    if "Cache-Control" not in response:
      response["Cache-Control"] = "no-store, no-cache"
    return response


class DefaultSecurityHeadersMiddleware:
  """Apply security headers that Django doesn't emit by default.

  Keep response header creation centralized in Django so behavior is consistent
  whether responses are served directly by gunicorn or behind a proxy.
  """

  def __init__(self, get_response):
    self.get_response = get_response

  def __call__(self, request: HttpRequest) -> HttpResponse:
    response = self.get_response(request)

    # Only set if not already explicitly set by a view.
    if "Permissions-Policy" not in response:
      response["Permissions-Policy"] = DEFAULT_PERMISSIONS_POLICY
    if "Cross-Origin-Opener-Policy" not in response:
      response["Cross-Origin-Opener-Policy"] = DEFAULT_COOP
    if "Content-Security-Policy-Report-Only" not in response:
      response["Content-Security-Policy-Report-Only"] = DEFAULT_CSP_REPORT_ONLY

    return response
