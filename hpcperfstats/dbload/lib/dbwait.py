"""
Utilities for resolving the PostgreSQL host/port used by the container startup
scripts.

This avoids hardcoding service names like `db` inside shell scripts, which can
break when the deploy uses different Docker Compose service names or custom
`hpcperfstats.ini` values.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Mapping

import psycopg


def resolve_postgres_wait_target(
  env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
  """
  Return (host, port) that startup scripts should wait on.

  Precedence:
  1. `POSTGRES_HOST` / `POSTGRES_PORT` if set
  2. `DB_HOST` / `DB_PORT` if set
  3. values from `hpcperfstats.ini` via `hpcperfstats.dbload.lib.conf_parser`

  Args:
    env (Mapping[str, str] | None): One of ``Mapping[str, str]``, ``None``.

  Returns:
    Tuple[str, str]: Tuple[str, str] produced by this call.

  Raises:
    ValueError: Raised when ``resolve_postgres_wait_target`` hits a
    ``ValueError`` failure path.

  Examples:
    >>> resolve_postgres_wait_target(None)  # doctest: +SKIP
  """
  env_map = os.environ if env is None else env

  host = env_map.get("POSTGRES_HOST") or env_map.get("DB_HOST")
  port = env_map.get("POSTGRES_PORT") or env_map.get("DB_PORT")

  if host and port:
    return str(host), str(port)

  # Import lazily so tests can override HPCPERFSTATS_INI and reload freely.
  from hpcperfstats.dbload.lib import conf_parser as cfg

  if not host:
    host = cfg.get_host()
  if not port:
    port = cfg.get_port()

  if not host or not port:
    raise ValueError(
      "Unable to resolve PostgreSQL host/port from env or configuration"
    )

  return str(host), str(port)


def can_resolve_host_port(host: str, port: str) -> bool:
  """
  Return True if `host:port` is resolvable via getaddrinfo.

  Note: this does NOT test TCP connectivity; it's only DNS/name resolution.

  Args:
    host (str): String for host.
    port (str): String for port.

  Returns:
    bool: True or False for this check.

  Examples:
    >>> can_resolve_host_port("x", "x")  # doctest: +SKIP
  """
  try:
    socket.getaddrinfo(host, port)
    return True
  except Exception:
    return False


def wait_for_host_port_resolution(
  host: str,
  port: str,
  *,
  timeout_seconds: int = 60,
  interval_seconds: float = 0.25,
) -> None:
  """
  Wait until DNS/name resolution for `host:port` succeeds.

  Args:
    host (str): String for host.
    port (str): String for port.
    timeout_seconds (int): Integer value for timeout seconds.
    interval_seconds (float): Floating-point value for interval seconds.

  Returns:
    None

  Raises:
    TimeoutError: Raised when ``wait_for_host_port_resolution`` hits a
    ``TimeoutError`` failure path.

  Examples:
    >>> wait_for_host_port_resolution("x", "x", 0, 0)  # doctest: +SKIP
  """
  deadline = time.time() + max(0, timeout_seconds)
  while True:
    if can_resolve_host_port(host, port):
      return
    if time.time() >= deadline:
      raise TimeoutError(f"Timed out waiting to resolve {host}:{port}")
    time.sleep(interval_seconds)


def postgres_unix_socket_path(host: str, port: str) -> str | None:
  """
  Return the libpq Unix socket file for ``host``/``port``, or None for TCP.

  libpq treats a ``host`` that starts with ``/`` as the socket directory and
  connects to ``<dir>/.s.PGSQL.<port>``.

  Args:
    host (str): PostgreSQL host name, or socket directory when it starts
      with ``/``.
    port (str): PostgreSQL port; it names the socket file.

  Returns:
    str | None: Socket file path, or None when ``host`` is a TCP host.

  Examples:
    >>> postgres_unix_socket_path("/run/postgresql", "5432")
    '/run/postgresql/.s.PGSQL.5432'
    >>> postgres_unix_socket_path("db", "5432") is None
    True
  """
  if not str(host).startswith("/"):
    return None
  return os.path.join(str(host), f".s.PGSQL.{port}")


def _postgres_accepts_connection(
  host: str, port: str, connect_timeout_seconds: float
) -> bool:
  """
  Return True when Postgres is serving: TCP connect, or a socket session.

  Over the socket a bare ``connect()`` is not enough: the image entrypoint's
  first-boot init server owns the same shared socket with
  ``listen_addresses=''`` and is then shut down mid-session. Ready means a
  libpq session (``hpcperfstats.ini`` credentials) whose server listens on
  TCP. The init server has no TCP listener, so TCP mode needs no session.

  Args:
    host (str): PostgreSQL host name, or socket directory when it starts
      with ``/``.
    port (str): PostgreSQL port.
    connect_timeout_seconds (float): Timeout for this connect attempt.

  Returns:
    bool: True when the server accepted the connection, else False.

  Examples:
    >>> _postgres_accepts_connection("/nonexistent-dir", "5432", 0.1)
    False
  """
  try:
    if postgres_unix_socket_path(host, port) is None:
      socket.create_connection(
        (host, int(port)), timeout=connect_timeout_seconds
      ).close()
      return True
    from hpcperfstats.dbload.lib import conf_parser as cfg

    with psycopg.connect(
      host=host,
      port=port,
      dbname=cfg.get_db_name(),
      user=cfg.get_username(),
      password=cfg.get_password(),
      connect_timeout=max(1, int(connect_timeout_seconds)),
    ) as conn:
      sql = "SELECT current_setting('listen_addresses')"
      return conn.execute(sql).fetchone()[0] != ""
  except OSError, psycopg.Error:
    return False


def wait_for_postgres_available(
  host: str,
  port: str,
  *,
  dns_timeout_seconds: int = 60,
  connect_timeout_seconds: float = 2.0,
  interval_seconds: float = 0.25,
) -> None:
  """
  Block until PostgreSQL accepts a connection over TCP or its Unix socket.

  A TCP host first waits (bounded) for name resolution, since Compose DNS
  lags on container start. A socket directory skips that step. The connect
  loop then waits, unbounded like the old ``nc -z`` loop, until the server
  listens.

  Args:
    host (str): PostgreSQL host name, or socket directory when it starts
      with ``/``.
    port (str): PostgreSQL port.
    dns_timeout_seconds (int): Name-resolution budget for a TCP host.
    connect_timeout_seconds (float): Timeout for each connect attempt.
    interval_seconds (float): Sleep between attempts.

  Returns:
    None

  Examples:
    >>> wait_for_postgres_available("/run/postgresql", "5432")  # doctest: +SKIP
  """
  if postgres_unix_socket_path(host, port) is None:
    wait_for_host_port_resolution(
      host,
      port,
      timeout_seconds=dns_timeout_seconds,
      interval_seconds=interval_seconds,
    )
  while not _postgres_accepts_connection(host, port, connect_timeout_seconds):
    time.sleep(interval_seconds)
