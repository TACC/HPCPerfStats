"""
Utilities for waiting on Redis during container startup.

This prevents race conditions where Django starts up and attempts to use the
Redis cache before the `redis` container is reachable or ready.

Attributes:
  COMPOSE_REDIS_UNIX_URL (str): Shared Unix socket URL used by compose
    ``web`` / ``pipeline`` when ``[CACHE] redis_location`` is missing.
  _UNIX_REDIS_SCHEMES (frozenset[str]): URL schemes that mean a Unix domain
    socket instead of TCP.
"""

from __future__ import annotations

import time
import urllib.parse

import redis

from hpcperfstats.dbload.lib.dbwait import wait_for_host_port_resolution

COMPOSE_REDIS_UNIX_URL = "unix:///run/redis/redis.sock?db=1"
_UNIX_REDIS_SCHEMES = frozenset({"unix", "redis+unix"})


def _ini_redis_location() -> str:
  """
  Return ``[CACHE] redis_location`` from the loaded INI, or empty.

  Args:
    None

  Returns:
    str: Stripped cache URL, or ``""`` when the INI omits the key.

  Examples:
    >>> callable(_ini_redis_location)
    True
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  return str(cfg.get_redis_location() or "").strip()


def compose_redis_tcp_wait_should_use_unix(redis_url: str) -> bool:
  """
  Return True when *redis_url* uses Compose hostname ``redis``.

  Startup wait must not resolve ``redis:6379`` until that alias exists
  on ``hpcperfstats_net``. Compose always shares ``redis_runtime``, so
  wait on the Unix socket instead.

  Args:
    redis_url (str): Cache URL from ``[CACHE] redis_location``.

  Returns:
    bool: True when the URL host is exactly ``redis``.

  Examples:
    >>> compose_redis_tcp_wait_should_use_unix("redis://redis:6379/1")
    True
    >>> compose_redis_tcp_wait_should_use_unix("redis://example:6379/1")
    False
  """
  if redis_url_uses_unix_socket(redis_url):
    return False
  host = (urllib.parse.urlparse(redis_url).hostname or "").lower()
  return host == "redis"


def redis_wait_url() -> str:
  """
  Return the Redis URL container startup scripts should wait on.

  Prefer ``[CACHE] redis_location``. When that is missing, empty, or the
  INI cannot be read, use :data:`COMPOSE_REDIS_UNIX_URL`. Remap Compose
  hostname ``redis`` (including baked ``redis://redis:6379/1``) to the
  Unix socket so wait does not DNS-timeout with
  ``Name or service not known`` / ``Timed out waiting to resolve
  redis:6379``. External TCP hosts stay unchanged.

  Args:
    None

  Returns:
    str: Redis URL for ``wait_for_redis_available``.

  Examples:
    >>> redis_wait_url().startswith(("unix://", "redis://", "redis+unix://"))
    True
  """
  try:
    url = _ini_redis_location()
  except Exception:
    return COMPOSE_REDIS_UNIX_URL
  if not url:
    return COMPOSE_REDIS_UNIX_URL
  if compose_redis_tcp_wait_should_use_unix(url):
    return COMPOSE_REDIS_UNIX_URL
  return url


def redis_url_uses_unix_socket(redis_url: str) -> bool:
  """
  Return True when *redis_url* addresses Redis over a Unix domain socket.

  Django cache and redis-py accept ``unix:///path`` and ``redis+unix:///path``.
  TCP URLs (``redis://host:port/db``) return False.

  Args:
    redis_url (str): Cache URL from ``[CACHE] redis_location`` or a test
      fixture.

  Returns:
    bool: True when the URL scheme is a Unix socket form.

  Examples:
    >>> redis_url_uses_unix_socket("unix:///run/redis/redis.sock?db=1")
    True
    >>> redis_url_uses_unix_socket("redis://redis:6379/1")
    False
  """
  scheme = urllib.parse.urlparse(redis_url).scheme.lower()
  return scheme in _UNIX_REDIS_SCHEMES


def resolve_redis_host_port(redis_url: str) -> tuple[str, str]:
  """
  Return (host, port) for a Redis URL.
  
  Args:
    redis_url (str): String for redis url.
  
  Returns:
    tuple[str, str]: tuple[str, str] produced by this call.
  
  Examples:
    >>> resolve_redis_host_port("x")  # doctest: +SKIP
  """
  parsed = urllib.parse.urlparse(redis_url)
  host = parsed.hostname or "localhost"
  port = str(parsed.port or 6379)
  return host, port


def wait_for_redis_available(
  redis_url: str,
  *,
  timeout_seconds: int = 60,
  interval_seconds: float = 0.25,
  dns_timeout_seconds: int | None = None,
  ping_timeout_seconds: float = 2.0,
) -> None:
  """
  Wait until Redis responds to `PING`.
  
  Args:
    redis_url (str): String for redis url.
    timeout_seconds (int): Integer value for timeout seconds.
    interval_seconds (float): Floating-point value for interval seconds.
    dns_timeout_seconds (int | None): One of ``int``, ``None``.
    ping_timeout_seconds (float): Floating-point value for ping timeout
    seconds.
  
  Returns:
    None
  
  Raises:
    TimeoutError: Raised when ``wait_for_redis_available`` hits a
    ``TimeoutError`` failure path.
  
  Examples:
    >>> wait_for_redis_available("x", 0, 0, None, 0)  # doctest: +SKIP
  """
  host, port = resolve_redis_host_port(redis_url)

  deadline = time.time() + max(0, timeout_seconds)
  remaining = max(0.0, deadline - time.time())
  dns_budget_seconds = (
    float(dns_timeout_seconds)
    if dns_timeout_seconds is not None
    else remaining
  )
  dns_budget_seconds = min(dns_budget_seconds, remaining)

  if dns_budget_seconds > 0 and not redis_url_uses_unix_socket(redis_url):
    wait_for_host_port_resolution(
      host,
      port,
      timeout_seconds=int(dns_budget_seconds),
      interval_seconds=interval_seconds,
    )

  last_error: Exception | None = None

  while time.time() < deadline:
    try:
      client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=ping_timeout_seconds,
        socket_timeout=ping_timeout_seconds,
      )
      # `ping()` raises redis.exceptions.ConnectionError when unreachable.
      if client.ping():
        return
    except Exception as e:  # pragma: no cover (covered via unit tests)
      last_error = e
      time.sleep(interval_seconds)

  raise TimeoutError(
    f"Timed out waiting for Redis at {redis_url}. Last error: {last_error}"
  )

