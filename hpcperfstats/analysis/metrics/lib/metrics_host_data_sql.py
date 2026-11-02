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
  Per-host first / last sample time in ``[t0, t1]`` via index probes.

  Two ``ORDER BY time LIMIT 1`` lookups per host on ``(host, time)`` over
  ``unnest(hosts)``, so the cost no longer scales with rows in the window
  (a ``GROUP BY host`` MIN/MAX read every row: 5-14 s per 16 hosts x 1 h on
  network storage). Hosts without samples come back with ``None`` bounds.

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

  upper_op, upper_param = ("<", t_lt) if t1 is None else ("<=", t1)
  probe = (
    "SELECT d.time FROM host_data d WHERE d.host = h.host"
    f" AND d.time >= %s AND d.time {upper_op} %s ORDER BY d.time {{}} LIMIT 1"
  )
  sql = (
    f"SELECT h.host, ({probe.format('ASC')}) AS mn,"
    f" ({probe.format('DESC')}) AS mx FROM unnest(%s::text[]) AS h(host)"
  )
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [t0, upper_param, t0, upper_param, list(hosts)])
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def _scope_sql(jid: str | None) -> tuple[str, list[Any]]:
  """
  Job-scope filter on alias ``d`` that cannot use the jid / dev indexes.

  Same rows as ``job_host_data_scope_sql`` (a NULL ``jid`` / ``dev`` never
  equals the job id), but wrapped in ``COALESCE`` so the planner must walk
  ``(host, time)`` in time order. With the plain predicate, hs04 picked the
  jid / ``j:`` indexes for a 64-host job (all of its rows, then host filter
  and sort) and the per-host ``ORDER BY time LIMIT 1`` probe timed out.

  Args:
    jid (str | None): Job id, or ``None`` / empty for host-wide reads.

  Returns:
    tuple[str, list[Any]]: ``(sql, params)``; ``("TRUE", [])`` without jid.

  Examples:
    >>> _scope_sql(None)
    ('TRUE', [])
    >>> _scope_sql("7")[1]
    ['7', '7']
  """
  if not jid:
    return "TRUE", []
  sql = "(COALESCE(d.jid, '') = %s OR COALESCE(d.dev, '') = ('j:' || %s))"
  return sql, [str(jid), str(jid)]


def first_last_scoped_times(
  hosts: list[str],
  start: Any,
  end: Any,
  jid: str | None = None,
) -> dict[str, tuple[Any, Any]]:
  """
  First / last (job-scoped) sample time per host via ``(host, time)`` probes.

  Same ``ORDER BY time LIMIT 1`` shape as ``in_window_per_host_min_max_rows``
  with the job scope as a filter, so an owned host answers after a few index
  entries instead of reading every row of the window (``SELECT DISTINCT host``
  over 12 h timed out on hpcperfstats04). Hosts with no scoped row map to
  ``(None, None)``.

  Args:
    hosts (list[str]): Hostnames.
    start (Any): Window start (inclusive).
    end (Any): Window end (inclusive).
    jid (str | None): Job id; ``None`` probes host-wide.

  Returns:
    dict[str, tuple[Any, Any]]: Host to ``(first, last)``.

  Examples:
    >>> first_last_scoped_times([], None, None)
    {}
  """
  if not hosts or start is None or end is None:
    return {}
  from django.db import connection

  scope, scope_params = _scope_sql(jid)
  probe = (
    "SELECT d.time FROM host_data d WHERE d.host = h.host"
    f" AND d.time >= %s AND d.time <= %s AND {scope}"
    " ORDER BY d.time {} LIMIT 1"
  )
  sql = (
    f"SELECT h.host, ({probe.format('ASC')}), ({probe.format('DESC')})"
    " FROM unnest(%s::text[]) AS h(host)"
  )
  one = [start, end, *scope_params]
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [*one, *one, list(hosts)])
    return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}


def hosts_owning_mid_sample(
  hosts: list[str],
  start: Any,
  end: Any,
  jid: str,
) -> set[str]:
  """
  Hosts whose first sample at or after the window midpoint has a job row.

  One ``(host, time)`` probe per host finds that sample; only its rows are
  checked against the job scope. A host the job runs on carries job rows at
  every sample, so the midpoint decides ownership in one sample's worth of
  rows. A "first owned row" probe instead reads the host's whole window
  when the job owns nothing there (accounting hosts without job rows: job
  71526 on hpcperfstats04 timed out on 5 of 16 hosts).

  Args:
    hosts (list[str]): Hostnames.
    start (Any): Window start.
    end (Any): Window end.
    jid (str): Job id.

  Returns:
    set[str]: Hosts with a job-owned row at their midpoint sample.

  Examples:
    >>> hosts_owning_mid_sample([], None, None, "1")
    set()
  """
  if not hosts or start is None or end is None or not jid:
    return set()
  from django.db import connection

  scope, scope_params = _scope_sql(jid)
  mid = start + (end - start) / 2
  # Scalar subquery in the SELECT list, never ``WHERE EXISTS``: hs04 v3
  # flattened EXISTS into a hash semi-join over every job-scoped row of
  # host_data (all chunks, all hosts) and it timed out. A target-list
  # subquery stays a per-host SubPlan with the midpoint probe as InitPlan.
  sql = (
    "SELECT h.host, (SELECT 1 FROM host_data d WHERE d.host = h.host"
    " AND d.time = (SELECT d2.time FROM host_data d2 WHERE d2.host = h.host"
    " AND d2.time >= %s AND d2.time <= %s ORDER BY d2.time LIMIT 1)"
    f" AND {scope} LIMIT 1) FROM unnest(%s::text[]) AS h(host)"
  )
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [mid, end, *scope_params, list(hosts)])
    return {row[0] for row in cursor.fetchall() if row[1] is not None}


def schema_rows_near_bounds(
  host: str,
  first: Any,
  last: Any,
  slice_s: int,
  jid: str | None = None,
) -> list[tuple[Any, Any, Any]]:
  """
  Distinct ``(type, event, unit)`` from the first ``slice_s`` and last sample.

  The monitor forces a full collect (every fast and slow key) on schema and
  job-boundary payloads, so the job's first and last samples carry the whole
  schema; reading only those slices bounds the cost by ``slice_s`` instead of
  the job length.

  Args:
    host (str): Hostname.
    first (Any): First scoped sample time.
    last (Any): Last scoped sample time.
    slice_s (int): Seconds read after ``first``.
    jid (str | None): Job id; ``None`` reads host-wide.

  Returns:
    list[tuple[Any, Any, Any]]: Distinct schema triples.

  Examples:
    >>> schema_rows_near_bounds("h", None, None, 600)
    []
  """
  if first is None or last is None:
    return []
  from datetime import timedelta

  from django.db import connection

  scope, scope_params = _scope_sql(jid)
  sql = (
    "SELECT DISTINCT d.type, d.event, d.unit FROM host_data d"
    " WHERE d.host = %s AND ((d.time >= %s AND d.time <= %s) OR d.time = %s)"
    f" AND {scope}"
  )
  hi = min(last, first + timedelta(seconds=max(1, int(slice_s))))
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [host, first, hi, last, *scope_params])
    return [tuple(row) for row in cursor.fetchall()]


def rows_per_sample_estimate(hosts: list[str], start: Any, end: Any) -> int:
  """
  Max rows in one mid-window sample across ``hosts`` (index probes only).

  Replaces the exact window ``COUNT(*)`` behind the large-job sampling
  heuristic: one ``(host, time)`` probe finds each host's first sample at or
  after the window midpoint, then counts that sample's rows.

  Args:
    hosts (list[str]): Hostnames (callers pass one bounded batch).
    start (Any): Window start.
    end (Any): Window end.

  Returns:
    int: Rows per host-sample (0 when nothing is found).

  Examples:
    >>> rows_per_sample_estimate([], None, None)
    0
  """
  if not hosts or start is None or end is None:
    return 0
  from django.db import connection

  mid = start + (end - start) / 2
  sql = (
    "SELECT COALESCE(MAX((SELECT COUNT(*) FROM host_data d"
    " WHERE d.host = h.host AND d.time = (SELECT d2.time FROM host_data d2"
    " WHERE d2.host = h.host AND d2.time >= %s AND d2.time <= %s"
    " ORDER BY d2.time LIMIT 1))), 0) FROM unnest(%s::text[]) AS h(host)"
  )
  with _profile_phase("db_fetch"), connection.cursor() as cursor:
    cursor.execute(sql, [mid, end, list(hosts)])
    row = cursor.fetchone()
  return int(row[0] or 0) if row else 0


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
