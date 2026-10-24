"""
Set-based PostgreSQL helpers for metrics / update_metrics offload paths.

Attributes:
  LIVE_DISTINCT_STATEMENT_TIMEOUT_MS: Local statement_timeout (ms) for live
    distinct freshen probes.
  _COUNTER_VALUE_COLS: ``host_data`` columns safe to interpolate into SQL.
"""

from __future__ import annotations

from typing import Any

# Short budget for optional live-distinct freshen (not portal default ~120s).
LIVE_DISTINCT_STATEMENT_TIMEOUT_MS = 15000
_COUNTER_VALUE_COLS = frozenset({"arc", "value", "delta"})


def _reduced_event_value_sql(val_col: str) -> str:
  """
  SQL CASE that sums hardware devs, else maxes ``j:`` copies, of one column.

  Placeholders are two sane-ceiling binds, in order. ``%`` in ``LIKE`` is
  doubled for psycopg.

  Args:
    val_col (str): ``arc``, ``value``, or ``delta``.

  Returns:
    str: SQL expression.

  Raises:
    ValueError: ``val_col`` is not ``arc``, ``value``, or ``delta``.

  Examples:
    >>> "MAX" in _reduced_event_value_sql("arc")
    True
  """
  if val_col not in _COUNTER_VALUE_COLS:
    raise ValueError(val_col)
  return f"""
    CASE
      WHEN COUNT(*) FILTER (WHERE COALESCE(h.dev, '') NOT LIKE 'j:%%') > 0
      THEN SUM(h.{val_col}) FILTER (
        WHERE COALESCE(h.dev, '') NOT LIKE 'j:%%'
          AND h.{val_col} IS NOT NULL
          AND abs(h.{val_col}) < %s
      )
      ELSE MAX(h.{val_col}) FILTER (
        WHERE COALESCE(h.dev, '') LIKE 'j:%%'
          AND h.{val_col} IS NOT NULL
          AND abs(h.{val_col}) < %s
      )
    END
  """


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
  with connection.cursor() as cursor:
    cursor.execute(sql, [list(hosts), start, end, nb])
    return [row[0] for row in cursor.fetchall() if row[0] is not None]


def host_data_sum_metric_per_sample_rows(
  hosts: list[str],
  time_filter: dict[str, Any],
  typ: str,
  events: list[str],
  val_col: str,
  *,
  jid: str | None = None,
) -> list[dict[str, Any]]:
  """
  Per-(host, sample time) sums via ``host_data_sum_metric_per_sample`` (#10).

  When ``jid`` is set, uses inline SQL with jid / ``dev=j:<jid>`` scope
  instead of the unscoped PG helper (shared-node co-tenant isolation).

  Args:
    hosts (list[str]): Hostnames.
    time_filter (dict[str, Any]): ``time__gte`` / ``time__lte``.
    typ (str): ``host_data.type``.
    events (list[str]): Event names.
    val_col (str): ``arc`` or ``value``.
    jid (str | None): Optional job id for shared-node scope.

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
  jid_s = str(jid).strip() if jid else ""
  if jid_s:
    from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
      job_host_data_scope_sql,
      job_is_shared_node,
      use_host_wide_residual_scope,
    )

    host_wide = use_host_wide_residual_scope(
      typ, shared=job_is_shared_node(jid_s)
    )
    if host_wide:
      scope = "TRUE"
      scope_params: list[Any] = []
    else:
      scope = job_host_data_scope_sql(table_alias="h")
      scope_params = [jid_s, jid_s]
    from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
      sane_counter_abs_limit,
    )

    limit = sane_counter_abs_limit(val_col)
    if host_wide:
      sql = f"""
        SELECT g.host, g.sample_time,
               SUM(g.evt_val)::double precision AS sum_val
        FROM (
          SELECT h.host::text AS host,
                 h.time AS sample_time,
                 {_reduced_event_value_sql(val_col)} AS evt_val
          FROM host_data h
          WHERE h.host = ANY(%s::text[])
            AND h.time >= %s AND h.time <= %s
            AND h.type = %s AND h.event = ANY(%s::text[])
            AND {scope}
          GROUP BY h.host, h.time, h.event
        ) g
        WHERE g.evt_val IS NOT NULL
        GROUP BY g.host, g.sample_time
      """
      params = [
        limit,
        limit,
        list(hosts),
        t0,
        t1,
        typ,
        list(events),
        *scope_params,
      ]
    else:
      sql = f"""
        SELECT h.host::text AS host,
               h.time AS sample_time,
               SUM(h.{val_col})::double precision AS sum_val
        FROM host_data h
        WHERE h.host = ANY(%s::text[])
          AND h.time >= %s AND h.time <= %s
          AND h.type = %s AND h.event = ANY(%s::text[])
          AND h.{val_col} IS NOT NULL
          AND abs(h.{val_col}) < %s
          AND {scope}
        GROUP BY h.host, h.time
      """
      params = [
        list(hosts),
        t0,
        t1,
        typ,
        list(events),
        limit,
        *scope_params,
      ]
  else:
    sql = (
      "SELECT host, sample_time, sum_val "
      "FROM host_data_sum_metric_per_sample("
      "%s::text[], %s, %s, %s, %s::text[], %s)"
    )
    params = [list(hosts), t0, t1, typ, list(events), val_col]
  with connection.cursor() as cursor:
    cursor.execute(sql, params)
    return [
      {"host": h, "time": st, "sum_val": float(sv) if sv is not None else 0.0}
      for h, st, sv in cursor.fetchall()
    ]


def residual_event_counter_sums(
  hosts: list[str],
  time_filter: dict[str, Any],
  typ: str,
  events: list[str],
  val_col: str = "delta",
) -> list[dict[str, Any]] | None:
  """
  Sum a host-wide residual counter by event, without adding ``j:`` copies.

  Args:
    hosts (list[str]): Hostnames.
    time_filter (dict[str, Any]): ``time__gte`` / ``time__lte``.
    typ (str): ``host_data.type``.
    events (list[str]): Event names.
    val_col (str): ``delta`` or ``arc``.

  Returns:
    list[dict[str, Any]] | None: ``event`` / ``delta_sum`` rows, or ``None``
    when PostgreSQL is not the connection vendor.

  Examples:
    >>> residual_event_counter_sums([], {}, "nfs", [], "delta")
    []
  """
  if val_col not in _COUNTER_VALUE_COLS or not hosts or not events:
    return []
  t0 = time_filter.get("time__gte")
  t1 = time_filter.get("time__lte")
  if t0 is None or t1 is None:
    return []
  from django.db import connection

  if connection.vendor != "postgresql":
    return None
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    sane_counter_abs_limit,
  )

  limit = sane_counter_abs_limit(val_col)
  reduced = _reduced_event_value_sql(val_col)
  sql = f"""
    SELECT g.event, SUM(g.evt_val)::double precision AS delta_sum
    FROM (
      SELECT h.event,
             {reduced} AS evt_val
      FROM host_data h
      WHERE h.host = ANY(%s::text[])
        AND h.time >= %s AND h.time <= %s
        AND h.type = %s AND h.event = ANY(%s::text[])
      GROUP BY h.host, h.time, h.event
    ) g
    WHERE g.evt_val IS NOT NULL
    GROUP BY g.event
  """
  with connection.cursor() as cursor:
    cursor.execute(
      sql,
      [limit, limit, list(hosts), t0, t1, typ, list(events)],
    )
    return [
      {"event": event, "delta_sum": float(total) if total is not None else 0.0}
      for event, total in cursor.fetchall()
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
  jid: str | None = None,
) -> float | None:
  """
  Five-minute ``job_arc`` aggregate, one host batch over the full window.

  Each batch keeps ``time__gte`` and ``time__lte`` so the first 5-minute
  bucket drops once per host. Batch means fold by host count. A statement
  timeout splits that batch's hosts. A timeout on one host is re-raised.

  Args:
    hosts (list[str]): Hostnames.
    time_filter (dict[str, Any]): ``time__gte`` / ``time__lte``.
    typ (str): ``host_data.type``.
    events (list[str]): Event names.
    conv (float): Unit conversion applied after per-bucket mean.
    nonnegative_rate (bool): Drop negative ``arc`` before aggregation.
    host_aggregate (str): ``mean`` (default) or ``sum`` across per-host means.
    jid (str | None): Optional job id for shared-node jid / ``j:`` scope.

  Returns:
    float | None: Count-weighted mean, or the sum of per-host means, or
    ``None`` when SQL cannot run or no host returned a mean.

  Raises:
    Exception: Re-raised ``OperationalError`` when one host still times
    out, or when the database error is not a statement timeout.

  Examples:
    >>> job_arc_cluster_aggregate_value(
    ...   [],
    ...   {"time__gte": 0, "time__lte": 1},
    ...   "nfs",
    ...   ["READ_ops"],
    ...   1.0,
    ... )
    None
  """
  if not hosts or not events:
    return None
  t0 = time_filter.get("time__gte")
  t1 = time_filter.get("time__lte")
  if t0 is None or t1 is None:
    return None
  from django.db import OperationalError, close_old_connections, connection

  if connection.vendor != "postgresql":
    return None
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _aggregate_df_host_batch,
    _is_statement_timeout_error,
    _iter_acct_host_batches,
  )
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    job_host_data_scope_sql,
    job_is_shared_node,
    use_host_wide_residual_scope,
  )

  arc_filter = " AND arc >= 0" if nonnegative_rate else ""
  jid_s = str(jid).strip() if jid else ""
  host_wide = bool(jid_s) and use_host_wide_residual_scope(
    typ, shared=job_is_shared_node(jid_s)
  )
  if jid_s and not host_wide:
    scope = " AND " + job_host_data_scope_sql(table_alias="")
    scope_params: list[Any] = [jid_s, jid_s]
  else:
    scope = ""
    scope_params = []
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    sane_counter_abs_limit,
  )

  limit = sane_counter_abs_limit("arc")
  if host_wide:
    sample_sql = f"""
      raw AS (
        SELECT host, time, event, dev, arc
        FROM host_data
        WHERE host = ANY(%s::text[])
          AND time >= %s AND time <= %s
          AND type = %s AND event = ANY(%s::text[])
          AND arc IS NOT NULL AND abs(arc) < %s
          {arc_filter}
          {scope}
      ),
      per_event AS (
        SELECT host, time,
          CASE
            WHEN COUNT(*) FILTER (WHERE COALESCE(dev, '') NOT LIKE 'j:%%') > 0
            THEN SUM(arc) FILTER (WHERE COALESCE(dev, '') NOT LIKE 'j:%%')
            ELSE MAX(arc) FILTER (WHERE COALESCE(dev, '') LIKE 'j:%%')
          END AS arc
        FROM raw
        GROUP BY host, time, event
      ),
      sample AS (
        SELECT host, time, SUM(arc)::double precision AS arc
        FROM per_event
        WHERE arc IS NOT NULL
        GROUP BY host, time
      )
    """
  else:
    sample_sql = f"""
      sample AS (
        SELECT host, time, SUM(arc)::double precision AS arc
        FROM host_data
        WHERE host = ANY(%s::text[])
          AND time >= %s AND time <= %s
          AND type = %s AND event = ANY(%s::text[])
          AND arc IS NOT NULL AND abs(arc) < %s
          {arc_filter}
          {scope}
        GROUP BY host, time
      )
    """
  sql = f"""
    WITH {sample_sql},
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
    SELECT AVG(host_mean)::double precision, COUNT(*) FROM per_host
  """
  batch = _aggregate_df_host_batch(typ, events, reject_dcgm_blank=False)
  weighted = 0.0
  seen = 0
  pending = [list(part) for part in _iter_acct_host_batches(list(hosts), batch)]
  while pending:
    chunk = pending.pop(0)
    try:
      with connection.cursor() as cursor:
        cursor.execute(
          sql,
          [
            chunk,
            t0,
            t1,
            typ,
            list(events),
            limit,
            *scope_params,
            float(conv),
          ],
        )
        row = cursor.fetchone()
    except OperationalError as exc:
      if not _is_statement_timeout_error(exc) or len(chunk) <= 1:
        raise
      close_old_connections()
      mid = len(chunk) // 2
      pending[:0] = [chunk[:mid], chunk[mid:]]
      continue
    if not row or row[0] is None or not row[1]:
      continue
    n = int(row[1])
    weighted += float(row[0]) * n
    seen += n
  if seen == 0:
    return None
  if host_aggregate == "sum":
    return weighted
  return weighted / seen
