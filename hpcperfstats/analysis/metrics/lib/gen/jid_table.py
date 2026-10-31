"""
Job-scoped host_data access via Django ORM. Provides jid_table,.

TypeDetailDataProvider, and HostDataProvider for querying job/host metrics
without raw SQL. Uses Redis caching for heavy queries.

Attributes:
  HOST_DATA_SUM_VAL_ALIAS: ``HOST_DATA_SUM_VAL_ALIAS``.
  HOST_DATA_TIME_ALIAS: ``HOST_DATA_TIME_ALIAS``.
  JID_TABLE_HOST_QUERY_BATCH: ``JID_TABLE_HOST_QUERY_BATCH``.
  TYPE_DETAIL_HOST_QUERY_BATCH: ``TYPE_DETAIL_HOST_QUERY_BATCH``.
  _DENSE_PLOT_AGG_TYPES: NFS/llite type names that use the dense (8) host batch.
  _GPU_AFF_UNSET: Sentinel for unloaded ``job_affinity`` GPU cache.
  _STATEMENT_TIMEOUT_ERROR_MARKERS: ``_STATEMENT_TIMEOUT_ERROR_MARKERS``.
  _logger: ``_logger``.
  _summary_agg_count: ``_summary_agg_count``.
  _summary_agg_count_lock: ``_summary_agg_count_lock``.
  _summary_agg_counting: ``_summary_agg_counting``.
  local_timezone: ``local_timezone``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.db import (
  InterfaceError,
  OperationalError,
  close_old_connections,
  connections,
)

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  apply_job_host_data_scope,
  gpu_devs_for_job,
  is_job_scoped_host_data_type,
  job_is_shared_node,
)
from hpcperfstats.analysis.metrics.lib.phase_profile import (
  phase as _profile_phase,
)
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.site.lib.machine.cache_utils import (
  KEY_JID_HOST_WINDOW_ROW_COUNT,
  KEY_JOB_HOST_LIST,
  KEY_JOB_JID_TABLE_WINDOW,
  KEY_JOB_SCHEMA,
  cached_orm,
  get_site_content_cache_timeout,
  make_cache_key,
)
from hpcperfstats.site.lib.machine.models import host_data, job_data

# Sentinel: affinity GPU list not loaded yet (distinct from None = unrestricted).
_GPU_AFF_UNSET: object = object()

# Chunk host__in on host_data for large jobs; single queries with thousands of
# hosts often hit PostgreSQL statement_timeout during metrics and plots.
# Must stay equal to ``metrics.METRICS_HOST_QUERY_BATCH`` (drift-tested).
# Prior value 64 let ~48-host NFS Summary SUM(arc) fit in one first attempt
# and burn the full 120s statement_timeout before any bisect (hs04).
JID_TABLE_HOST_QUERY_BATCH = 16


_STATEMENT_TIMEOUT_ERROR_MARKERS = (
  "statement timeout",
  "canceling statement due to statement timeout",
  "querycanceled",
)


def is_metrics_compute_control_flow_error(exc: Any) -> Any:
  """
  True for timeout control flow that must never be swallowed by a fallback.

  Thread workers rely on database statement timeouts and parent no-progress
  detection. A timeout escaping a bounded query must propagate so an
  ``except Exception`` fallback cannot start a slower unbounded path.

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
  except TypeError, ValueError, OverflowError:
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
    yield hosts[i : i + bs]


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


def _estimate_one_min_samples_for_window(start_time: Any, end_time: Any) -> int:
  """
  Estimate 1-minute sample count for a job window (design cadence).

  Args:
    start_time (Any): Job window start datetime.
    end_time (Any): Job window end datetime.

  Returns:
    int: At least 1 estimated sample.

  Examples:
    >>> from datetime import datetime, timezone
    >>> a = datetime(2024, 1, 1, tzinfo=timezone.utc)
    >>> b = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
    >>> _estimate_one_min_samples_for_window(a, b)
    60
  """
  try:
    span_s = float((end_time - start_time).total_seconds())
  except Exception:
    return 1
  if not (span_s > 0):
    return 1
  return max(1, int((span_s + 59.0) // 60.0))


def _resolve_plot_aggregate_time_bucket_count(n_hosts: int) -> int:
  """
  Choose large-job time bucket count so hostsxtimes stays under the row budget.

  Design capacity ``5000x48x60`` uses ``floor(budget / n_hosts)`` (e.g. 200 at
  5000 hosts with a 1M-row budget), never more than configured buckets.

  Args:
    n_hosts (int): Number of hosts on the job.

  Returns:
    int: Bucket count (minimum 32).

  Examples:
    >>> _resolve_plot_aggregate_time_bucket_count(5000)  # doctest: +SKIP
  """
  configured = int(cfg.get_large_job_time_buckets())
  budget = int(cfg.get_plot_aggregate_max_host_time_points())
  hosts = max(1, int(n_hosts or 1))
  by_budget = max(32, budget // hosts)
  return max(32, min(configured, by_budget))


def _iter_aggregate_time_filter_chunks(
  tkw: Any,
  slice_s: int,
) -> Iterator[Any]:
  """
  Yield time-filter dicts that partition ``tkw`` into wall-clock slices.

  Args:
    tkw (Any): Base time kwargs (``time__in`` or ``time__gte``/``time__lte``).
    slice_s (int): Target wall-clock seconds per chunk (minimum 60).

  Yields:
    Any: Dict suitable for ``host_data.objects.filter(**chunk)``.

  Examples:
    >>> list(_iter_aggregate_time_filter_chunks({"time__in": []}, 3600))
    []
  """
  slice_s = max(60, int(slice_s or 3600))
  if not isinstance(tkw, dict) or not tkw:
    return
  if "time__in" in tkw:
    times = list(tkw.get("time__in") or [])
    if not times:
      return
    chunk_n = max(1, int(slice_s // 60))
    for i in range(0, len(times), chunk_n):
      yield {"time__in": times[i : i + chunk_n]}
    return
  gte = tkw.get("time__gte")
  lte = tkw.get("time__lte")
  if gte is None or lte is None:
    yield dict(tkw)
    return
  try:
    cur = gte
    while cur < lte:
      nxt = cur + timedelta(seconds=slice_s)
      if nxt >= lte:
        yield {"time__gte": cur, "time__lte": lte}
        break
      yield {"time__gte": cur, "time__lt": nxt}
      cur = nxt
  except TypeError:
    # Non-datetime bounds (unit tests / odd callers): one chunk, no wall split.
    yield dict(tkw)


def _split_time_filter_for_timeout(time_filter: Any) -> Any:
  """
  Bisect a time filter for statement_timeout retry (host size already 1).

  Args:
    time_filter (Any): ``time__in`` list or ``time__gte``/``time__lte`` window.

  Returns:
    Any: ``(left, right)`` filter dicts, or ``None`` when too small to split.

  Examples:
    >>> _split_time_filter_for_timeout({"time__in": [1]}) is None
    True
  """
  if not isinstance(time_filter, dict):
    return None
  if "time__in" in time_filter:
    times = list(time_filter.get("time__in") or [])
    if len(times) < 2:
      return None
    mid = max(1, len(times) // 2)
    return (
      {"time__in": times[:mid]},
      {"time__in": times[mid:]},
    )
  gte = time_filter.get("time__gte")
  lte = time_filter.get("time__lte")
  if lte is None:
    lte = time_filter.get("time__lt")
  if gte is None or lte is None:
    return None
  try:
    span_s = float((lte - gte).total_seconds())
  except Exception:
    return None
  if span_s < 120.0:
    return None
  mid = gte + timedelta(seconds=span_s / 2.0)
  return (
    {"time__gte": gte, "time__lte": mid},
    {"time__gt": mid, "time__lte": lte},
  )


def _iter_host_time_query_chunks(
  hosts: Any,
  time_filter: Any,
  *,
  batch_size: Any | None = None,
  slice_s: Any | None = None,
) -> Iterator[Any]:
  """
  Yield ``(host_chunk, time_kw)`` over host batches x wall-clock time slices.

  Args:
    hosts (Any): Accounting host FQDNs for ``host__in``.
    time_filter (Any): Base time kwargs (``time__in`` or gte/lte window).
    batch_size (Any | None): Hosts per chunk (default ``JID_TABLE_HOST_QUERY_BATCH``).
    slice_s (Any | None): Seconds per time slice; default from INI
      ``metrics_plot_aggregate_time_slice_s``.

  Yields:
    Iterator[Any]: ``(host_chunk, time_kw)`` pairs.

  Examples:
    >>> list(_iter_host_time_query_chunks([], {}))
    []
  """
  host_list = _listify_acct_hosts(hosts)
  if not host_list:
    return
  if slice_s is None:
    slice_s = int(cfg.get_metrics_plot_aggregate_time_slice_s())
  tkw = dict(time_filter) if isinstance(time_filter, dict) else {}
  time_chunks = list(_iter_aggregate_time_filter_chunks(tkw, int(slice_s)))
  if not time_chunks:
    time_chunks = [tkw] if tkw else [{}]
  for tf in time_chunks:
    for host_chunk in _iter_acct_host_batches(host_list, batch_size):
      yield host_chunk, tf


def _run_with_host_time_timeout_retry(
  hosts: Any,
  time_filter: Any,
  run: Any,
  merge: Any,
  *,
  min_hosts: int = 1,
  max_attempts: int = 2,
  empty: Any = None,
) -> Any:
  """
  Run ``run(hosts, time_filter)``; on statement timeout bisect hosts then time.

  Args:
    hosts (Any): Hostnames for this attempt.
    time_filter (Any): Time kwargs or ``None`` (legacy callers).
    run (Any): Callable ``(hosts_list, time_filter) -> T``.
    merge (Any): Callable ``(left, right) -> T`` after a split.
    min_hosts (int): Stop host bisect at this size.
    max_attempts (int): Retries when a minimal chunk still times out.
    empty (Any): Value when ``hosts`` is empty (default ``[]``).

  Returns:
    Any: Value from ``run`` or merged host/time splits.

  Raises:
    Exception: Non-timeout errors, or timeout after splits are exhausted.
    last_exc: Re-raised when a statement_timeout remains after host/time
      splits and ``max_attempts`` are exhausted.

  Examples:
    >>> _run_with_host_time_timeout_retry(
    ...   [], None, lambda h, t: [], lambda a, b: a + b
    ... )
    []
  """
  host_list = [str(h) for h in (hosts or []) if h]
  if not host_list:
    return [] if empty is None else empty
  last_exc = None
  tf = time_filter
  for attempt in range(max_attempts):
    try:
      close_old_connections()
      with _profile_phase("db_fetch"):
        return run(host_list, tf)
    except OperationalError as exc:
      last_exc = exc
      if not _is_statement_timeout_error(exc):
        raise
      if len(host_list) > min_hosts:
        mid = max(1, len(host_list) // 2)
        left = _run_with_host_time_timeout_retry(
          host_list[:mid],
          tf,
          run,
          merge,
          min_hosts=min_hosts,
          max_attempts=max_attempts,
          empty=empty,
        )
        right = _run_with_host_time_timeout_retry(
          host_list[mid:],
          tf,
          run,
          merge,
          min_hosts=min_hosts,
          max_attempts=max_attempts,
          empty=empty,
        )
        return merge(left, right)
      split = _split_time_filter_for_timeout(tf) if tf is not None else None
      if split is not None:
        left_tf, right_tf = split
        left = _run_with_host_time_timeout_retry(
          host_list,
          left_tf,
          run,
          merge,
          min_hosts=min_hosts,
          max_attempts=max_attempts,
          empty=empty,
        )
        right = _run_with_host_time_timeout_retry(
          host_list,
          right_tf,
          run,
          merge,
          min_hosts=min_hosts,
          max_attempts=max_attempts,
          empty=empty,
        )
        return merge(left, right)
      if attempt + 1 >= max_attempts:
        raise
      close_old_connections()
  if last_exc is not None:
    raise last_exc
  return [] if empty is None else empty


def _merge_list_results(left: Any, right: Any) -> Any:
  """
  Concatenate two list-like query results (timeout-split merge).

  Args:
    left (Any): First chunk (list-like or empty).
    right (Any): Second chunk (list-like or empty).

  Returns:
    Any: Concatenated list of rows from ``left`` then ``right``.

  Examples:
    >>> _merge_list_results([1], [2])
    [1, 2]
  """
  return list(left or []) + list(right or [])


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
          h
          for h in (_normalize_host_cell_for_host_data(h) for h in host_chunk)
          if h
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
      f"[jid_table] window row count count-fallback jid={jid}: {exc}",
      flush=True,
    )
    return 0
  coerced_n = _coerce_nonnegative_window_row_count(n)
  if coerced_n is None:
    log_print(
      f"[jid_table] window row count not coercible jid={jid} raw={n!r}",
      flush=True,
    )
    return 0
  if ttl > 0 and jid and iso_pair is not None:
    with contextlib.suppress(Exception):
      cache.set(key, coerced_n, timeout=ttl)
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
  except TypeError, ValueError, OverflowError:
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
    k = 0 if step_sec <= 0 else int((ts - start).total_seconds() // step_sec)
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
    >>> _strided_distinct_times_date_bin_via_grouped_max_sql(
    ...   0
    ... )  # doctest: +SKIP
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
  except TypeError, ValueError, OverflowError:
    nb = 2
  step_sec = max(span_sec / float(nb - 1), 1e-9)

  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    strided_bucket_max_times,
  )

  via_fn = strided_bucket_max_times(hosts, start_dt, end_dt, nb)
  if via_fn:
    return via_fn

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
            for h in (_normalize_host_cell_for_host_data(h) for h in host_chunk)
            if h
          ]
          if not clean_chunk:
            continue
          placeholders = ", ".join(["%s"] * len(clean_chunk))
          sql = (
            "SELECT (FLOOR(EXTRACT(EPOCH FROM ("
            + tbl
            + "."
            + tcol
            + " - %s)) / %s))::bigint AS grp, "
            "MAX(" + tbl + "." + tcol + ") AS mx FROM " + tbl + " "
            "WHERE "
            + tbl
            + "."
            + tcol
            + " >= %s AND "
            + tbl
            + "."
            + tcol
            + " <= %s AND "
            + tbl
            + "."
            + hcol
            + " IN ("
            + placeholders
            + ") GROUP BY 1"
          )
          params = [
            start_dt,
            step_sec,
            start_dt,
            end_dt,
            *clean_chunk,
          ]
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
        start, end, acct_hosts, n_buckets
      )
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
        start, end, acct_hosts, n_buckets
      )
      if out:
        return out
    except Exception:
      logging.getLogger(__name__).warning(
        "jid_table date_bin strided times failed; falling back to NTILE",
        exc_info=True,
      )
  try:
    return _strided_distinct_times_postgresql(start, end, acct_hosts, n_buckets)
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

    dt = django_tz.make_aware(dt, UTC)
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
    fqdn
    for fqdn in (
      _as_host_data_fqdn(h)
      for h in _normalize_job_accounting_host_list(raw_host_list)
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
  acct_host_list = list(_build_acct_host_fqdns(hl_raw))
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


class jid_table:
  """
  Job-scoped view of job_data and host_data using Django ORM. No raw connection.

  Attributes:
    units: ``(type, event) -> unit`` labels of the schema host.
    _base_filter: ``_base_filter``.
    _gpu_affinity_devs_cache: Cached ``gpu_devs_for_job`` result or unset.
    _large_job_plot_cache_token: ``_large_job_plot_cache_token``.
    _shared_node: Affinity-based shared-node signal for residual host-wide reads.
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
    self._gpu_affinity_devs_cache: list[str] | object | None = _GPU_AFF_UNSET
    self._large_job_plot_cache_token = "full"
    self._shared_node = False

    try:
      row = cached_orm(
        make_cache_key(KEY_JOB_JID_TABLE_WINDOW, jid),
        get_site_content_cache_timeout(),
        lambda: (
          job_data.objects.filter(jid=jid)
          .values_list("host_list", "start_time", "end_time")
          .first()
        ),
      )
    except Exception:
      row = None

    if row is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.units = {}
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
      self.units = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    # job_data host_list: use fqdn for host_data lookups (cast to str for varchar comparison)
    self.acct_host_list = _build_acct_host_fqdns(hl_raw)
    self.start_time = _ensure_tz(st)
    self.end_time = _ensure_tz(et)
    self._shared_node = job_is_shared_node(str(jid))
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
          apply_job_host_data_scope(
            host_data.objects.filter(
              time__gte=self._base_filter["time__gte"],
              time__lte=self._base_filter["time__lte"],
              host__in=host_chunk,
            ),
            self.jid,
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
    with _profile_phase("db_fetch"):
      self.host_list = (
        cached_orm(
          make_cache_key(KEY_JOB_HOST_LIST, jid, _st, _et),
          get_site_content_cache_timeout(),
          _host_list_fn,
        )
        or []
      )
    _logger.debug("jid_table host_list query time: %.1fs", time.time() - qtime)

    if len(self.host_list) == 0:
      self.schema = {}
      self.units = {}
      return

    # Schema: distinct (type, event) for one host (cached)
    etime = time.time()

    def _schema_fn() -> Any:
      """
      Distinct ``(type, event, unit)`` of one host as a plain list (no pandas).

      Shared-node jobs union the residual host-wide pairs so NUMA
      ``host_mem`` / fabric types appear on Device data.

      Returns:
        Any: ``{"rows": [[type, event, unit], ...]}``.

      Examples:
        >>> jid_table()._schema_fn()  # doctest: +SKIP
      """
      if not self.host_list:
        return {"rows": []}
      raw_rows = list(
        apply_job_host_data_scope(
          host_data.objects.filter(
            host=str(self.host_list[0]),
            time__gte=self._base_filter["time__gte"],
            time__lte=self._base_filter["time__lte"],
          ),
          self.jid,
        )
        .values_list("type", "event", "unit")
        .distinct()
      )
      rows = [tuple(r[:3]) for r in raw_rows if len(r) >= 3]
      if getattr(self, "_shared_node", False):
        host_wide_rows = list(
          host_data.objects.filter(
            host=str(self.host_list[0]),
            time__gte=self._base_filter["time__gte"],
            time__lte=self._base_filter["time__lte"],
          )
          .values_list("type", "event", "unit")
          .distinct()
        )
        seen = {(r[0], r[1]) for r in rows}
        for r in host_wide_rows:
          if len(r) < 3:
            continue
          typ = str(r[0]) if r[0] is not None else ""
          pair = (r[0], r[1])
          if typ and not is_job_scoped_host_data_type(typ) and pair not in seen:
            seen.add(pair)
            rows.append(tuple(r[:3]))
      out = []
      for typ, event, unit in rows:
        t = _normalize_host_data_schema_label(typ)
        e = _normalize_host_data_schema_label(event)
        if t is None:
          continue
        out.append([t, e, "" if unit is None else str(unit)])
      return {"rows": out}

    with _profile_phase("db_fetch"):
      schema_rows = cached_orm(
        make_cache_key(KEY_JOB_SCHEMA, jid, self.host_list[0], "v2"),
        get_site_content_cache_timeout(),
        _schema_fn,
      )
    rows = (
      (schema_rows or {}).get("rows") if isinstance(schema_rows, dict) else []
    )
    self.schema = {}
    self.units = {}
    for typ, event, unit in rows or []:
      if event is None:
        continue
      self.schema.setdefault(str(typ), set()).add(str(event))
      self.units.setdefault((str(typ), str(event)), str(unit or ""))
    self.schema = {t: sorted(evs) for t, evs in sorted(self.schema.items())}
    _logger.debug("jid_table schema time: %.1fs", time.time() - etime)
    with _profile_phase("db_fetch"):
      self._apply_large_job_time_sampling_if_needed()

  def _gpu_affinity_devs_cached(self) -> list[str] | None:
    """
    Return ``job_affinity`` GPU ``dev`` list for this jid (cached per instance).

    ``None`` means no affinity rows (unrestricted). An empty list means
    affinity exists but no GPUs are assigned.

    Returns:
      list[str] | None: Allowed hardware GPU device ids, or ``None``.

    Examples:
      >>> jid_table("1")._gpu_affinity_devs_cached()  # doctest: +SKIP
      None
    """
    if self._gpu_affinity_devs_cache is _GPU_AFF_UNSET:
      self._gpu_affinity_devs_cache = gpu_devs_for_job(str(self.jid or ""))
    return self._gpu_affinity_devs_cache  # type: ignore[return-value]

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
    Thin time axis when raw rows or hostsxsamples exceed the plot memory budget.

    Activates when ``COUNT(*)`` exceeds the large-job threshold **or** when
    ``n_hosts x estimated_1min_samples`` exceeds
    ``get_plot_aggregate_max_host_time_points()`` (design ``5000x48x60``).
    Bucket count is ``min(configured, floor(budget / n_hosts))``.

    Returns:
      None

    Examples:
      >>> jid_table()._apply_large_job_time_sampling_if_needed()  # doctest: +SKIP
    """
    self._large_job_plot_cache_token = "full"
    if not self.acct_host_list or not self.start_time or not self.end_time:
      return
    n_hosts = len(self.acct_host_list)
    budget = int(cfg.get_plot_aggregate_max_host_time_points())
    est_samples = _estimate_one_min_samples_for_window(
      self.start_time, self.end_time
    )
    need_sampling = (n_hosts * est_samples) > budget
    n = None
    threshold = cfg.get_large_job_host_data_row_threshold()
    try:
      n = _count_host_data_rows_for_window_cached(
        self.jid, self.start_time, self.end_time, self.acct_host_list
      )
      if n > threshold:
        need_sampling = True
    except Exception as exc:
      _logger.warning(
        "jid_table large-job row count failed jid=%s: %s",
        self.jid,
        exc,
      )
      if not need_sampling:
        return
    if not need_sampling:
      return
    n_buckets = _resolve_plot_aggregate_time_bucket_count(n_hosts)
    try:
      sampled = _strided_distinct_times_for_large_job(
        self.start_time, self.end_time, self.acct_host_list, n_buckets
      )
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
    self._large_job_plot_cache_token = f"lb{len(sampled)}"
    _logger.info(
      "jid_table large-job time sampling jid=%s hosts=%s budget=%s "
      "rows~%s bucket_times=%s est_host_samples=%s",
      self.jid,
      n_hosts,
      budget,
      n if n is not None else "n/a",
      len(sampled),
      n_hosts * est_samples,
    )

  def close(self) -> None:
    """
    No-op; provided for context-manager symmetry.

    Returns:
      None

    Examples:
      >>> jid_table().close()  # doctest: +SKIP
    """

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
    with contextlib.suppress(Exception):
      self.close()
