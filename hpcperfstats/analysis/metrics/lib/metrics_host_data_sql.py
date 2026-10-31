"""
Set-based PostgreSQL helpers for metrics / update_metrics offload paths.

Attributes:
  LIVE_DISTINCT_STATEMENT_TIMEOUT_MS: Local statement_timeout (ms) for live
    distinct freshen probes.
  _COUNTER_VALUE_COLS: ``host_data`` columns safe to interpolate into SQL.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.analysis.metrics.lib.phase_profile import (
  phase as _profile_phase,
)

# Short budget for optional live-distinct freshen (not portal default ~120s).
LIVE_DISTINCT_STATEMENT_TIMEOUT_MS = 15000


def in_window_per_host_min_max_rows(
  hosts: list[str],
  time_filter: dict[str, Any],
) -> list[dict[str, Any]]:
  """
  Per-host ``MIN(time)`` / ``MAX(time)`` in one ``GROUP BY host`` query.

  Args:
    hosts (list[str]): Hostnames (non-empty).
    time_filter (dict[str, Any]): Django-style ``time__gte`` / ``time__lte`` keys.

  Returns:
    list[dict[str, Any]]: Rows with ``host``, ``mn``, ``mx`` keys.

  Examples:
    >>> in_window_per_host_min_max_rows([], {})  # doctest: +SKIP
    []
  """
  if not hosts:
    return []
  t0 = time_filter.get("time__gte")
  t1 = time_filter.get("time__lte")
  t_lt = time_filter.get("time__lt")
  if t0 is None or (t1 is None and t_lt is None):
    return []
  from django.db import connection

  if t_lt is not None and t1 is None:
    upper_sql = "time < %s"
    upper_param = t_lt
  else:
    upper_sql = "time <= %s"
    upper_param = t1
  sql = f"""
    SELECT host, MIN(time) AS mn, MAX(time) AS mx
    FROM host_data
    WHERE host = ANY(%s::text[])
      AND time >= %s
      AND {upper_sql}
    GROUP BY host
  """
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [list(hosts), t0, upper_param])
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def jid_scoped_distinct_host_time_count(
  jid: str,
  start: Any,
  end: Any,
  hosts: list[str] | None = None,
) -> int:
  """
  Live distinct sample count for one job window via ``jid_scoped_distinct_host_time_count`` (#8).

  Runs under ``SET LOCAL statement_timeout`` of
  ``LIVE_DISTINCT_STATEMENT_TIMEOUT_MS`` so soft-fail cancels quickly.

  Args:
    jid (str): Job id.
    start (Any): Window start (timestamptz).
    end (Any): Window end (timestamptz).
    hosts (list[str] | None): Optional ``job_data.host_list``; ``None`` keeps
      unscoped 0038 semantics (``p_hosts`` DEFAULT NULL).

  Returns:
    int: Sum of per-host ``COUNT(DISTINCT time)``.

  Examples:
    >>> jid_scoped_distinct_host_time_count("", None, None)  # doctest: +SKIP
    0
  """
  from django.db import connection, transaction

  if (
    connection.vendor != "postgresql" or not jid or start is None or end is None
  ):
    return 0
  host_list = list(hosts) if hosts else None
  sql = "SELECT jid_scoped_distinct_host_time_count(%s, %s, %s, %s::text[])"
  with transaction.atomic(), connection.cursor() as cursor:
    cursor.execute(
      "SET LOCAL statement_timeout = %s",
      [LIVE_DISTINCT_STATEMENT_TIMEOUT_MS],
    )
    cursor.execute(sql, [str(jid), start, end, host_list])
    row = cursor.fetchone()
  if not row or row[0] is None:
    return 0
  return int(row[0])


def strided_bucket_max_times(
  hosts: list[str],
  start: Any,
  end: Any,
  n_buckets: int,
) -> list[Any]:
  """
  Strided bucket max timestamps via ``host_data_strided_bucket_max_times`` (#11).

  Args:
    hosts (list[str]): Hostnames.
    start (Any): Window start.
    end (Any): Window end.
    n_buckets (int): Target bucket count (stride grid).

  Returns:
    list[Any]: Sorted distinct ``MAX(time)`` per stride group.

  Examples:
    >>> strided_bucket_max_times([], None, None, 2)  # doctest: +SKIP
    []
  """
  from django.db import connection

  if connection.vendor != "postgresql" or not hosts:
    return []
  if start is None or end is None:
    return []
  try:
    nb = max(2, int(n_buckets))
  except TypeError, ValueError, OverflowError:
    nb = 2
  sql = (
    "SELECT * FROM host_data_strided_bucket_max_times(%s::text[], %s, %s, %s)"
  )
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [list(hosts), start, end, nb])
    return [row[0] for row in cursor.fetchall() if row[0] is not None]
