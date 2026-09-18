"""
Pipeline RabbitMQ memory scream: poll management API every 5 minutes.

Always emits a ``[rabbitmq-watcher]`` line so operators can correlate RSS
climbs in compose logs. When ``mem_used`` is at or above 40 GiB, the line
includes literal ``ERROR`` and a band floor (40, 50, 60, … every 10 GiB).

Attributes:
  WATCHER_PREFIX: Log line token for grep/pager correlation.
  POLL_INTERVAL_S: Seconds between management polls (5 minutes).
  ERROR_FLOOR_GIB: First ERROR band floor in GiB.
  ERROR_STEP_GIB: GiB step between ERROR band floors.
  GIB: Bytes per binary GiB (2**30).
  NodeMemorySnapshot: Named tuple of one management poll snapshot.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import base64
import json
import os
import time
import urllib.request

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.process_title import set_daemon_process_title

WATCHER_PREFIX = "[rabbitmq-watcher]"
POLL_INTERVAL_S = 300
ERROR_FLOOR_GIB = 40
ERROR_STEP_GIB = 10
GIB = 1024 ** 3


class NodeMemorySnapshot(NamedTuple):
  """
  One RabbitMQ management poll: node memory plus connection count.

  Attributes:
    mem_used_bytes: Erlang ``mem_used`` from ``/api/nodes`` (bytes).
    connections: ``object_totals.connections`` from ``/api/overview``.
    node_name: Selected node name, or empty when absent.
  """

  mem_used_bytes: int
  connections: int | None
  node_name: str


def error_threshold_band(mem_gib: float) -> int | None:
  """
  Return the ERROR band floor for ``mem_gib``, or ``None`` below 40 GiB.

  Bands are 40, 50, 60, … using floor arithmetic on the integer GiB value:
  ``40 + 10 * ((int(mem_gib) - 40) // 10)``.

  Args:
    mem_gib (float): Memory used in binary GiB (``mem_used / 2**30``).

  Returns:
    int | None: Band floor GiB when ``mem_gib >= 40``, else ``None``.

  Examples:
    >>> error_threshold_band(39.99) is None
    True
    >>> error_threshold_band(40.0)
    40
    >>> error_threshold_band(49.9)
    40
    >>> error_threshold_band(50.0)
    50
    >>> error_threshold_band(70.1)
    70
  """
  if mem_gib < ERROR_FLOOR_GIB:
    return None
  steps = (int(mem_gib) - ERROR_FLOOR_GIB) // ERROR_STEP_GIB
  return ERROR_FLOOR_GIB + ERROR_STEP_GIB * steps


def format_watcher_line(
  *,
  mem_used_bytes: int,
  mem_used_gib: float,
  connections: int | None,
  threshold_gib: int | None,
) -> str:
  """
  Build one greppable watcher log line (prefix always present).

  When ``threshold_gib`` is set, insert literal ``ERROR`` and
  ``threshold_gib=N`` so log pagers that key on ``ERROR`` fire.

  Args:
    mem_used_bytes (int): Raw ``mem_used`` from management (bytes).
    mem_used_gib (float): Same value in binary GiB for human correlation.
    connections (int | None): Connection count, or ``None`` when unknown.
    threshold_gib (int | None): ERROR band floor, or ``None`` below floor.

  Returns:
    str: Single-line message starting with ``[rabbitmq-watcher]``.

  Examples:
    >>> format_watcher_line(
    ...     mem_used_bytes=1000,
    ...     mem_used_gib=0.0,
    ...     connections=1,
    ...     threshold_gib=None,
    ... ).startswith("[rabbitmq-watcher]")
    True
    >>> "ERROR" in format_watcher_line(
    ...     mem_used_bytes=50 * (1024 ** 3),
    ...     mem_used_gib=50.0,
    ...     connections=None,
    ...     threshold_gib=50,
    ... )
    True
  """
  conn_s = "n/a" if connections is None else str(int(connections))
  parts = [WATCHER_PREFIX]
  if threshold_gib is not None:
    parts.append("ERROR")
    parts.append("threshold_gib=%d" % int(threshold_gib))
  parts.append("mem_used_gib=%.2f" % float(mem_used_gib))
  parts.append("mem_used_bytes=%d" % int(mem_used_bytes))
  parts.append("connections=%s" % conn_s)
  return " ".join(parts)


def _management_base_url() -> str:
  """
  Resolve RabbitMQ management base URL (env override or INI host).

  Returns:
    str: Base URL without a trailing slash.

  Examples:
    >>> isinstance(_management_base_url(), str)
    True
  """
  host = cfg.get_rmq_server()
  default = "http://%s:15672" % host
  return str(os.environ.get("RABBITMQ_MANAGEMENT_URL", default)).rstrip("/")


def _management_auth() -> tuple[str, str]:
  """
  Resolve management HTTP basic-auth credentials from the environment.

  Returns:
    tuple[str, str]: ``(user, password)``, defaulting to ``guest``/``guest``.

  Examples:
    >>> u, p = _management_auth()
    >>> isinstance(u, str) and isinstance(p, str)
    True
  """
  user = os.environ.get("RABBITMQ_MANAGEMENT_USER", "guest")
  password = os.environ.get("RABBITMQ_MANAGEMENT_PASSWORD", "guest")
  return str(user), str(password)


def _http_get_json(
  url: str,
  *,
  user: str,
  password: str,
  timeout_s: float,
) -> Any:
  """
  GET a JSON document from the management API with basic auth.

  Args:
    url (str): Absolute management API URL.
    user (str): Basic-auth username.
    password (str): Basic-auth password.
    timeout_s (float): ``urlopen`` timeout in seconds.

  Returns:
    Any: Parsed JSON (typically ``dict`` or ``list``).

  Raises:
    urllib.error.URLError: On transport failure.
    TimeoutError: When the request times out.
    json.JSONDecodeError: When the body is not JSON.
    ValueError: When HTTP status is not success (via ``urlopen``).

  Examples:
    >>> _http_get_json(  # doctest: +SKIP
    ...     "http://rabbitmq:15672/api/nodes",
    ...     user="guest",
    ...     password="guest",
    ...     timeout_s=2.0,
    ... )
  """
  token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
  req = urllib.request.Request(
      url, headers={"Authorization": "Basic %s" % token}
  )
  with urllib.request.urlopen(req, timeout=timeout_s) as resp:
    return json.loads(resp.read().decode())


def fetch_node_memory_snapshot(
  *,
  base_url: str,
  user: str,
  password: str,
  timeout_s: float = 5.0,
) -> NodeMemorySnapshot:
  """
  Fetch ``mem_used`` from ``/api/nodes`` and connections from overview.

  Prefers a running node; falls back to the first dict entry.

  Args:
    base_url (str): Management base URL (no trailing slash required).
    user (str): Basic-auth username.
    password (str): Basic-auth password.
    timeout_s (float): Per-request timeout in seconds.

  Returns:
    NodeMemorySnapshot: Parsed memory and optional connection count.

  Raises:
    LookupError: When nodes JSON is empty or lacks ``mem_used``.
    urllib.error.URLError: On transport failure for the nodes request.
    json.JSONDecodeError: When a response body is not JSON.

  Examples:
    >>> fetch_node_memory_snapshot(  # doctest: +SKIP
    ...     base_url="http://rabbitmq:15672",
    ...     user="guest",
    ...     password="guest",
    ... )
  """
  root = str(base_url).rstrip("/")
  nodes = _http_get_json(
      "%s/api/nodes" % root,
      user=user,
      password=password,
      timeout_s=timeout_s,
  )
  if not isinstance(nodes, list) or not nodes:
    raise LookupError("RabbitMQ /api/nodes returned no nodes")
  running = [n for n in nodes if isinstance(n, dict) and n.get("running")]
  node = running[0] if running else (
      nodes[0] if isinstance(nodes[0], dict) else None
  )
  if not isinstance(node, dict) or node.get("mem_used") is None:
    raise LookupError("RabbitMQ /api/nodes missing mem_used")
  mem_used = int(node["mem_used"])
  node_name = str(node.get("name") or "")

  connections: int | None = None
  try:
    overview = _http_get_json(
        "%s/api/overview" % root,
        user=user,
        password=password,
        timeout_s=timeout_s,
    )
    if isinstance(overview, dict):
      totals = overview.get("object_totals") or {}
      if isinstance(totals, dict) and totals.get("connections") is not None:
        connections = int(totals["connections"])
  except Exception:
    connections = None

  return NodeMemorySnapshot(
      mem_used_bytes=mem_used,
      connections=connections,
      node_name=node_name,
  )


def poll_once(*, log_fn: Any = log_print) -> None:
  """
  One management poll: always log a watcher line; soft-fail on errors.

  Fetch failures log a non-ERROR watcher line (so pagers do not fire on
  broker-down) and return without raising.

  Args:
    log_fn (Any): Callable used like ``print`` / ``log_print``.

  Returns:
    None

  Examples:
    >>> poll_once(log_fn=lambda *a, **k: None)  # doctest: +SKIP
  """
  try:
    user, password = _management_auth()
    snap = fetch_node_memory_snapshot(
        base_url=_management_base_url(),
        user=user,
        password=password,
    )
  except Exception as exc:
    log_fn("%s fetch_failed reason=%s" % (WATCHER_PREFIX, exc), flush=True)
    return

  mem_gib = float(snap.mem_used_bytes) / float(GIB)
  band = error_threshold_band(mem_gib)
  line = format_watcher_line(
      mem_used_bytes=snap.mem_used_bytes,
      mem_used_gib=mem_gib,
      connections=snap.connections,
      threshold_gib=band,
  )
  log_fn(line, flush=True)


def main() -> None:
  """
  Supervisord entrypoint: set process title, then poll forever.

  Warmup delay lives in supervisord (``sleep 30`` before ``exec``) so unit
  tests can import helpers without blocking.

  Returns:
    None

  Examples:
    >>> # supervisord: sleep 30 then exec this module
    >>> main  # doctest: +SKIP
  """
  set_daemon_process_title(name="rabbitmq_watcher.py", role="main")
  while True:
    poll_once()
    time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
  main()
