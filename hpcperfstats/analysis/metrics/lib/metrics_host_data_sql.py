"""Set-based PostgreSQL helpers for metrics / update_metrics offload paths."""

from __future__ import annotations

from typing import Any


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
  with connection.cursor() as cursor:
    cursor.execute(sql, [list(hosts), t0, upper_param])
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def jid_scoped_distinct_host_time_count(
  jid: str,
  start: Any,
  end: Any,
) -> int:
  """
  Live distinct sample count for one job window via ``jid_scoped_distinct_host_time_count`` (#8).

  Args:
    jid (str): Job id.
    start (Any): Window start (timestamptz).
    end (Any): Window end (timestamptz).

  Returns:
    int: Sum of per-host ``COUNT(DISTINCT time)``.

  Examples:
    >>> jid_scoped_distinct_host_time_count("", None, None)  # doctest: +SKIP
    0
  """
  from django.db import connection

  if (
    connection.vendor != "postgresql" or not jid or start is None or end is None
  ):
    return 0
  sql = "SELECT jid_scoped_distinct_host_time_count(%s, %s, %s)"
  with connection.cursor() as cursor:
    cursor.execute(sql, [str(jid), start, end])
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
  with connection.cursor() as cursor:
    cursor.execute(sql, [list(hosts), start, end, nb])
    return [row[0] for row in cursor.fetchall() if row[0] is not None]


def host_data_sum_metric_per_sample_rows(
  hosts: list[str],
  time_filter: dict[str, Any],
  typ: str,
  events: list[str],
  val_col: str,
) -> list[dict[str, Any]]:
  """
  Per-(host, sample time) sums via ``host_data_sum_metric_per_sample`` (#10).

  Args:
    hosts (list[str]): Hostnames.
    time_filter (dict[str, Any]): ``time__gte`` / ``time__lte``.
    typ (str): ``host_data.type``.
    events (list[str]): Event names.
    val_col (str): ``arc`` or ``value``.

  Returns:
    list[dict[str, Any]]: Rows with ``host``, ``sample_time``, ``sum_val``.

  Examples:
    >>> host_data_sum_metric_per_sample_rows(
    ...   [], {}, "x", [], "arc"
    ... )  # doctest: +SKIP
    []
  """
  if val_col not in ("arc", "value"):
    return []
  if not hosts or not events:
    return []
  t0 = time_filter.get("time__gte")
  t1 = time_filter.get("time__lte")
  if t0 is None or t1 is None:
    return []
  from django.db import connection

  if connection.vendor != "postgresql":
    return []
  sql = (
    "SELECT host, sample_time, sum_val "
    "FROM host_data_sum_metric_per_sample("
    "%s::text[], %s, %s, %s, %s::text[], %s)"
  )
  with connection.cursor() as cursor:
    cursor.execute(
      sql,
      [list(hosts), t0, t1, typ, list(events), val_col],
    )
    return [
      {"host": h, "time": st, "sum_val": float(sv) if sv is not None else 0.0}
      for h, st, sv in cursor.fetchall()
    ]


def job_arc_cluster_aggregate_value(
  hosts: list[str],
  time_filter: dict[str, Any],
  typ: str,
  events: list[str],
  conv: float,
  *,
  nonnegative_rate: bool = False,
  host_aggregate: str = "mean",
) -> float | None:
  """
  ``job_arc`` cluster value in one SQL query (#6): 5m bucket mean, first-bucket drop.

  Args:
    hosts (list[str]): Hostnames.
    time_filter (dict[str, Any]): ``time__gte`` / ``time__lte``.
    typ (str): ``host_data.type``.
    events (list[str]): Event names.
    conv (float): Unit conversion applied after per-bucket mean.
    nonnegative_rate (bool): Drop negative ``arc`` before aggregation.
    host_aggregate (str): ``mean`` (default) or ``sum`` across per-host means.

  Returns:
    float | None: Aggregated metric, or ``None`` when SQL path cannot run or no data.

  Examples:
    >>> job_arc_cluster_aggregate_value([], {}, "t", [], 1.0)  # doctest: +SKIP
  """
  if not hosts or not events:
    return None
  t0 = time_filter.get("time__gte")
  t1 = time_filter.get("time__lte")
  if t0 is None or t1 is None:
    return None
  from django.db import connection

  if connection.vendor != "postgresql":
    return None
  cross_host = "SUM" if host_aggregate == "sum" else "AVG"
  arc_filter = " AND arc >= 0" if nonnegative_rate else ""
  sql = f"""
    WITH sample AS (
      SELECT host, time, SUM(arc)::double precision AS arc
      FROM host_data
      WHERE host = ANY(%s::text[])
        AND time >= %s AND time <= %s
        AND type = %s AND event = ANY(%s::text[])
        {arc_filter}
      GROUP BY host, time
    ),
    bucketed AS (
      SELECT host,
             time_bucket('5 minutes', time) AS bucket,
             AVG(arc)::double precision AS bavg
      FROM sample
      GROUP BY host, time_bucket('5 minutes', time)
    ),
    ranked AS (
      SELECT host, bavg,
             ROW_NUMBER() OVER (PARTITION BY host ORDER BY bucket) AS rn,
             COUNT(*) OVER (PARTITION BY host) AS cnt
      FROM bucketed
    ),
    trimmed AS (
      SELECT host, bavg * %s AS val
      FROM ranked
      WHERE cnt <= 1 OR rn > 1
    ),
    per_host AS (
      SELECT host, AVG(val)::double precision AS host_mean
      FROM trimmed
      GROUP BY host
    )
    SELECT {cross_host}(host_mean) FROM per_host
  """
  with connection.cursor() as cursor:
    cursor.execute(
      sql,
      [list(hosts), t0, t1, typ, list(events), float(conv)],
    )
    row = cursor.fetchone()
  if not row or row[0] is None:
    return None
  return float(row[0])
