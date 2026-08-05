"""
Job-scoped host_data access via Django ORM. Provides jid_table,.

TypeDetailDataProvider, and HostDataProvider for querying job/host metrics
without raw SQL. Uses Redis caching for heavy queries.

Attributes:
  HOST_DATA_SUM_VAL_ALIAS: ``HOST_DATA_SUM_VAL_ALIAS``.
  HOST_DATA_TIME_ALIAS: ``HOST_DATA_TIME_ALIAS``.
  JID_TABLE_HOST_QUERY_BATCH: ``JID_TABLE_HOST_QUERY_BATCH``.
  TYPE_DETAIL_HOST_QUERY_BATCH: ``TYPE_DETAIL_HOST_QUERY_BATCH``.
  _STATEMENT_TIMEOUT_ERROR_MARKERS: ``_STATEMENT_TIMEOUT_ERROR_MARKERS``.
  _logger: ``_logger``.
  _summary_agg_count: ``_summary_agg_count``.
  _summary_agg_count_lock: ``_summary_agg_count_lock``.
  _summary_agg_counting: ``_summary_agg_counting``.
  local_timezone: ``local_timezone``.
"""
from __future__ import annotations

from typing import Any, Iterator

import contextlib
import hashlib
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from datetime import timezone as dt_utc

from django.core.cache import cache
from django.db import InterfaceError, OperationalError, close_old_connections, connections
from django.db.models import (
    DateTimeField,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.analysis.metrics.lib.gen.utils import queryset_to_dataframe
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.site.lib.machine.cache_utils import (
    KEY_AGG_DF,
    KEY_JOB_HOST_LIST,
    KEY_JOB_JID_TABLE_WINDOW,
    KEY_JOB_SCHEMA,
    KEY_HOST_DATA_DF,
    KEY_HOST_SCHEMA,
    KEY_HOST_TIME_DF,
    KEY_JID_HOST_WINDOW_ROW_COUNT,
    KEY_LLITE_DELTA,
    KEY_NFS_FSIO,
    KEY_TYPE_DETAIL_AGG,
    KEY_TYPE_DETAIL_HOST_TIME,
    cached_orm,
    get_site_content_cache_timeout,
    make_cache_key,
    make_cache_key_bounded,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    canonical_event_name_for_type,
    events_probe_names,
    type_probe_names,
)
from hpcperfstats.site.lib.machine.models import host_data, job_data

# Chunk host__in on host_data for large jobs; single queries with thousands of
# hosts often hit PostgreSQL statement_timeout during metrics and plots.
JID_TABLE_HOST_QUERY_BATCH = 64
# Smaller batches for type-detail plots (many events × long windows × mdc, etc.).
TYPE_DETAIL_HOST_QUERY_BATCH = 8

_STATEMENT_TIMEOUT_ERROR_MARKERS = (
    "statement timeout",
    "canceling statement due to statement timeout",
    "querycanceled",
)

# Alias for every SQL-side per-(host, time) sum of a host_data value column.
HOST_DATA_SUM_VAL_ALIAS = "sum_val"
# Grouping alias for the sample timestamp. ``time`` cannot be reused as an
# annotation alias (it is a model field) and must not be grouped on directly;
# see host_data_sum_val_per_sample_queryset.
HOST_DATA_TIME_ALIAS = "sample_time"


def host_data_sum_val_annotation(
  column: Any,
  *,
  nonnegative_only: bool = False,
  coalesce_zero: bool = True,
) -> Any:
  """
  Float-typed ``SUM(column)`` for a per-(host, time) host_data aggregate.
  
  ``host_data.value``/``arc``/``delta`` are ``RealField``, so pairing ``Sum``
    with
  an integer ``Value(0)`` makes Django refuse to resolve the expression type
  (``FieldError: Expression contains mixed types: RealField, IntegerField``)
    when
  the query is compiled — the annotation must carry an explicit float
  ``output_field``.
  
  ``coalesce_zero`` keeps parity with pandas ``groupby().sum()``, which returns
  ``0.0`` for a group whose samples are all NULL; pass ``False`` where callers
  historically saw ``NULL`` (missing) for such groups. ``nonnegative_only``
    drops
  negative samples (counter resets, bad rollover width) from the sum via an
  aggregate ``FILTER`` so the ``(host, time)`` group still exists, matching
  ``Series.where(s >= 0).sum()``.
  
  Args:
    column (Any): Column passed to this helper.
    nonnegative_only (bool): Boolean flag for nonnegative only.
    coalesce_zero (bool): Boolean flag for coalesce zero.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> host_data_sum_val_annotation(None, True, True)  # doctest: +SKIP
  """
  sum_filter = Q(**{"{0}__gte".format(column): 0}) if nonnegative_only else None
  aggregate = Sum(column, filter=sum_filter, output_field=FloatField())
  if not coalesce_zero:
    return aggregate
  return Coalesce(aggregate, Value(0.0), output_field=FloatField())


def host_data_sum_val_per_sample_queryset(
  qs: Any,
  column: Any,
  *,
  nonnegative_only: bool = False,
  coalesce_zero: bool = True,
) -> Any:
  """
  One row per (host, sample time) with ``column`` summed by PostgreSQL.
  
  ``host_data.time`` is declared ``primary_key=True`` even though the table is
  unique on (time, host, type, event, dev). PostgreSQL advertises
  ``allows_group_by_selected_pks``, so Django treats every other column as
  functionally dependent on ``time`` and silently drops ``host`` from
  ``.values("host", "time").annotate(...)`` — the aggregate then sums across all
  hosts at each timestamp. Grouping on an ``ExpressionWrapper`` keeps the
  timestamp out of that optimization, so GROUP BY stays (host, time).
  
  Rows carry ``HOST_DATA_TIME_ALIAS`` instead of ``time``; use
  ``host_data_restore_time_column`` on the resulting DataFrame.
  
  Args:
    qs (Any): Qs passed to this helper.
    column (Any): Column passed to this helper.
    nonnegative_only (bool): Boolean flag for nonnegative only.
    coalesce_zero (bool): Boolean flag for coalesce zero.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> host_data_sum_val_per_sample_queryset(None, None, True, True)
  """
  return (
      qs.annotate(
          **{
              HOST_DATA_TIME_ALIAS:
                  ExpressionWrapper(F("time"), output_field=DateTimeField())
          }
      )
      .values("host", HOST_DATA_TIME_ALIAS)
      .annotate(
          **{
              HOST_DATA_SUM_VAL_ALIAS:
                  host_data_sum_val_annotation(
                      column,
                      nonnegative_only=nonnegative_only,
                      coalesce_zero=coalesce_zero,
                  )
          }
      )
      .order_by("host", HOST_DATA_TIME_ALIAS)
  )


def host_data_restore_time_column(df: Any) -> Any:
  """
  Rename ``HOST_DATA_TIME_ALIAS`` back to ``time`` on an aggregate DataFrame.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> host_data_restore_time_column(None)  # doctest: +SKIP
  """
  if df is None:
    return df
  if HOST_DATA_TIME_ALIAS in getattr(df, "columns", ()):
    return df.rename(columns={HOST_DATA_TIME_ALIAS: "time"})
  return df


def is_metrics_compute_control_flow_error(exc: Any) -> Any:
  """
  True for wall-clock cancellations that must never be swallowed by a fallback.
  
  ``update_metrics`` pool workers bound each job with ``SIGALRM``
  (``MetricsComputeJobTimeoutError``, a ``TimeoutError``). The alarm is one-
    shot,
  so retrying a slower path inside ``except Exception`` runs unbounded.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> is_metrics_compute_control_flow_error(None)  # doctest: +SKIP
  """
  return isinstance(exc, TimeoutError)


def _coerce_jid_table_host_query_batch_size(batch_size: int) -> Any:
  """
  Parse optional per-chunk size for ``host__in`` batching; never raise on bad.
  
    input.
  
  batch size is expected; ``int("c641-092.vista.tacc.utexas.edu")`` would break
  large-job row counting.
  
  Args:
    batch_size (int): Integer value for batch size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerce_jid_table_host_query_batch_size(0)  # doctest: +SKIP
  """
  default = JID_TABLE_HOST_QUERY_BATCH
  if batch_size is None:
    return default
  try:
    n = int(batch_size)
  except (TypeError, ValueError, OverflowError):
    logging.getLogger(__name__).warning(
        "jid_table invalid host query batch_size %r; using default %s",
        batch_size,
        default,
    )
    return default
  if n < 1:
    return default
  return n


def _listify_acct_hosts(acct_hosts: Any) -> Any:
  """
  Coerce accounting host input to a list of hostname strings.
  
  ``job_data.host_list`` is normally a list of short names. A buggy import or
  serializer may store a **single FQDN string** (or comma-separated names) in
    the
  column. Using ``list("host.example.com")`` splits into characters, which
  breaks ``host__in`` chunking and can trigger obscure driver errors (including
  ``invalid literal for int()`` on fragments).
  
  Args:
    acct_hosts (Any): Acct hosts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _listify_acct_hosts(None)  # doctest: +SKIP
  """
  def _norm_scalar(x: Any) -> Any:
    """
    Internal helper to handle norm scalar.
    
    Args:
      x (Any): X passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _norm_scalar(None)  # doctest: +SKIP
    """
    if x is None:
      return []
    if isinstance(x, bytes):
      s = x.decode("utf-8", errors="replace").strip()
      if not s:
        return []
      if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
      return [s]
    if isinstance(x, str):
      s = x.strip()
      if not s:
        return []
      if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
      return [s]
    return [str(x)]

  out = []
  stack = [acct_hosts]
  seen_container_ids = set()
  while stack:
    cur = stack.pop()
    if cur is None:
      continue
    if isinstance(cur, (str, bytes)):
      out.extend(_norm_scalar(cur))
      continue
    if isinstance(cur, dict):
      out.extend(_norm_scalar(cur))
      continue
    if isinstance(cur, (list, tuple, set, deque)):
      cid = id(cur)
      if cid in seen_container_ids:
        continue
      seen_container_ids.add(cid)
      stack.extend(reversed(list(cur)))
      continue
    try:
      it = iter(cur)
    except TypeError:
      out.extend(_norm_scalar(cur))
      continue
    stack.extend(reversed(list(it)))
  if not out:
    return []
  # Preserve order while dropping duplicates/empties.
  deduped = []
  seen = set()
  for h in out:
    hs = str(h).strip()
    if not hs or hs in seen:
      continue
    seen.add(hs)
    deduped.append(hs)
  return deduped


@contextlib.contextmanager
def _pg_relax_statement_timeout_for_large_job_time_sql() -> Iterator[Any]:
  """
  Disable PostgreSQL ``statement_timeout`` for long strided-time sampling SQL.
  
  ``DISTINCT`` + ``NTILE`` and wide-window ``date_bin`` aggregates can exceed
    the
  default session limit; restore the configured timeout when the block exits
  (same pattern as metrics batch queries in ``update_metrics``).
  
  Yields:
    Iterator[Any]: Open return polymorphism from
    ``_pg_relax_statement_timeout_for_large_job_time_sql``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _pg_relax_statement_timeout_for_large_job_time_sql()  # doctest: +SKIP
  """
  conn = connections["default"]
  if conn.vendor != "postgresql":
    yield
    return
  restore_ms = cfg.get_db_statement_timeout_ms()
  with conn.cursor() as cursor:
    cursor.execute("SET statement_timeout = 0")
  try:
    yield
  finally:
    with conn.cursor() as cursor:
      if restore_ms > 0:
        cursor.execute("SET statement_timeout = %s", [restore_ms])
      else:
        cursor.execute("SET statement_timeout = 0")


def _iter_acct_host_batches(
  acct_host_list: Any,
  batch_size: Any | None = None,
) -> Iterator[Any]:
  """
  Yield successive host__in subsets of acct_host_list (stable order).
  
  Args:
    acct_host_list (Any): Acct host list passed to this helper.
    batch_size (Any | None): One of ``Any``, ``None``.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_acct_host_batches(None, None)  # doctest: +SKIP
  """
  hosts = _listify_acct_hosts(acct_host_list)
  if not hosts:
    return
  bs = _coerce_jid_table_host_query_batch_size(batch_size)
  for i in range(0, len(hosts), bs):
    yield hosts[i:i + bs]


def _is_statement_timeout_error(exc: Any) -> Any:
  """
  True when PostgreSQL canceled the statement due to timeout (splittable).
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_statement_timeout_error(None)  # doctest: +SKIP
  """
  if not isinstance(exc, OperationalError):
    return False
  msg = str(exc).lower()
  return any(marker in msg for marker in _STATEMENT_TIMEOUT_ERROR_MARKERS)


def _queryset_to_dataframe_with_host_chunk_retry(
  host_chunk: Any,
  build_qs: Any,
  *,
  min_hosts: int = 1,
  max_attempts: int = 2,
) -> Any:
  """
  Materialize ``build_qs(host_chunk)``; split hosts or retry on statement.
  
    timeout.
  
  Args:
    host_chunk (Any): Host chunk passed to this helper.
    build_qs (Any): Build qs passed to this helper.
    min_hosts (int): Integer value for min hosts.
    max_attempts (int): Integer value for max attempts.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_queryset_to_dataframe_with_host_chunk_retry``
    hits a ``Exception`` failure path.
    last_exc: Raised when ``_queryset_to_dataframe_with_host_chunk_retry``
    hits a ``last_exc`` failure path.
  
  Examples:
    >>> _queryset_to_dataframe_with_host_chunk_retry(None, None, 0, 0)
  """
  import pandas as pd

  hosts = [str(h) for h in host_chunk if h]
  if not hosts:
    return pd.DataFrame()
  last_exc = None
  for attempt in range(max_attempts):
    try:
      close_old_connections()
      return queryset_to_dataframe(build_qs(hosts))
    except OperationalError as exc:
      last_exc = exc
      if not _is_statement_timeout_error(exc):
        raise
      if len(hosts) > min_hosts:
        mid = max(1, len(hosts) // 2)
        left = _queryset_to_dataframe_with_host_chunk_retry(
            hosts[:mid],
            build_qs,
            min_hosts=min_hosts,
            max_attempts=max_attempts,
        )
        right = _queryset_to_dataframe_with_host_chunk_retry(
            hosts[mid:],
            build_qs,
            min_hosts=min_hosts,
            max_attempts=max_attempts,
        )
        if left.empty:
          return right
        if right.empty:
          return left
        return pd.concat([left, right], ignore_index=True)
      if attempt + 1 >= max_attempts:
        raise
      close_old_connections()
  if last_exc is not None:
    raise last_exc
  return pd.DataFrame()


def _fetch_host_data_values_frames(
  host_list: Any,
  build_qs: Any,
  batch_size: Any | None = None,
) -> Any:
  """
  Run chunked ``host__in`` queries with timeout split/retry; concat row frames.
  
  Args:
    host_list (Any): Host list passed to this helper.
    build_qs (Any): Build qs passed to this helper.
    batch_size (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _fetch_host_data_values_frames(None, None, None)  # doctest: +SKIP
  """
  import pandas as pd

  frames = []
  for host_chunk in _iter_acct_host_batches(host_list, batch_size):
    chunk_df = _queryset_to_dataframe_with_host_chunk_retry(host_chunk, build_qs)
    if chunk_df is not None and not chunk_df.empty:
      frames.append(chunk_df)
  if not frames:
    return pd.DataFrame()
  return pd.concat(frames, ignore_index=True)


def _type_detail_group_metric_to_sum_val(df_raw: Any, metric: Any) -> Any:
  """
  Group raw host/time/metric rows into sum_val (pandas aggregate fallback).
  
  Args:
    df_raw (Any): Df raw passed to this helper.
    metric (Any): Metric passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _type_detail_group_metric_to_sum_val(None, None)  # doctest: +SKIP
  """
  import pandas as pd

  if (
      df_raw.empty
      or "host" not in df_raw.columns
      or "time" not in df_raw.columns
      or metric not in df_raw.columns
  ):
    return pd.DataFrame(columns=["host", "time", "sum_val"])
  return (
      df_raw.groupby(["host", "time"], as_index=False)[metric]
      .sum()
      .rename(columns={metric: "sum_val"})
      .sort_values(["host", "time"])
  )


def _type_detail_concat_sum_val_frames(frames: Any) -> Any:
  """
  Concat SQL-aggregated per-chunk frames (host, time, sum_val).
  
  Args:
    frames (Any): Frames passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _type_detail_concat_sum_val_frames(None)  # doctest: +SKIP
  """
  import pandas as pd

  if not frames:
    return pd.DataFrame(columns=["host", "time", "sum_val"])
  df = host_data_restore_time_column(pd.concat(frames, ignore_index=True))
  if df.empty or "sum_val" not in df.columns:
    return pd.DataFrame(columns=["host", "time", "sum_val"])
  if not {"host", "time"}.issubset(df.columns):
    return pd.DataFrame(columns=["host", "time", "sum_val"])
  return df.sort_values(["host", "time"]).reset_index(drop=True)


def _unwrap_singleton_scalar(value: Any) -> Any:
  """
  Unwrap one-element list/tuple/deque-style wrappers to a scalar.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unwrap_singleton_scalar(None)  # doctest: +SKIP
  """
  if value is None:
    return None
  cur = value
  for _ in range(24):
    if isinstance(cur, (list, tuple, deque)) and len(cur) == 1:
      cur = cur[0]
      continue
    # pandas / numpy wrappers often expose one logical element.
    if isinstance(cur, (str, bytes, bytearray, dict, set)):
      break
    if hasattr(cur, "__len__") and hasattr(cur, "__getitem__"):
      try:
        ln = len(cur)
      except Exception:
        break
      if ln == 1:
        try:
          cur = cur[0]
        except Exception:
          break
        continue
      if ln > 1:
        return None
    break
  return cur


def _normalize_window_bound_datetime(value: Any) -> Any:
  """
  Coerce cache/ORM window bound payloads to a datetime scalar or ``None``.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_window_bound_datetime(None)  # doctest: +SKIP
  """
  cur = _unwrap_singleton_scalar(value)
  if cur is None:
    return None
  # pandas.Timestamp, numpy datetime64 scalar wrappers.
  if hasattr(cur, "to_pydatetime"):
    try:
      cur = cur.to_pydatetime()
    except Exception:
      return None
  if isinstance(cur, datetime):
    return cur
  return None


def _is_psycopg_connection_desync(exc: Any) -> Any:
  """
  True for errors that warrant a fresh DB connection and one retry.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_psycopg_connection_desync(None)  # doctest: +SKIP
  """
  if isinstance(exc, (InterfaceError, OperationalError)):
    return True
  return "lost synchronization" in str(exc).lower()


def _count_host_data_rows_for_window(
  start: Any,
  end: Any,
  acct_hosts: Any,
) -> Any:
  """
  Total host_data rows in [start, end] for accounting FQDNs.
  
  Always uses chunked ``host__in`` queries. A previous PostgreSQL fast path
  used ``ANY(%s::text[])`` on a shared Django connection and could corrupt the
  psycopg wire protocol (``lost synchronization with server``).
  
  Retries once after :func:`close_old_connections` when the connection is
  desynchronized (same class of errors as raw-SQL / concurrent use).
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_count_host_data_rows_for_window`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _count_host_data_rows_for_window(None, None, None)  # doctest: +SKIP
  """
  start_dt = _normalize_window_bound_datetime(start)
  end_dt = _normalize_window_bound_datetime(end)
  if start_dt is None or end_dt is None:
    return 0
  hosts = _listify_acct_hosts(acct_hosts)
  if not hosts:
    return 0
  for attempt in range(2):
    try:
      close_old_connections()
      total = 0
      for host_chunk in _iter_acct_host_batches(hosts):
        clean_chunk = [
            h for h in (
                _normalize_host_cell_for_host_data(h) for h in host_chunk
            ) if h
        ]
        if not clean_chunk:
          continue
        total += host_data.objects.filter(
            time__gte=start_dt,
            time__lte=end_dt,
            host__in=clean_chunk,
        ).count()
      return total
    except Exception as exc:
      if attempt == 0 and _is_psycopg_connection_desync(exc):
        continue
      raise


def _acct_hosts_cache_fingerprint(acct_hosts: Any) -> Any:
  """
  Internal helper to handle accounting hosts cache fingerprint.
  
  Args:
    acct_hosts (Any): Acct hosts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _acct_hosts_cache_fingerprint(None)  # doctest: +SKIP
  """
  hosts = _listify_acct_hosts(acct_hosts)
  blob = "\n".join(sorted(hosts))
  return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _coerce_nonnegative_window_row_count(value: Any) -> Any:
  """
  Parse a cached or computed window row count as a non-negative int.
  
  Some cache serializers (JSON, or upstream bugs) surface a scalar count as a
  one-element sequence (e.g. ``[1_500_000]``). Unwrap shallowly, then ``int()``;
  return ``None`` if the value is not a usable count.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerce_nonnegative_window_row_count(None)  # doctest: +SKIP
  """
  if value is None:
    return None
  cur = value
  for _ in range(24):
    if isinstance(cur, (list, tuple)) and len(cur) == 1:
      cur = cur[0]
      continue
    if isinstance(cur, deque) and len(cur) == 1:
      cur = cur[0]
      continue
    # NumPy scalars / 0-d arrays, pandas Series (len 1), etc.: one logical element.
    if isinstance(cur, (str, bytes, dict, set)):
      break
    if hasattr(cur, "__len__") and hasattr(cur, "__getitem__"):
      try:
        ln = len(cur)
      except Exception:
        break
      if ln == 1:
        try:
          cur = cur[0]
        except Exception:
          break
        continue
    break
  if isinstance(cur, bool):
    return None
  try:
    n = int(cur)
  except Exception:
    return None
  return n if n >= 0 else None


def _safe_positive_int_ttl_seconds(raw_ttl: Any, *, default: Any) -> Any:
  """
  Cache timeout for row-count entries; never raises (misconfig / odd cache.
  
    types).
  
  Args:
    raw_ttl (Any): Raw ttl passed to this helper.
    default (Any): Default passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _safe_positive_int_ttl_seconds(None, None)  # doctest: +SKIP
  """
  try:
    n = int(raw_ttl)
  except Exception:
    return default
  return n if n >= 0 else 0


def _job_window_iso_pair_for_cache_key(start: Any, end: Any) -> Any:
  """
  Return ``(start_iso, end_iso)`` or ``None`` if bounds are not cache-key safe.
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _job_window_iso_pair_for_cache_key(None, None)  # doctest: +SKIP
  """
  start_dt = _normalize_window_bound_datetime(start)
  end_dt = _normalize_window_bound_datetime(end)
  if start_dt is None or end_dt is None:
    return None
  try:
    return (start_dt.isoformat(), end_dt.isoformat())
  except Exception:
    return None


def _count_host_data_rows_for_window_cached(
  jid: Any,
  start: Any,
  end: Any,
  acct_hosts: Any,
) -> Any:
  """
  Exact window COUNT(*) with optional short Django-cache TTL (see conf_parser).
  
  Args:
    jid (Any): Jid passed to this helper.
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _count_host_data_rows_for_window_cached(None, None, None, None)
  """
  default_ttl = 300
  try:
    ttl_raw = cfg.get_large_job_window_row_count_cache_ttl()
  except Exception:
    ttl_raw = default_ttl
  ttl = _safe_positive_int_ttl_seconds(ttl_raw, default=default_ttl)
  iso_pair = _job_window_iso_pair_for_cache_key(start, end)
  if ttl > 0 and jid and iso_pair is not None:
    key = make_cache_key(
        KEY_JID_HOST_WINDOW_ROW_COUNT,
        jid,
        iso_pair[0],
        iso_pair[1],
        _acct_hosts_cache_fingerprint(acct_hosts),
    )
    try:
      cached = cache.get(key)
    except Exception:
      cached = None
    if cached is not None:
      coerced = _coerce_nonnegative_window_row_count(cached)
      if coerced is not None:
        return coerced
  try:
    n = _count_host_data_rows_for_window(start, end, acct_hosts)
  except Exception as exc:
    log_print(
        "[jid_table] window row count count-fallback jid={0}: {1}".format(
            jid, exc
        ),
        flush=True,
    )
    return 0
  coerced_n = _coerce_nonnegative_window_row_count(n)
  if coerced_n is None:
    log_print(
        "[jid_table] window row count not coercible jid={0} raw={1!r}".format(
            jid, n
        ),
        flush=True,
    )
    return 0
  if ttl > 0 and jid and iso_pair is not None:
    try:
      cache.set(key, coerced_n, timeout=ttl)
    except Exception:
      pass
  return coerced_n


def _distinct_times_in_window_batched(
  start: Any,
  end: Any,
  acct_hosts: Any,
  batch_size: Any | None = None,
) -> Any:
  """
  UNION of DISTINCT ``host_data.time`` in ``[start, end]`` across hosts.
  
  Uses chunked ``host__in`` ORM queries (same batch size as row counts). Raw SQL
  with ``ANY(%s::text[])`` on Django's shared connection previously corrupted
    the
  psycopg wire protocol (``lost synchronization with server``).
  
  Retries once after :func:`close_old_connections` on connection desync.
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
    batch_size (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_distinct_times_in_window_batched`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _distinct_times_in_window_batched(None, None, None, None)
  """
  if start is None or end is None:
    return []
  hosts = _listify_acct_hosts(acct_hosts)
  if not hosts:
    return []
  for attempt in range(2):
    try:
      close_old_connections()
      all_ts = set()
      with _pg_relax_statement_timeout_for_large_job_time_sql():
        for host_chunk in _iter_acct_host_batches(hosts, batch_size):
          qs = (
              host_data.objects.filter(
                  time__gte=start,
                  time__lte=end,
                  host__in=list(host_chunk),
              )
              .values_list("time", flat=True)
              .distinct()
          )
          for ts in qs:
            if ts is not None:
              all_ts.add(ts)
      return sorted(all_ts)
    except Exception as exc:
      if attempt == 0 and _is_psycopg_connection_desync(exc):
        continue
      raise


def _ntile_bucket_max_timestamps(sorted_ts: Any, n_buckets: Any) -> Any:
  """
  Replicate ``NTILE(n_buckets) OVER (ORDER BY ts)`` then ``MAX(ts)`` per bucket.
  
  Args:
    sorted_ts (Any): Sorted ts passed to this helper.
    n_buckets (Any): N buckets passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ntile_bucket_max_timestamps(None, None)  # doctest: +SKIP
  """
  L = len(sorted_ts)
  if L == 0:
    return []
  try:
    nb = int(n_buckets)
  except (TypeError, ValueError, OverflowError):
    nb = 2048
  if nb < 2:
    return [sorted_ts[-1]]
  base = L // nb
  rem = L % nb
  out = []
  idx = 0
  for b in range(nb):
    sz = base + (1 if b < rem else 0)
    if sz == 0:
      continue
    out.append(sorted_ts[idx + sz - 1])
    idx += sz
  return out


def _date_bin_bucket_maxima(sorted_ts: Any, start: Any, step_sec: Any) -> Any:
  """
  ``MAX(ts)`` per ``date_bin(step, ts, start)``-style bucket (sorted input).
  
  Args:
    sorted_ts (Any): Sorted ts passed to this helper.
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    step_sec (Any): Step sec passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _date_bin_bucket_maxima(None, None, None)  # doctest: +SKIP
  """
  if not sorted_ts:
    return []
  bins = {}
  for ts in sorted_ts:
    if step_sec <= 0:
      k = 0
    else:
      k = int((ts - start).total_seconds() // step_sec)
    bins[k] = ts
  return [bins[k] for k in sorted(bins)]


def _strided_distinct_times_postgresql(
  start: Any,
  end: Any,
  acct_hosts: Any,
  n_buckets: Any,
) -> Any:
  """
  Up to n_buckets timestamps, one per NTILE bucket over DISTINCT time (ordered).
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
    n_buckets (Any): N buckets passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _strided_distinct_times_postgresql(None, None, None, None)
  """
  if not acct_hosts or start is None or end is None or n_buckets < 2:
    return []
  distinct_sorted = _distinct_times_in_window_batched(start, end, acct_hosts)
  if not distinct_sorted:
    return []
  return _ntile_bucket_max_timestamps(distinct_sorted, n_buckets)


def _strided_distinct_times_date_bin_via_grouped_max_sql(
  start: Any,
  end: Any,
  acct_hosts: Any,
  n_buckets: Any,
  batch_size: Any | None = None,
) -> Any:
  """
  One ``MAX(time)`` per wall-clock stride bucket, merged across host chunks.
  
  Matches the stride grid used by :func:`_date_bin_bucket_maxima` without
  materializing every distinct ``time`` in Python (which is expensive on very
  large windows).
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
    n_buckets (Any): N buckets passed to this helper.
    batch_size (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when
    ``_strided_distinct_times_date_bin_via_grouped_max_sql`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _strided_distinct_times_date_bin_via_grouped_max_sql(0)  # doctest: +SKIP
  """
  start_dt = _normalize_window_bound_datetime(start)
  end_dt = _normalize_window_bound_datetime(end)
  hosts = _listify_acct_hosts(acct_hosts)
  if not hosts or start_dt is None or end_dt is None:
    return []
  span_sec = (end_dt - start_dt).total_seconds()
  if span_sec <= 0:
    return [start_dt]
  try:
    nb = max(2, int(n_buckets))
  except (TypeError, ValueError, OverflowError):
    nb = 2
  step_sec = max(span_sec / float(nb - 1), 1e-9)

  conn = connections["default"]
  if conn.vendor != "postgresql":
    return []

  meta = host_data._meta
  ops = conn.ops
  tbl = ops.quote_name(meta.db_table)
  tcol = ops.quote_name(meta.get_field("time").column)
  hcol = ops.quote_name(meta.get_field("host").column)
  merged = {}

  for attempt in range(2):
    try:
      close_old_connections()
      with _pg_relax_statement_timeout_for_large_job_time_sql():
        for host_chunk in _iter_acct_host_batches(hosts, batch_size):
          clean_chunk = [
              h
              for h in (
                  _normalize_host_cell_for_host_data(h) for h in host_chunk
              )
              if h
          ]
          if not clean_chunk:
            continue
          placeholders = ", ".join(["%s"] * len(clean_chunk))
          sql = (
              "SELECT (FLOOR(EXTRACT(EPOCH FROM ("
              + tbl + "." + tcol + " - %s)) / %s))::bigint AS grp, "
              "MAX(" + tbl + "." + tcol + ") AS mx FROM " + tbl + " "
              "WHERE " + tbl + "." + tcol + " >= %s AND " + tbl + "." + tcol
              + " <= %s AND " + tbl + "." + hcol + " IN (" + placeholders
              + ") GROUP BY 1"
          )
          params = [start_dt, step_sec, start_dt, end_dt] + clean_chunk
          with conn.cursor() as cursor:
            cursor.execute(sql, params)
            for grp, mx in cursor.fetchall():
              if mx is None:
                continue
              prev = merged.get(grp)
              if prev is None or mx > prev:
                merged[grp] = mx
      break
    except Exception as exc:
      if attempt == 0 and _is_psycopg_connection_desync(exc):
        continue
      raise

  if not merged:
    return []
  return [merged[k] for k in sorted(merged)]


def _strided_distinct_times_date_bin_postgresql(
  start: Any,
  end: Any,
  acct_hosts: Any,
  n_buckets: Any,
) -> Any:
  """
  Up to ~n_buckets timestamps via date-bin-style buckets (Python, same grid as.
  
    PG).
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
    n_buckets (Any): N buckets passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _strided_distinct_times_date_bin_postgresql(None, None, None, None)
  """
  if not acct_hosts or start is None or end is None or n_buckets < 2:
    return []
  span_sec = (end - start).total_seconds()
  if span_sec <= 0:
    return [start]
  if connections["default"].vendor == "postgresql":
    try:
      via_sql = _strided_distinct_times_date_bin_via_grouped_max_sql(
          start, end, acct_hosts, n_buckets)
      if via_sql:
        return via_sql
    except Exception:
      _logger.warning(
          "jid_table date_bin grouped-max SQL failed; falling back to DISTINCT",
          exc_info=True,
      )
  distinct_sorted = _distinct_times_in_window_batched(start, end, acct_hosts)
  if not distinct_sorted:
    return []
  step_sec = max(span_sec / float(max(n_buckets - 1, 1)), 1e-9)
  return _date_bin_bucket_maxima(distinct_sorted, start, step_sec)


def _strided_distinct_times_for_large_job(
  start: Any,
  end: Any,
  acct_hosts: Any,
  n_buckets: Any,
) -> Any:
  """
  Strided sample timestamps: ``date_bin`` when configured, else NTILE path.
  
  Args:
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    acct_hosts (Any): Acct hosts passed to this helper.
    n_buckets (Any): N buckets passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _strided_distinct_times_for_large_job(None, None, None, None)
  """
  if cfg.get_large_job_time_sample_sql_mode() == "date_bin":
    try:
      out = _strided_distinct_times_date_bin_postgresql(
          start, end, acct_hosts, n_buckets)
      if out:
        return out
    except Exception:
      logging.getLogger(__name__).warning(
          "jid_table date_bin strided times failed; falling back to NTILE",
          exc_info=True,
      )
  try:
    return _strided_distinct_times_postgresql(
        start, end, acct_hosts, n_buckets)
  except Exception:
    logging.getLogger(__name__).warning(
        "jid_table NTILE strided times failed; falling back to fixed window points",
        exc_info=True,
    )
    # Keep sampling active even when DB striding paths fail (e.g. statement_timeout)
    # so downstream queries avoid full-window scans for very large jobs.
    if start is None or end is None:
      return []
    if end <= start:
      return [start]
    mid = start + ((end - start) / 2)
    return [start, mid, end]


local_timezone = cfg.get_local_timezone()
_logger = logging.getLogger(__name__)

# Optional instrumentation: count get_aggregate_df entries while building summary plots.
_summary_agg_count_lock = threading.Lock()
_summary_agg_count = 0
_summary_agg_counting = False


def begin_summary_aggregate_counting() -> None:
    """
    Start counting ``get_aggregate_df`` calls (thread-safe).
    
    Returns:
      None
    
    Examples:
      >>> begin_summary_aggregate_counting()  # doctest: +SKIP
    """
    global _summary_agg_counting, _summary_agg_count
    with _summary_agg_count_lock:
        _summary_agg_count = 0
        _summary_agg_counting = True


def end_summary_aggregate_counting() -> Any:
    """
    Stop counting and return the number of ``get_aggregate_df`` calls seen.
    
    Returns:
      Any: Open return polymorphism from ``end_summary_aggregate_counting``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> end_summary_aggregate_counting()  # doctest: +SKIP
    """
    global _summary_agg_counting, _summary_agg_count
    with _summary_agg_count_lock:
        _summary_agg_counting = False
        return _summary_agg_count


def _incr_summary_aggregate_count_if_active() -> None:
    """
    Internal helper to handle incr summary aggregate count if active.
    
    Returns:
      None
    
    Examples:
      >>> _incr_summary_aggregate_count_if_active()  # doctest: +SKIP
    """
    global _summary_agg_count
    if not _summary_agg_counting:
        return
    with _summary_agg_count_lock:
        if _summary_agg_counting:
            _summary_agg_count += 1


def _ensure_tz(dt: Any) -> Any:
  """
  Ensure datetime is timezone-aware in local_timezone for display.
  
  Args:
    dt (Any): Dt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ensure_tz(None)  # doctest: +SKIP
  """
  if dt is None:
    return None
  if dt.tzinfo is None:
    from django.utils import timezone as django_tz
    dt = django_tz.make_aware(dt, dt_utc.utc)
  return dt.astimezone(local_timezone)


def _normalize_host_cell_for_host_data(value: Any) -> Any:
  """
  Coerce ``host_data.host`` cell to a hashable hostname string (set/DISTINCT.
  
    safe).
  
  Bad payloads occasionally store nested sequences in ``host``; ``set.update``
  on queryset rows then raises ``unhashable type: 'list'``.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_host_cell_for_host_data(None)  # doctest: +SKIP
  """
  if value is None:
    return None
  if isinstance(value, bytes):
    s = value.decode("utf-8", errors="replace").strip()
    return s if s else None
  if isinstance(value, str):
    s = value.strip()
    return s if s else None
  if isinstance(value, (list, tuple)):
    for item in value:
      nh = _normalize_host_cell_for_host_data(item)
      if nh:
        return nh
    return None
  if isinstance(value, dict):
    return None
  s = str(value).strip()
  return s if s else None


def _normalize_job_accounting_host_list(raw: Any) -> Any:
  """
  Coerce ``job_data.host_list`` (ArrayField) to a list of short hostnames.
  
  Defensive: corrupted cache/ORM values can surface as a non-list (e.g. a lone
  ``datetime``), which must not be iterated for FQDN construction. A single FQDN
  string (or comma-separated short names) is accepted like
    :func:`_listify_acct_hosts`.
  Nested list payloads are flattened via :func:`_listify_acct_hosts`.
  
  Args:
    raw (Any): Raw passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_job_accounting_host_list(None)  # doctest: +SKIP
  """
  if raw is None:
    return []
  if isinstance(raw, datetime):
    return []
  if isinstance(raw, (list, tuple, set, deque)):
    return _listify_acct_hosts(raw)
  if isinstance(raw, (str, bytes)):
    return _listify_acct_hosts(raw)
  return []


def _host_data_suffix() -> Any:
  """
  Normalized host_data domain suffix with one leading dot (or empty).
  
  Returns:
    Any: Open return polymorphism from ``_host_data_suffix``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _host_data_suffix()  # doctest: +SKIP
  """
  ext = str(cfg.get_host_name_ext() or "").strip()
  ext = ext.lstrip(".")
  return "." + ext if ext else ""


def _as_host_data_fqdn(host: Any) -> Any:
  """
  Return host in host_data FQDN form, avoiding duplicate suffix append.
  
  Args:
    host (Any): Host passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _as_host_data_fqdn(None)  # doctest: +SKIP
  """
  host_s = str(host or "").strip()
  if not host_s:
    return ""
  suffix = _host_data_suffix()
  if not suffix:
    return host_s
  if host_s.lower().endswith(suffix.lower()):
    return host_s
  return host_s + suffix


def _build_acct_host_fqdns(raw_host_list: Any) -> Any:
  """
  Coerce job_data.host_list to host_data lookup FQDNs.
  
  Args:
    raw_host_list (Any): Raw host list passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_acct_host_fqdns(None)  # doctest: +SKIP
  """
  return [
      fqdn for fqdn in (
          _as_host_data_fqdn(h) for h in _normalize_job_accounting_host_list(raw_host_list)
      )
      if fqdn
  ]


def _unpack_cached_job_window_row(row: Any) -> Any:
  """
  Return ``(host_list, start_time, end_time)`` from a cached jid row.
  
  Expects a ``values_list`` tuple of ``(host_list, start_time, end_time)``
    (pickle-safe).
  
  Args:
    row (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unpack_cached_job_window_row(None)  # doctest: +SKIP
  """
  if row is None:
    return None, None, None
  if isinstance(row, (tuple, list)) and len(row) == 3:
    return row[0], row[1], row[2]
  return None, None, None


def gpu_acct_window_for_job_data(job: Any) -> Any:
  """
  Return ``(start_time, end_time, acct_host_list)`` for GPU-style host_data.
  
    queries.
  
  Uses the same FQDN construction as :class:`jid_table` without distinct-host
  discovery, schema scans, or large-job time sampling (those are expensive and
  unnecessary for rolled-up GPU aggregates that only need accounting hosts and
  the job window).
  
  Args:
    job (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> gpu_acct_window_for_job_data(None)  # doctest: +SKIP
  """
  hl_raw = getattr(job, "host_list", None)
  st = _normalize_window_bound_datetime(getattr(job, "start_time", None))
  et = _normalize_window_bound_datetime(getattr(job, "end_time", None))
  if st is None and et is None:
    return _ensure_tz(st), _ensure_tz(et), []
  acct_host_list = [
      h
      for h in _build_acct_host_fqdns(hl_raw)
  ]
  return _ensure_tz(st), _ensure_tz(et), acct_host_list


def _normalize_host_data_schema_label(value: Any) -> Any:
  """
  Coerce ``host_data.type`` / ``event`` cell to a string safe for pandas.
  
    ``.unique()``.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_host_data_schema_label(None)  # doctest: +SKIP
  """
  if value is None:
    return None
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  if isinstance(value, str):
    return value
  if isinstance(value, (list, dict, tuple)):
    try:
      return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
      return str(value)
  return str(value)


def _coerce_jid_table_schema_dataframe(df: Any) -> Any:
  """
  Normalize schema frame so ``type`` / ``event`` are hashable strings (no list.
  
    cells).
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerce_jid_table_schema_dataframe(None)  # doctest: +SKIP
  """
  if df is None or df.empty or "type" not in df.columns:
    return df
  out = df.copy()
  out["type"] = out["type"].map(_normalize_host_data_schema_label)
  if "event" in out.columns:
    out["event"] = out["event"].map(_normalize_host_data_schema_label)
  out = out[out["type"].notna()]
  return out


class jid_table:
  """
  Job-scoped view of job_data and host_data using Django ORM. No raw connection.
  
  Attributes:
    _base_filter: ``_base_filter``.
    _large_job_plot_cache_token: ``_large_job_plot_cache_token``.
    acct_host_list: ``acct_host_list``.
    end_time: ``end_time``.
    host_list: ``host_list``.
    jid: ``jid``.
    schema: ``schema``.
    start_time: ``start_time``.
  """

  def __init__(self, jid: Any) -> None:
    """
    Build job-scoped filter from job_data and populate host_list and schema.
    
      from.
    
      host_data.
    
    Args:
      jid (Any): Jid passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> jid_table(None)  # doctest: +SKIP
    """
    _logger.debug("Initializing jid_table for job %s", jid)

    self.jid = jid
    self._large_job_plot_cache_token = "full"

    try:
      row = cached_orm(
          make_cache_key(KEY_JOB_JID_TABLE_WINDOW, jid),
          get_site_content_cache_timeout(),
          lambda: job_data.objects.filter(jid=jid).values_list(
              "host_list", "start_time", "end_time"
          ).first(),
      )
    except Exception:
      row = None

    if row is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    hl_raw, st_raw, et_raw = _unpack_cached_job_window_row(row)
    st = _normalize_window_bound_datetime(st_raw)
    et = _normalize_window_bound_datetime(et_raw)
    if st is None and et is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    # job_data host_list: use fqdn for host_data lookups (cast to str for varchar comparison)
    self.acct_host_list = _build_acct_host_fqdns(hl_raw)
    self.start_time = _ensure_tz(st)
    self.end_time = _ensure_tz(et)
    self._base_filter = {
        "time__gte": self.start_time,
        "time__lte": self.end_time,
        "host__in": self.acct_host_list,
    }

    # Distinct hosts that actually have host_data in range (cached)
    qtime = time.time()

    def _host_list_fn() -> Any:
      """
      Internal helper to handle host list function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> jid_table()._host_list_fn()  # doctest: +SKIP
      """
      found = set()
      for host_chunk in _iter_acct_host_batches(self.acct_host_list):
        host_qs = (
            host_data.objects.filter(
                time__gte=self._base_filter["time__gte"],
                time__lte=self._base_filter["time__lte"],
                host__in=host_chunk,
            )
            .values_list("host", flat=True)
            .distinct()
        )
        for raw_host in host_qs:
          nh = _normalize_host_cell_for_host_data(raw_host)
          if nh:
            found.add(nh)
      return list(found)

    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    self.host_list = cached_orm(
        make_cache_key(KEY_JOB_HOST_LIST, jid, _st, _et),
        get_site_content_cache_timeout(),
        _host_list_fn,
    ) or []
    _logger.debug("jid_table host_list query time: %.1fs", time.time() - qtime)

    if len(self.host_list) == 0:
      self.schema = {}
      return

    # Schema: distinct (type, event) for one host (cached)
    etime = time.time()

    def _schema_fn() -> Any:
      """
      Return distinct (type, event) pairs for one host as a DataFrame, using.
      
        Django ORM only.
      
      Uses values_list(...).distinct() to avoid the Django bug that can raise
      IndexError in values().distinct() when schema and model definitions
        diverge.
      
      Returns:
        Any: Open return polymorphism from ``_schema_fn``: concrete type
        depends on inputs and branch (mapping, scalar, handle, or
        ``None``-like empty).
      
      Examples:
        >>> jid_table()._schema_fn()  # doctest: +SKIP
      """
      import pandas as pd

      if not self.host_list:
        return pd.DataFrame(columns=["type", "event"])

      raw_rows = list(
          host_data.objects.filter(
              host=str(self.host_list[0]),
              time__gte=self._base_filter["time__gte"],
              time__lte=self._base_filter["time__lte"],
          )
          .values_list("type", "event")
          .distinct()
      )
      # Defensive: some backends/composite PKs can return extra columns or even
      # shorter tuples if the DB schema and model fields diverge. Keep only rows
      # with at least two elements and trim to the first two.
      rows = [tuple(r[:2]) for r in raw_rows if len(r) >= 2]
      if not rows:
        return pd.DataFrame(columns=["type", "event"])
      return _coerce_jid_table_schema_dataframe(
          pd.DataFrame(rows, columns=["type", "event"]))

    schema_df = cached_orm(
        make_cache_key(KEY_JOB_SCHEMA, jid, self.host_list[0]),
        get_site_content_cache_timeout(),
        _schema_fn,
    )
    schema_df = _coerce_jid_table_schema_dataframe(schema_df)
    if schema_df is None or schema_df.empty or "type" not in schema_df.columns:
      self.schema = {}
    else:
      types = sorted(schema_df["type"].unique().tolist())
      self.schema = {}
      for t in types:
        self.schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
    _logger.debug("jid_table schema time: %.1fs", time.time() - etime)
    self._apply_large_job_time_sampling_if_needed()

  def _host_data_time_filter_kwargs(self) -> Any:
    """
    Time scope for host_data queries: full window or sampled ``time__in``.
    
    Returns:
      Any: Open return polymorphism from ``_host_data_time_filter_kwargs``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> jid_table()._host_data_time_filter_kwargs()  # doctest: +SKIP
    """
    if not self._base_filter:
      return {}
    if "time__in" in self._base_filter:
      return {"time__in": self._base_filter["time__in"]}
    return {
        "time__gte": self._base_filter["time__gte"],
        "time__lte": self._base_filter["time__lte"],
    }

  def _apply_large_job_time_sampling_if_needed(self) -> None:
    """
    If row count exceeds threshold, restrict queries to NTILE-strided.
    
      timestamps.
    
    Returns:
      None
    
    Examples:
      >>> jid_table()._apply_large_job_time_sampling_if_needed()  # doctest: +SKIP
    """
    self._large_job_plot_cache_token = "full"
    if not self.acct_host_list or not self.start_time or not self.end_time:
      return
    threshold = cfg.get_large_job_host_data_row_threshold()
    try:
      n = _count_host_data_rows_for_window_cached(
          self.jid, self.start_time, self.end_time, self.acct_host_list)
    except Exception as exc:
      _logger.warning(
          "jid_table large-job row count failed jid=%s: %s",
          self.jid,
          exc,
      )
      return
    if n <= threshold:
      return
    n_buckets = cfg.get_large_job_time_buckets()
    try:
      sampled = _strided_distinct_times_for_large_job(
          self.start_time, self.end_time, self.acct_host_list, n_buckets)
    except Exception as exc:
      _logger.warning(
          "jid_table strided times failed jid=%s: %s",
          self.jid,
          exc,
      )
      return
    if not sampled:
      return
    self._base_filter = {
        "time__in": sampled,
        "host__in": self.acct_host_list,
    }
    self._large_job_plot_cache_token = "lb{}".format(len(sampled))
    _logger.info(
        "jid_table large-job time sampling jid=%s rows~%s bucket_times=%s",
        self.jid,
        n,
        len(sampled),
    )

  def _host_data_qs(self, **extra_filters: Any) -> Any:
    """
    Base host_data queryset for this job (time range + hosts).
    
    Args:
      **extra_filters (Any): Extra keyword arguments (``extra_filters``); keys
      are ``str`` and value types match the wrapped protocol for this helper.
    
    Returns:
      Any: Open return polymorphism from ``_host_data_qs``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> jid_table()._host_data_qs()  # doctest: +SKIP
    """
    if not self._base_filter:
      return host_data.objects.none()
    tkw = self._host_data_time_filter_kwargs()
    return host_data.objects.filter(
        host__in=self._base_filter["host__in"],
        **tkw,
        **extra_filters,
    )

  def _full_host_data_rows_batched(self, cols: Any) -> Any:
    """
    values_list rows for the job window, chunking host__in for large node.
    
      counts.
    
    Args:
      cols (Any): Cols passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> jid_table()._full_host_data_rows_batched(None)  # doctest: +SKIP
    """
    rows = []
    if not self.acct_host_list or not self._base_filter:
      return rows
    tkw = self._host_data_time_filter_kwargs()
    for host_chunk in _iter_acct_host_batches(self.acct_host_list):
      qs = (
          host_data.objects.filter(
              host__in=host_chunk,
              **tkw,
          )
          .values_list(*cols)
          .order_by("host", "time")
      )
      for r in qs:
        if not isinstance(r, (list, tuple)):
          continue
        if len(r) < len(cols):
          continue
        rows.append(tuple(r[:len(cols)]))
    return rows

  def get_host_time_df(self) -> Any:
    """
    DataFrame of (host, time) distinct, ordered by host, time (cached).
    
    Returns:
      Any: Open return polymorphism from ``get_host_time_df``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> jid_table().get_host_time_df()  # doctest: +SKIP
    """
    def _fn() -> Any:
      """
      Internal helper to handle function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> jid_table()._fn()  # doctest: +SKIP
      """
      import pandas as pd

      if not self.acct_host_list:
        return pd.DataFrame(columns=["host", "time"])
      tkw = self._host_data_time_filter_kwargs()
      frames = []
      for host_chunk in _iter_acct_host_batches(self.acct_host_list):
        qs = (
            host_data.objects.filter(
                host__in=host_chunk,
                **tkw,
            )
            .values("host", "time")
            .distinct()
            .order_by("host", "time")
        )
        frames.append(queryset_to_dataframe(qs))
      if not frames:
        return pd.DataFrame(columns=["host", "time"])
      out = pd.concat(frames, ignore_index=True)
      if out.empty or not {"host", "time"}.issubset(out.columns):
        return pd.DataFrame(columns=["host", "time"])
      return out.sort_values(["host", "time"]).reset_index(drop=True)

    key = make_cache_key(
        KEY_HOST_TIME_DF, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_aggregate_df(
    self,
    typ: Any,
    val_col: Any,
    events: Any,
    conv: float = 1.0,
  ) -> Any:
    """
    Aggregate val_col (e.g. 'arc' or 'value') for given type and events.
    
      Returns.
    
      DataFrame with columns host, time, sum_val (sum * conv). Result is
        cached
      per (jid, typ, val_col, events).
    
    Args:
      typ (Any): Typ passed to this helper.
      val_col (Any): Val col passed to this helper.
      events (Any): Events passed to this helper.
      conv (float): Floating-point value for conv.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> jid_table().get_aggregate_df(None, None, None, 0)  # doctest: +SKIP
    """
    _incr_summary_aggregate_count_if_active()
    probed_events = events_probe_names(events, typ=typ)
    events_key = ":".join(sorted(probed_events))
    # GPU snapshot gauges: exclude DCGM blank-family before sum-across-dev.
    _gpu_types = frozenset({"nvidia_gpu", "amd_gpu", "intel_gpu"})
    reject_dcgm_blank = typ in _gpu_types or any(
        t in _gpu_types for t in type_probe_names(typ)
    )

    def _fn_pandas_groupby() -> Any:
      """
      Aggregate via raw host_data rows when SQL SUM is unavailable.

      Uses host-chunk timeout split/retry (same contract as TypeDetail).

      Returns:
        Any: DataFrame with host, time, sum_val columns (possibly empty).

      Examples:
        >>> # Called from get_aggregate_df fallback after SQL failure
        >>> True
        True
      """
      hosts = [str(h) for h in self._base_filter.get("host__in") or []]
      import pandas as pd

      if not hosts:
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      tkw = self._host_data_time_filter_kwargs()
      host_batch = (
          TYPE_DETAIL_HOST_QUERY_BATCH
          if reject_dcgm_blank
          else JID_TABLE_HOST_QUERY_BATCH
      )
      frames = []
      for candidate_typ in type_probe_names(typ):
        def build_qs_raw(
          host_chunk: Any,
          ct: Any = candidate_typ,
        ) -> Any:
          """
          Build raw-row values queryset for one host sub-chunk.

          Args:
            host_chunk (Any): Hostnames for this ``host__in`` slice.
            ct (Any): Candidate ``host_data.type`` string.

          Returns:
            Any: Django values queryset (host, time, val_col).

          Examples:
            >>> # Wired into _fetch_host_data_values_frames
            >>> True
            True
          """
          qs = host_data.objects.filter(
              host__in=host_chunk,
              **tkw,
              type=ct,
              event__in=probed_events,
          )
          if reject_dcgm_blank and val_col in ("value", "delta", "arc"):
            qs = qs.filter(**{f"{val_col}__lt": DCGM_FP64_BLANK})
          return qs.values("host", "time", val_col)

        df_raw = _fetch_host_data_values_frames(
            hosts,
            build_qs_raw,
            batch_size=host_batch,
        )
        if (
            df_raw is None
            or df_raw.empty
            or "host" not in df_raw.columns
            or "time" not in df_raw.columns
            or val_col not in df_raw.columns
        ):
          continue
        df_grouped = (
            df_raw.groupby(["host", "time"], as_index=False)[val_col]
            .sum()
            .rename(columns={val_col: "sum_val"})
        )
        frames.append(df_grouped)
        break
      if not frames:
        return pd.DataFrame(columns=["host", "time", "sum_val"])
      df_all = pd.concat(frames, ignore_index=True)
      df_all = (
          df_all.groupby(["host", "time"], as_index=False)["sum_val"]
          .sum()
          .sort_values(["host", "time"])
      )
      df_all["sum_val"] = df_all["sum_val"] * conv
      return df_all

    def _fn() -> Any:
      """
      Prefer SQL SUM per sample; fall back to raw-row groupby on failure.

      Host chunks use timeout split/retry; GPU types use a smaller batch.

      Returns:
        Any: DataFrame with host, time, sum_val columns (possibly empty).

      Raises:
        Exception: Control-flow timeouts (SIGALRM) are re-raised.

      Examples:
        >>> # Invoked via cached_orm inside get_aggregate_df
        >>> True
        True
      """
      import pandas as pd

      hosts = [str(h) for h in self._base_filter.get("host__in") or []]
      if not hosts:
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      _ALLOWED = ("arc", "value", "delta")
      if val_col not in _ALLOWED:
        return _fn_pandas_groupby()

      host_batch = (
          TYPE_DETAIL_HOST_QUERY_BATCH
          if reject_dcgm_blank
          else JID_TABLE_HOST_QUERY_BATCH
      )

      try:
        tkw = self._host_data_time_filter_kwargs()
        frames = []
        for host_chunk in _iter_acct_host_batches(hosts, host_batch):
          chunk_frames = []
          for candidate_typ in type_probe_names(typ):
            def build_qs_sql(
              host_subchunk: Any,
              ct: Any = candidate_typ,
            ) -> Any:
              """
              Build SQL SUM-per-sample queryset for one host sub-chunk.

              Args:
                host_subchunk (Any): Hostnames for this ``host__in`` slice.
                ct (Any): Candidate ``host_data.type`` string.

              Returns:
                Any: Aggregated Django queryset (host, sample_time, sum_val).

              Examples:
                >>> # Wired into _queryset_to_dataframe_with_host_chunk_retry
                >>> True
                True
              """
              qs_base = host_data.objects.filter(
                  host__in=host_subchunk,
                  **tkw,
                  type=ct,
                  event__in=probed_events,
              )
              if reject_dcgm_blank:
                qs_base = qs_base.filter(**{f"{val_col}__lt": DCGM_FP64_BLANK})
              return host_data_sum_val_per_sample_queryset(qs_base, val_col)

            df_chunk = host_data_restore_time_column(
                _queryset_to_dataframe_with_host_chunk_retry(
                    host_chunk,
                    build_qs_sql,
                )
            )
            if not df_chunk.empty and "sum_val" in df_chunk.columns:
              chunk_frames.append(df_chunk)
              break
          if chunk_frames:
            frames.append(chunk_frames[0])
        if not frames:
          return pd.DataFrame(columns=["host", "time", "sum_val"])
        df_sql = pd.concat(frames, ignore_index=True)
        if df_sql.empty or "sum_val" not in df_sql.columns:
          return pd.DataFrame(columns=["host", "time", "sum_val"])
        df_sql = (
            df_sql.groupby(["host", "time"], as_index=False)["sum_val"]
            .sum()
            .sort_values(["host", "time"])
        )
        df_sql["sum_val"] = df_sql["sum_val"].astype("float64") * conv
        return df_sql
      except Exception as exc:
        if is_metrics_compute_control_flow_error(exc):
          raise
        _logger.warning(
            "get_aggregate_df SQL aggregate failed jid=%s typ=%s error=%s: %s; "
            "falling back to raw-row pandas groupby",
            self.jid,
            typ,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return _fn_pandas_groupby()

    key = make_cache_key_bounded(
        KEY_AGG_DF, self.jid, typ, val_col, events_key,
        self._large_job_plot_cache_token,
    )
    import pandas as pd

    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    if result is not None:
      return result
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_full_host_data_df(self, columns: Any | None = None) -> Any:
    """
    Full host_data for this job as DataFrame (host, time, type, event, value,.
    
      etc.). Cached when columns is None.
    
    Args:
      columns (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Open return polymorphism from ``get_full_host_data_df``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> jid_table().get_full_host_data_df(None)  # doctest: +SKIP
    """
    cols = columns or ["host", "time", "type", "event", "value", "arc", "delta"]

    # When specific columns are requested, return a fresh DataFrame without
    # caching to mirror previous behaviour (no cache on the raw-SQL path).
    # Use values_list defensively and filter/trim tuples to avoid Django's
    # "tuple index out of range" bug when model fields and DB schema diverge.
    if columns is not None:
      import pandas as pd

      rows = self._full_host_data_rows_batched(cols)
      return pd.DataFrame(rows, columns=cols)

    def _fn() -> Any:
      """
      Internal helper to handle function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> jid_table()._fn()  # doctest: +SKIP
      """
      import pandas as pd

      rows = self._full_host_data_rows_batched(cols)
      return pd.DataFrame(rows, columns=cols)

    key = make_cache_key(
        KEY_HOST_DATA_DF, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_llite_delta_by_event(self) -> Any:
    """
    Lustre vfs_read_bytes/vfs_write_bytes sum(delta) by event for this job.
    
      (cached).
    
    Dual-reads legacy ``read_bytes``/``write_bytes``; returned ``event`` values
      are
    canonicalized to ``vfs_*``.
    
    Returns:
      Any: Open return polymorphism from ``get_llite_delta_by_event``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> jid_table().get_llite_delta_by_event()  # doctest: +SKIP
    """
    byte_events = ["vfs_read_bytes", "vfs_write_bytes"]

    def _llite_fn() -> Any:
      """
      Internal helper to handle llite function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> jid_table()._llite_fn()  # doctest: +SKIP
      """
      for typ in type_probe_names("lustre_llite"):
        probed = events_probe_names(byte_events, typ=typ)
        qs = (self._host_data_qs(
            type=typ,
            event__in=probed,
        ).values("event").annotate(delta_sum=Sum("delta")).order_by("event"))
        df = queryset_to_dataframe(qs)
        if not df.empty:
          if "event" in df.columns:
            df = df.copy()
            df["event"] = df["event"].map(
                lambda e: canonical_event_name_for_type(typ, str(e)))
            df = (
                df.groupby("event", as_index=False)["delta_sum"]
                .sum()
                .sort_values("event")
            )
          return df
      return queryset_to_dataframe(None)

    key = make_cache_key(
        KEY_LLITE_DELTA, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _llite_fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_nfs_delta_totals_mb(self) -> Any:
    """
    Aggregate NFS client byte counters (monitor type `nfs`) to total read/write.
    
      MB.
    
    Uses the same counters as the monitor's `nfs.c` BYTE_KEYS:
      normal/direct/server
    read and write. Intended for the job detail File System when Lustre `llite`
    stats are not available.
    
    Returns:
      Any: Open return polymorphism from ``get_nfs_delta_totals_mb``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> jid_table().get_nfs_delta_totals_mb()  # doctest: +SKIP
    """
    nfs_read_events = ("normal_read", "direct_read", "server_read")
    nfs_write_events = ("normal_write", "direct_write", "server_write")
    all_events = list(nfs_read_events) + list(nfs_write_events)

    def _nfs_fn() -> Any:
      """
      Internal helper to handle nfs function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> jid_table()._nfs_fn()  # doctest: +SKIP
      """
      df = queryset_to_dataframe(None)
      for typ in type_probe_names("nfs"):
        qs = (
            self._host_data_qs(
                type=typ,
                event__in=all_events,
            ).values("event").annotate(delta_sum=Sum("delta")).order_by("event"))
        df = queryset_to_dataframe(qs)
        if not df.empty:
          break
      if df.empty or "delta_sum" not in df.columns:
        return None
      read_total = 0.0
      write_total = 0.0
      for _, row in df.iterrows():
        ev = row.get("event")
        ds = float(row.get("delta_sum") or 0)
        if ev in nfs_read_events:
          read_total += ds
        elif ev in nfs_write_events:
          write_total += ds
      if read_total == 0.0 and write_total == 0.0:
        return None
      return [read_total / (1024 * 1024), write_total / (1024 * 1024)]

    key = make_cache_key(
        KEY_NFS_FSIO, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _nfs_fn)
    return result

  def close(self) -> None:
    """
    No-op; provided for context-manager symmetry.
    
    Returns:
      None
    
    Examples:
      >>> jid_table().close()  # doctest: +SKIP
    """
    pass

  def __enter__(self) -> Any:
    """
    Context manager entry; return self.
    
    Returns:
      Any: Open return polymorphism from ``__enter__``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __enter__()  # doctest: +SKIP
    """
    return self

  def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> Any:
    """
    Context manager exit; call close().
    
    Args:
      _exc_type (Any):  exc type passed to this helper.
      _exc_val (Any):  exc val passed to this helper.
      _exc_tb (Any):  exc tb passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> __exit__(None, None, None)  # doctest: +SKIP
    """
    self.close()
    return False

  def __del__(self) -> None:
    """
    Destructor; call close() if possible. Prefer using 'with jid_table(...)'.
    
    for guaranteed cleanup; __del__ is not guaranteed to run (e.g. at
      interpreter
    shutdown or with circular refs).
    
    Returns:
      None
    
    Examples:
      >>> __del__()  # doctest: +SKIP
    """
    try:
      self.close()
    except Exception:
      pass


class TypeDetailDataProvider:
  """
  ORM-based provider for type-detail view: host_data scoped by job start/end.
  
  and.
  
    accounting host_list (no host_data.jid).
  
  Attributes:
    end_time: ``end_time``.
    host_list: ``host_list``.
    jid: ``jid``.
    start_time: ``start_time``.
    type_name: ``type_name``.
  """

  def __init__(
    self,
    jid: Any,
    type_name: Any,
    start_time: Any,
    end_time: Any,
    host_list: Any,
  ) -> None:
    """
    Build base filter for type_name, time range, and optional host_list.
    
      ``jid``.
    
      is only for cache keys / API identity.
    
    Args:
      jid (Any): Jid passed to this helper.
      type_name (Any): Type name passed to this helper.
      start_time (Any): Start time passed to this helper.
      end_time (Any): End time passed to this helper.
      host_list (Any): Host list passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> TypeDetailDataProvider(None, None, None, None, None)  # doctest: +SKIP
    """
    self.jid = jid
    self.type_name = type_name
    self.start_time = start_time
    self.end_time = end_time
    self.host_list = list(host_list) if host_list else []

  def _qs(self, **extra: Any) -> Any:
    """
    Base host_data queryset for this provider (type, time range, optional.
    
      host_list).
    
    Args:
      **extra (Any): Extra keyword arguments (``extra``); keys are ``str`` and
      value types match the wrapped protocol for this helper.
    
    Returns:
      Any: Open return polymorphism from ``_qs``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> TypeDetailDataProvider()._qs()  # doctest: +SKIP
    """
    flt = {
        "type": self.type_name,
        "time__gte": self.start_time,
        "time__lte": self.end_time,
    }
    if self.host_list:
      flt["host__in"] = self.host_list
    return host_data.objects.filter(**flt, **extra)

  def get_host_time_df(self) -> Any:
    """
    DataFrame of (host, time) distinct, ordered by host, time (cached).
    
    Returns:
      Any: Open return polymorphism from ``get_host_time_df``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> TypeDetailDataProvider().get_host_time_df()  # doctest: +SKIP
    """
    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    key = make_cache_key(
        KEY_TYPE_DETAIL_HOST_TIME, self.jid, self.type_name, _st, _et
    )

    def _fn() -> Any:
      """
      Internal helper to handle function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> TypeDetailDataProvider()._fn()  # doctest: +SKIP
      """
      import pandas as pd

      if (
          not self.host_list
          or self.start_time is None
          or self.end_time is None
      ):
        qs = (
            self._qs()
            .values("host", "time")
            .distinct()
            .order_by("host", "time")
        )
        return queryset_to_dataframe(qs)

      type_name = self.type_name
      start_time = self.start_time
      end_time = self.end_time

      def build_qs(host_chunk: Any) -> Any:
        """
        Build the queryset.
        
        Args:
          host_chunk (Any): Host chunk passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> TypeDetailDataProvider().build_qs(None)  # doctest: +SKIP
        """
        return (
            host_data.objects.filter(
                type=type_name,
                time__gte=start_time,
                time__lte=end_time,
                host__in=host_chunk,
            )
            .values("host", "time")
            .distinct()
            .order_by("host", "time")
        )

      out = _fetch_host_data_values_frames(
          self.host_list,
          build_qs,
          batch_size=TYPE_DETAIL_HOST_QUERY_BATCH,
      )
      if out.empty or not {"host", "time"}.issubset(out.columns):
        return pd.DataFrame(columns=["host", "time"])
      return out.sort_values(["host", "time"]).reset_index(drop=True)

    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_events_units(self) -> Any:
    """
    List of (event, unit) for one host.
    
    Returns:
      Any: Open return polymorphism from ``get_events_units``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> TypeDetailDataProvider().get_events_units()  # doctest: +SKIP
    """
    if not self.host_list:
      return []
    qs = (self._qs(host=self.host_list[0]).values("event", "unit").distinct())
    df = queryset_to_dataframe(qs)
    if df.empty:
      return []
    return list(df[["event", "unit"]].itertuples(index=False, name=None))

  def get_type_list(self) -> Any:
    """
    Return sorted list of distinct type names for the first host.
    
    Returns:
      Any: Open return polymorphism from ``get_type_list``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> TypeDetailDataProvider().get_type_list()  # doctest: +SKIP
    """
    if not self.host_list:
      return []
    qs = self._qs(host=self.host_list[0]).values_list("type",
                                                      flat=True).distinct()
    return sorted(set(qs))

  def get_aggregate_df(self, event: Any, metric: str = "arc") -> Any:
    """
    Aggregate metric (e.g. arc) by host and time for the given event; returns.
    
      DataFrame with sum_val (cached).
    
    Args:
      event (Any): Event passed to this helper.
      metric (str): String for metric.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> TypeDetailDataProvider().get_aggregate_df(None, "x")  # doctest: +SKIP
    """
    _ALLOWED_METRICS = ("arc", "value", "delta")
    if metric not in _ALLOWED_METRICS:
      metric = "arc"
    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    key = make_cache_key(
        KEY_TYPE_DETAIL_AGG, self.jid, self.type_name, event, metric, _st, _et
    )
    import pandas as pd

    def _fn() -> Any:
      """
      Internal helper to handle function.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Raises:
        Exception: Raised when ``_fn`` hits a ``Exception`` failure path.
      
      Examples:
        >>> TypeDetailDataProvider()._fn()  # doctest: +SKIP
      """
      if (
          not self.host_list
          or self.start_time is None
          or self.end_time is None
      ):
        qs = self._qs(event=event).values("host", "time", metric)
        return _type_detail_group_metric_to_sum_val(queryset_to_dataframe(qs), metric)

      type_name = self.type_name
      start_time = self.start_time
      end_time = self.end_time

      try:
        sql_frames = []
        for host_chunk in _iter_acct_host_batches(
            self.host_list,
            TYPE_DETAIL_HOST_QUERY_BATCH,
        ):
          def build_qs_sql(
            host_subchunk: Any,
            ev: Any = event,
            met: Any = metric,
          ) -> Any:
            """
            Build the queryset sql.
            
            Args:
              host_subchunk (Any): Host subchunk passed to this helper.
              ev (Any): Ev passed to this helper.
              met (Any): Met passed to this helper.
            
            Returns:
              Any: Value produced by this call (type depends on inputs).
            
            Examples:
              >>> TypeDetailDataProvider().build_qs_sql(None, None, None)
            """
            return host_data_sum_val_per_sample_queryset(
                host_data.objects.filter(
                    type=type_name,
                    time__gte=start_time,
                    time__lte=end_time,
                    host__in=host_subchunk,
                    event=ev,
                ),
                met,
            )

          sql_frames.append(
              _queryset_to_dataframe_with_host_chunk_retry(
                  host_chunk,
                  build_qs_sql,
              )
          )
        return _type_detail_concat_sum_val_frames(sql_frames)
      except Exception as exc:
        if is_metrics_compute_control_flow_error(exc):
          raise
        logging.getLogger(__name__).warning(
            "TypeDetailDataProvider SQL aggregate failed jid=%s type=%s event=%s "
            "error=%s: %s; falling back to raw-row pandas groupby",
            self.jid,
            type_name,
            event,
            type(exc).__name__,
            exc,
            exc_info=True,
        )

      def build_qs_raw(
        host_chunk: Any,
        ev: Any = event,
        met: Any = metric,
      ) -> Any:
        """
        Build the queryset raw.
        
        Args:
          host_chunk (Any): Host chunk passed to this helper.
          ev (Any): Ev passed to this helper.
          met (Any): Met passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> TypeDetailDataProvider().build_qs_raw(None, None, None)
        """
        return (
            host_data.objects.filter(
                type=type_name,
                time__gte=start_time,
                time__lte=end_time,
                host__in=host_chunk,
                event=ev,
            )
            .values("host", "time", met)
        )

      df_raw = _fetch_host_data_values_frames(
          self.host_list,
          build_qs_raw,
          batch_size=TYPE_DETAIL_HOST_QUERY_BATCH,
      )
      return _type_detail_group_metric_to_sum_val(df_raw, metric)

    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    if result is not None:
      return result
    return pd.DataFrame()


class HostDataProvider:
  """
  ORM-based provider for host-scoped host_data (one host, time range). Same.
  
    interface as jid_table for SummaryPlot: jid, host_list, get_host_time_df,
    get_aggregate_df.
  
  Attributes:
    _base_filter: ``_base_filter``.
    host_list: ``host_list``.
    jid: ``jid``.
    schema: ``schema``.
  """

  def __init__(self, host_fqdn: Any, start_time: Any, end_time: Any) -> None:
    """
    Build base filter and schema for one host and time range. Schema is cached.
    
    Args:
      host_fqdn (Any): Host fqdn passed to this helper.
      start_time (Any): Start time passed to this helper.
      end_time (Any): End time passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> HostDataProvider(None, None, None)  # doctest: +SKIP
    """
    self.jid = host_fqdn.split(".")[0].replace("-", "_")
    self.host_list = [host_fqdn]
    self._base_filter = {
        "host": host_fqdn,
        "time__gte": start_time,
        "time__lte": end_time,
    }
    # Schema: distinct (type, event) for this host (cached)
    _st = start_time.isoformat() if start_time else ""
    _et = end_time.isoformat() if end_time else ""
    cache_key = make_cache_key(KEY_HOST_SCHEMA, host_fqdn, _st, _et)

    def _schema_fn() -> Any:
      """
      Return schema dict {type: [events...]} for this host/time range using ORM.
      
        only.
      
      Returns:
        Any: Open return polymorphism from ``_schema_fn``: concrete type
        depends on inputs and branch (mapping, scalar, handle, or
        ``None``-like empty).
      
      Examples:
        >>> HostDataProvider()._schema_fn()  # doctest: +SKIP
      """
      import pandas as pd

      raw_rows = list(
          host_data.objects.filter(
              host=str(self._base_filter["host"]),
              time__gte=self._base_filter["time__gte"],
              time__lte=self._base_filter["time__lte"],
          )
          .values_list("type", "event")
          .distinct()
      )
      # Defensive: keep only rows with at least two elements and trim to first
      # two columns in case backend adds extras or returns shorter tuples.
      rows = [tuple(r[:2]) for r in raw_rows if len(r) >= 2]
      if not rows:
        return {}
      schema_df = pd.DataFrame(rows, columns=["type", "event"])
      if schema_df.empty:
        return {}
      types = sorted(schema_df["type"].unique().tolist())
      schema = {}
      for t in types:
        schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
      return schema

    self.schema = cached_orm(cache_key, get_site_content_cache_timeout(), _schema_fn) or {}

  def _host_data_qs(self, **extra_filters: Any) -> Any:
    """
    Base host_data queryset for this host (time range).
    
    Args:
      **extra_filters (Any): Extra keyword arguments (``extra_filters``); keys
      are ``str`` and value types match the wrapped protocol for this helper.
    
    Returns:
      Any: Open return polymorphism from ``_host_data_qs``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> HostDataProvider()._host_data_qs()  # doctest: +SKIP
    """
    return host_data.objects.filter(**self._base_filter, **extra_filters)

  def get_host_time_df(self) -> Any:
    """
    DataFrame of (host, time) distinct, ordered by host, time.
    
    Returns:
      Any: Open return polymorphism from ``get_host_time_df``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> HostDataProvider().get_host_time_df()  # doctest: +SKIP
    """
    qs = (self._host_data_qs().values("host", "time").distinct().order_by(
        "host", "time"))
    return queryset_to_dataframe(qs)

  def get_aggregate_df(
    self,
    typ: Any,
    val_col: Any,
    events: Any,
    conv: float = 1.0,
  ) -> Any:
    """
    Aggregate val_col for type and events; returns DataFrame with host, time,.
    
      sum_val (sum * conv).
    
    Args:
      typ (Any): Typ passed to this helper.
      val_col (Any): Val col passed to this helper.
      events (Any): Events passed to this helper.
      conv (float): Floating-point value for conv.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> HostDataProvider().get_aggregate_df(None, None, None, 0)
    """
    _incr_summary_aggregate_count_if_active()
    _ALLOWED_METRICS = ("arc", "value", "delta")
    if val_col not in _ALLOWED_METRICS:
      val_col = "arc"
    probed_events = events_probe_names(events, typ=typ)
    df = None
    for candidate_typ in type_probe_names(typ):
      # Single-host provider: keep NULL (missing) for all-NULL sample groups,
      # unlike the job-scoped aggregates that mirror pandas ``sum() == 0.0``.
      qs = host_data_sum_val_per_sample_queryset(
          self._host_data_qs(
              type=candidate_typ,
              event__in=probed_events,
          ),
          val_col,
          coalesce_zero=False,
      )
      candidate = host_data_restore_time_column(queryset_to_dataframe(qs))
      if not candidate.empty and "sum_val" in candidate.columns:
        df = candidate
        break
    if df is None:
      import pandas as pd
      df = pd.DataFrame(columns=["host", "time", "sum_val"])
    if not df.empty and "sum_val" in df.columns:
      df["sum_val"] = df["sum_val"] * conv
    return df
