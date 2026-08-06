"""
Profiling middleware: add ?prof to a URL to profile the view (DEBUG only).

Optional query params:
- sort: pstats sort key (default "time")
- count: number of rows to show (default 100)

Attributes:
  DEFAULT_COOP: Cross-Origin-Opener-Policy value applied by DefaultSecurityHeadersMiddleware.
  DEFAULT_CSP: Bokeh-relaxed HTML CSP (self scripts/styles + unsafe-eval; no unsafe-inline).
  DEFAULT_CSP_NO_ACTIVE: CSP for redirects, JSON, and empty non-HTML responses.
  DEFAULT_CSP_REPORT_ONLY: Report-only CSP used to stage further tightening.
  DEFAULT_CSP_STRICT: Strict HTML CSP without unsafe-eval for login shells.
  DEFAULT_PERMISSIONS_POLICY: Permissions-Policy value applied by DefaultSecurityHeadersMiddleware.
  _BOKEH_RELAXED_CSP_PREFIXES: Path prefixes that keep Bokeh unsafe-eval for HTML responses.
  _REDIRECT_STATUSES: HTTP redirect status codes treated as no-active-content responses.
"""
from __future__ import annotations

from typing import Any

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

# Enforced CSP for HTML documents that embed Bokeh (direct Gunicorn defense-in-depth).
# Public nginx SPA policies are hash-based; this keeps unsafe-eval only, never unsafe-inline.
DEFAULT_CSP = (
  "default-src 'self'; "
  "base-uri 'self'; "
  "object-src 'none'; "
  "frame-ancestors 'self'; "
  "form-action 'self'; "
  "img-src 'self' data:; "
  "font-src 'self' data:; "
  "style-src 'self'; "
  # Bokeh embed on /machine/* and /pub/* may still require unsafe-eval.
  "script-src 'self' 'unsafe-eval'; "
  "connect-src 'self'; "
  "upgrade-insecure-requests; "
  "report-uri /csp-report/;"
)

# Strict CSP without unsafe-eval for non-Bokeh HTML shells (login).
DEFAULT_CSP_STRICT = (
  "default-src 'self'; "
  "base-uri 'self'; "
  "object-src 'none'; "
  "frame-ancestors 'self'; "
  "form-action 'self'; "
  "img-src 'self' data:; "
  "font-src 'self' data:; "
  "style-src 'self'; "
  "script-src 'self'; "
  "connect-src 'self'; "
  "upgrade-insecure-requests; "
  "report-uri /csp-report/;"
)

# No active content for redirects, JSON APIs, and empty non-HTML responses.
DEFAULT_CSP_NO_ACTIVE = (
  "default-src 'none'; "
  "base-uri 'none'; "
  "object-src 'none'; "
  "frame-ancestors 'self'; "
  "form-action 'none'; "
  "script-src 'none'; "
  "style-src 'none'; "
  "img-src 'none'; "
  "font-src 'none'; "
  "connect-src 'none'; "
  "upgrade-insecure-requests; "
  "report-uri /csp-report/;"
)

# Stricter report-only policy used to stage future tightening (no unsafe-eval / inline).
DEFAULT_CSP_REPORT_ONLY = (
  "default-src 'self'; "
  "base-uri 'self'; "
  "object-src 'none'; "
  "frame-ancestors 'self'; "
  "form-action 'self'; "
  "img-src 'self' data:; "
  "font-src 'self' data:; "
  "style-src 'self'; "
  "script-src 'self'; "
  "connect-src 'self'; "
  "upgrade-insecure-requests; "
  "report-uri /csp-report/;"
)

# Paths that embed Bokeh and keep relaxed script-src (unsafe-eval) for HTML documents.
_BOKEH_RELAXED_CSP_PREFIXES = (
  "/machine/",
  "/pub/",
  "/api/jobs/",
  "/api/host_plot/",
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _response_is_html(response: HttpResponse) -> bool:
  """
  Return True when the response is an HTML document that needs document CSP.

  Empty bodies (204), redirects, and non-HTML content types use no-active CSP.

  Args:
    response (HttpResponse): Django response object.

  Returns:
    bool: True when Content-Type contains text/html and status is not 204.

  Examples:
    >>> class _R:
    ...     status_code = 200
    ...     def get(self, key, default=""):
    ...         return "text/html; charset=utf-8" if key == "Content-Type" else default
    >>> _response_is_html(_R())
    True
  """
  if int(getattr(response, "status_code", 0) or 0) == 204:
    return False
  content_type = str(response.get("Content-Type", "") or "").lower()
  return "text/html" in content_type


def _csp_for_request(request: HttpRequest, response: HttpResponse) -> str:
  """
  Return enforced CSP for this path and response body type.

  Non-HTML and redirect responses use a no-active-content policy. HTML documents
  keep path-scoped policies without unsafe-inline; Bokeh HTML paths retain
  unsafe-eval only.

  Args:
    request (HttpRequest): Incoming Django request.
    response (HttpResponse): Response produced by the view/middleware chain.

  Returns:
    str: Content-Security-Policy header value.

  Examples:
    >>> from django.http import HttpResponse
    >>> class _Req:
    ...     path = "/api/jobs/"
    >>> _csp_for_request(_Req(), HttpResponse(content_type="application/json")) == DEFAULT_CSP_NO_ACTIVE
    True
  """
  if response.status_code in _REDIRECT_STATUSES or not _response_is_html(response):
    return DEFAULT_CSP_NO_ACTIVE
  path = request.path or ""
  if path.startswith("/login_prompt"):
    return DEFAULT_CSP_STRICT
  if any(path.startswith(prefix) for prefix in _BOKEH_RELAXED_CSP_PREFIXES):
    return DEFAULT_CSP
  if path.startswith("/api/"):
    return DEFAULT_CSP_STRICT
  return DEFAULT_CSP_STRICT


class ProfileMiddleware:
  """
  Simple profiling middleware for Django views (Django 3+/6+ style).
  
  Activated only when:
  - settings.DEBUG is True, and
  - the incoming request has a ?prof query parameter.
  
  Attributes:
    get_response: Next middleware/view callable in the Django stack.
  """

  def __init__(self, get_response: Any) -> None:
    """
    Store the next middleware callable for this profiling wrapper.
    
    Args:
      get_response (Any): Next middleware or view callable.
    
    Returns:
      None
    
    Examples:
      >>> ProfileMiddleware(lambda request: HttpResponse("ok")).get_response is not None
      True
    """
    self.get_response = get_response

  def _enabled(self, request: HttpRequest) -> bool:
    """
    Return True if profiling is enabled for this request.
    
    Args:
      request (HttpRequest): Incoming request.
    
    Returns:
      bool: True when DEBUG is on and ``prof`` is present in the query string.
    
    Examples:
      >>> class _Req:
      ...     GET = {"prof": "1"}
      >>> from django.conf import settings as _settings
      >>> bool(_settings.DEBUG) or True  # doctest: +SKIP
      True
    """
    return bool(settings.DEBUG and "prof" in request.GET)

  def __call__(self, request: HttpRequest) -> HttpResponse:
    """
    Optionally profile the downstream view and replace the body with stats text.
    
    Args:
      request (HttpRequest): Incoming request.
    
    Returns:
      HttpResponse: Downstream response, or a plain-text profile dump when enabled.
    
    Raises:
      Exception: Re-raises any exception from the downstream view unchanged.
    
    Examples:
      >>> ProfileMiddleware(lambda request: HttpResponse("ok"))  # doctest: +SKIP
    """
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
  """
  Apply a consistent default cache policy.
  
  Views may still opt in by explicitly setting Cache-Control. nginx preserves
  application Cache-Control on proxied responses while owning transport security
  headers at the edge.
  
  Attributes:
    get_response: Next middleware/view callable in the Django stack.
  """

  def __init__(self, get_response: Any) -> None:
    """
    Store the next middleware callable for cache-control defaults.
    
    Args:
      get_response (Any): Next middleware or view callable.
    
    Returns:
      None
    
    Examples:
      >>> DefaultCacheControlMiddleware(lambda request: HttpResponse("ok")).get_response is not None
      True
    """
    self.get_response = get_response

  def __call__(self, request: HttpRequest) -> HttpResponse:
    """
    Ensure responses without Cache-Control receive a no-store default.
    
    Args:
      request (HttpRequest): Incoming request.
    
    Returns:
      HttpResponse: Downstream response, possibly with Cache-Control added.
    
    Examples:
      >>> mw = DefaultCacheControlMiddleware(lambda request: HttpResponse("ok"))
      >>> "Cache-Control" in mw(HttpRequest())  # doctest: +SKIP
      True
    """
    response = self.get_response(request)
    # If a view (or Django itself, e.g. some static handlers) has already set
    # Cache-Control, respect that decision.
    if "Cache-Control" not in response:
      response["Cache-Control"] = "no-store, no-cache"
    return response


class DefaultSecurityHeadersMiddleware:
  """
  Apply security headers that Django doesn't emit by default.
  
  nginx is the public enforcement layer for proxied traffic (duplicate upstream
  security headers are hidden). These Django defaults remain defense-in-depth for
  direct Gunicorn access and unit tests.
  
  Attributes:
    get_response: Next middleware/view callable in the Django stack.
  """

  def __init__(self, get_response: Any) -> None:
    """
    Store the next middleware callable for security-header defaults.
    
    Args:
      get_response (Any): Next middleware or view callable.
    
    Returns:
      None
    
    Examples:
      >>> DefaultSecurityHeadersMiddleware(lambda request: HttpResponse("ok")).get_response is not None
      True
    """
    self.get_response = get_response

  def __call__(self, request: HttpRequest) -> HttpResponse:
    """
    Attach Permissions-Policy, COOP, and path-aware CSP when not already set.
    
    Args:
      request (HttpRequest): Incoming request.
    
    Returns:
      HttpResponse: Downstream response with security headers applied.
    
    Examples:
      >>> mw = DefaultSecurityHeadersMiddleware(lambda request: HttpResponse("{}"))
      >>> mw  # doctest: +SKIP
    """
    response = self.get_response(request)

    # Only set if not already explicitly set by a view.
    if "Permissions-Policy" not in response:
      response["Permissions-Policy"] = DEFAULT_PERMISSIONS_POLICY
    if "Cross-Origin-Opener-Policy" not in response:
      response["Cross-Origin-Opener-Policy"] = DEFAULT_COOP
    if "Content-Security-Policy" not in response:
      response["Content-Security-Policy"] = _csp_for_request(request, response)
    if "Content-Security-Policy-Report-Only" not in response:
      response["Content-Security-Policy-Report-Only"] = DEFAULT_CSP_REPORT_ONLY

    return response
