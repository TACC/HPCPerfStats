"""
Utilities for resolving the PostgreSQL host/port used by the container
startup scripts.

This avoids hardcoding service names like `db` inside shell scripts, which
can break when the deploy uses different Docker Compose service names or
custom `hpcperfstats.ini` values.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Mapping, Tuple


def resolve_postgres_wait_target(env: Mapping[str, str] | None = None) -> Tuple[str, str]:
  """
  Return (host, port) that startup scripts should wait on.

  Precedence:
  1. `POSTGRES_HOST` / `POSTGRES_PORT` if set
  2. `DB_HOST` / `DB_PORT` if set
  3. values from `hpcperfstats.ini` via `hpcperfstats.conf_parser`
  4. hardcoded defaults (`db:5432`) as a final fallback
  """
  env_map = os.environ if env is None else env

  host = env_map.get("POSTGRES_HOST") or env_map.get("DB_HOST")
  port = env_map.get("POSTGRES_PORT") or env_map.get("DB_PORT")

  if host and port:
    return str(host), str(port)

  # Import lazily so tests can override HPCPERFSTATS_INI and reload freely.
  try:
    from hpcperfstats import conf_parser as cfg

    if not host:
      host = cfg.get_host()
    if not port:
      port = cfg.get_port()
  except Exception:
    # Startup scripts should be resilient; we'll fall back below.
    pass

  return str(host or "db"), str(port or "5432")


def can_resolve_host_port(host: str, port: str) -> bool:
  """Return True if `host:port` is resolvable via getaddrinfo.

  Note: this does NOT test TCP connectivity; it's only DNS/name resolution.
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
  """Wait until DNS/name resolution for `host:port` succeeds."""
  deadline = time.time() + max(0, timeout_seconds)
  while True:
    if can_resolve_host_port(host, port):
      return
    if time.time() >= deadline:
      raise TimeoutError(f"Timed out waiting to resolve {host}:{port}")
    time.sleep(interval_seconds)

