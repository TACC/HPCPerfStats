"""
Utilities for waiting on Valkey during container startup.

This prevents race conditions where Django starts up and attempts to use the
Valkey cache before the `valkey` container is reachable or ready.

The client is valkey-py. It parses ``unix://`` (Unix socket) and
``valkey://`` / ``valkeys://`` TCP URLs.

Attributes:
  COMPOSE_VALKEY_UNIX_URL (str): Shared Unix socket URL used by compose
    ``web`` / ``pipeline`` when ``[CACHE] valkey_location`` is missing.
  _UNIX_SCHEMES (frozenset[str]): URL schemes that mean a Unix domain socket
    instead of TCP.
"""

from __future__ import annotations

import time
import urllib.parse

import valkey

from hpcperfstats.dbload.lib.dbwait import wait_for_host_port_resolution

COMPOSE_VALKEY_UNIX_URL = "unix:///run/valkey/valkey.sock?db=1"
_UNIX_SCHEMES = frozenset({"unix"})


def compose_valkey_tcp_wait_should_use_unix(valkey_url: str) -> bool:
  """
  Return True when *valkey_url* uses Compose hostname ``valkey``.

  Startup wait must not resolve ``valkey:6379`` until that alias exists
  on ``hpcperfstats_net``. Compose always shares ``valkey_runtime``, so
  wait on the Unix socket instead.

  Args:
    valkey_url (str): Cache URL from ``[CACHE] valkey_location``.

  Returns:
    bool: True when the URL host is exactly ``valkey``.

  Examples:
    >>> compose_valkey_tcp_wait_should_use_unix("valkey://valkey:6379/1")
    True
    >>> compose_valkey_tcp_wait_should_use_unix("valkey://example:6379/1")
    False
  """
  if valkey_url_uses_unix_socket(valkey_url):
    return False
  host = (urllib.parse.urlparse(valkey_url).hostname or "").lower()
  return host == "valkey"


def valkey_wait_url() -> str:
  """
  Return the Valkey URL container startup scripts should wait on.

  Prefer ``[CACHE] valkey_location`` (its getter already falls back to
  :data:`COMPOSE_VALKEY_UNIX_URL`). When the INI cannot be read, use the
  compose socket. Remap Compose hostname ``valkey`` to the Unix socket so
  the wait does not DNS-timeout with ``Name or service not known``.
  External TCP hosts stay unchanged.

  Args:
    None

  Returns:
    str: Valkey URL for ``wait_for_valkey_available``.

  Examples:
    >>> valkey_wait_url().startswith(("unix://", "valkey://", "valkeys://"))
    True
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  try:
    url = cfg.get_valkey_location()
  except Exception:
    return COMPOSE_VALKEY_UNIX_URL
  if compose_valkey_tcp_wait_should_use_unix(url):
    return COMPOSE_VALKEY_UNIX_URL
  return url


def valkey_url_uses_unix_socket(valkey_url: str) -> bool:
  """
  Return True when *valkey_url* addresses Valkey over a Unix domain socket.

  valkey-py accepts ``unix:///path``; TCP URLs (``valkey://host:port/db``)
  return False.

  Args:
    valkey_url (str): Cache URL from ``[CACHE] valkey_location`` or a test
      fixture.

  Returns:
    bool: True when the URL scheme is the Unix socket form.

  Examples:
    >>> valkey_url_uses_unix_socket("unix:///run/valkey/valkey.sock?db=1")
    True
    >>> valkey_url_uses_unix_socket("valkey://valkey:6379/1")
    False
  """
  scheme = urllib.parse.urlparse(valkey_url).scheme.lower()
  return scheme in _UNIX_SCHEMES


def resolve_valkey_host_port(valkey_url: str) -> tuple[str, str]:
  """
  Return (host, port) for a Valkey TCP URL.

  Args:
    valkey_url (str): Valkey URL.

  Returns:
    tuple[str, str]: Host (``localhost`` when absent) and port (``6379``
    when absent).

  Examples:
    >>> resolve_valkey_host_port("valkey://cache:6380/1")
    ('cache', '6380')
  """
  parsed = urllib.parse.urlparse(valkey_url)
  host = parsed.hostname or "localhost"
  port = str(parsed.port or 6379)
  return host, port


def wait_for_valkey_available(
  valkey_url: str,
  *,
  timeout_seconds: int = 60,
  interval_seconds: float = 0.25,
  dns_timeout_seconds: int | None = None,
  ping_timeout_seconds: float = 2.0,
) -> None:
  """
  Wait until Valkey responds to `PING`.

  Args:
    valkey_url (str): Valkey URL to ping.
    timeout_seconds (int): Overall wait budget in seconds.
    interval_seconds (float): Sleep between attempts.
    dns_timeout_seconds (int | None): DNS resolution budget for TCP URLs;
      ``None`` uses the remaining overall budget.
    ping_timeout_seconds (float): Socket connect/read timeout per attempt.

  Returns:
    None

  Raises:
    TimeoutError: Valkey did not answer ``PING`` within *timeout_seconds*.

  Examples:
    >>> wait_for_valkey_available(
    ...   "unix:///run/valkey/valkey.sock?db=1", timeout_seconds=5
    ... )  # doctest: +SKIP
  """
  host, port = resolve_valkey_host_port(valkey_url)

  deadline = time.time() + max(0, timeout_seconds)
  remaining = max(0.0, deadline - time.time())
  dns_budget_seconds = (
    float(dns_timeout_seconds) if dns_timeout_seconds is not None else remaining
  )
  dns_budget_seconds = min(dns_budget_seconds, remaining)

  if dns_budget_seconds > 0 and not valkey_url_uses_unix_socket(valkey_url):
    wait_for_host_port_resolution(
      host,
      port,
      timeout_seconds=int(dns_budget_seconds),
      interval_seconds=interval_seconds,
    )

  last_error: Exception | None = None

  while time.time() < deadline:
    try:
      client = valkey.Valkey.from_url(
        valkey_url,
        socket_connect_timeout=ping_timeout_seconds,
        socket_timeout=ping_timeout_seconds,
      )
      # `ping()` raises valkey.exceptions.ConnectionError when unreachable.
      if client.ping():
        return
    except Exception as e:  # pragma: no cover (covered via unit tests)
      last_error = e
      time.sleep(interval_seconds)

  raise TimeoutError(
    f"Timed out waiting for Valkey at {valkey_url}. Last error: {last_error}"
  )
