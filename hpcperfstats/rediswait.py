"""Utilities for waiting on Redis during container startup.

This prevents race conditions where Django starts up and attempts to use the
Redis cache before the `redis` container is reachable or ready.
"""

from __future__ import annotations

import time
import urllib.parse

import redis

from hpcperfstats.dbwait import wait_for_host_port_resolution


def resolve_redis_host_port(redis_url: str) -> tuple[str, str]:
  """Return (host, port) for a Redis URL.

  Examples:
    - redis://redis:6379/1 -> ("redis", "6379")
    - redis://127.0.0.1 -> ("127.0.0.1", "6379")
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
  """Wait until Redis responds to `PING`.

  Raises:
    TimeoutError: if Redis isn't reachable within `timeout_seconds`.
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

  if dns_budget_seconds > 0:
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

