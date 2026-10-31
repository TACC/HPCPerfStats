"""
One ``COPY … (FORMAT BINARY)`` fetch per job into a JobFrame.

The fetch reuses the existing host x time chunking and statement-timeout
bisect (``jid_table._iter_host_time_query_chunks`` /
``_run_with_host_time_timeout_retry``) so a huge job still splits on
``statement_timeout`` exactly like the pandas path, and the chunk streams are
decoded by K0 (``k0_decode``) into one sorted frame.

Scope follows ``job_host_data_scope``: every row of a job-scoped type must
belong to the job (``jid`` match or ``dev = 'j:<jid>'``); on shared-node jobs
the residual (non-allowlisted) types are read host-wide and carry
``owned = 0`` for rows of other tenants, so kernels can apply the
hardware-SUM-else-``j:``-MAX rule the SQL aggregates use today.

Attributes:
  COPY_SELECT_COLUMNS: SQL select list in K0 stream order.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
  JobFrame,
  NameInterner,
  build_job_frame,
)
from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
  DecodedChunk,
  concat_chunks,
  decode_copy_binary,
  empty_chunk,
)

COPY_SELECT_COLUMNS = (
  "h.time, h.host, h.type, h.event, h.dev, h.value, h.arc, h.delta"
)


def _time_clause(time_filter: dict[str, Any] | None) -> tuple[str, list[Any]]:
  """
  SQL predicate and params for a ``jid_table`` time filter dict.

  Supports ``time__in`` (large-job strided axis) and ``time__gte`` with
  ``time__lte`` or ``time__lt`` (host x time chunks).

  Args:
    time_filter (dict[str, Any] | None): Django-style time kwargs.

  Returns:
    tuple[str, list[Any]]: ``(sql_fragment, params)``; ``("TRUE", [])`` for
    an empty filter.

  Examples:
    >>> _time_clause({"time__gte": 1, "time__lt": 2})
    ('h.time >= %s AND h.time < %s', [1, 2])
    >>> _time_clause({"time__in": [1, 2]})
    ('h.time = ANY(%s::timestamptz[])', [[1, 2]])
  """
  if not time_filter:
    return "TRUE", []
  if "time__in" in time_filter:
    return "h.time = ANY(%s::timestamptz[])", [list(time_filter["time__in"])]
  parts: list[str] = []
  params: list[Any] = []
  if time_filter.get("time__gte") is not None:
    parts.append("h.time >= %s")
    params.append(time_filter["time__gte"])
  if time_filter.get("time__gt") is not None:
    parts.append("h.time > %s")
    params.append(time_filter["time__gt"])
  if time_filter.get("time__lte") is not None:
    parts.append("h.time <= %s")
    params.append(time_filter["time__lte"])
  if time_filter.get("time__lt") is not None:
    parts.append("h.time < %s")
    params.append(time_filter["time__lt"])
  if not parts:
    return "TRUE", []
  return " AND ".join(parts), params


def build_copy_sql(
  hosts: Sequence[str],
  time_filter: dict[str, Any] | None,
  jid: str,
  *,
  shared: bool,
  job_scoped_types: Sequence[str] = (),
  job_scoped: bool = True,
) -> tuple[str, list[Any]]:
  """
  Build the ``COPY (SELECT …) TO STDOUT (FORMAT BINARY)`` statement.

  Non-shared jobs select only job-owned rows (8 columns). Shared-node jobs
  select owned rows of allowlisted types plus every row of residual types,
  with a ninth ``owned`` int column.

  Args:
    hosts (Sequence[str]): Host FQDNs for ``host = ANY``.
    time_filter (dict[str, Any] | None): Time kwargs for this chunk.
    jid (str): Job id (scope).
    shared (bool): ``job_is_shared_node(jid)``.
    job_scoped_types (Sequence[str]): Allowlist of types that stay
      job-scoped on shared nodes (``_job_scoped_host_data_types``).
    job_scoped (bool): False reads every row of the hosts in the window
      (single-host plots); ``shared`` is then ignored.

  Returns:
    tuple[str, list[Any]]: SQL and parameters.

  Examples:
    >>> sql, params = build_copy_sql(
    ...   ["h"], {"time__gte": 1, "time__lte": 2}, "9", shared=False
    ... )
    >>> "FORMAT BINARY" in sql and params[-2:] == ["9", "9"]
    True
  """
  time_sql, time_params = _time_clause(time_filter)
  owned_sql = "(h.jid = %s OR h.dev = ('j:' || %s))"
  # Parameters follow placeholder order: SELECT list first (shared ``owned``
  # column), then WHERE host / time / scope.
  if not job_scoped:
    select = COPY_SELECT_COLUMNS
    scope = "TRUE"
    params: list[Any] = [list(hosts), *time_params]
  elif shared:
    select = f"{COPY_SELECT_COLUMNS}, ({owned_sql})::int AS owned"
    scope = f"({owned_sql} OR NOT (h.type = ANY(%s::text[])))"
    params = [jid, jid, list(hosts), *time_params]
    params += [jid, jid, list(job_scoped_types)]
  else:
    select = COPY_SELECT_COLUMNS
    scope = owned_sql
    params = [list(hosts), *time_params, jid, jid]
  sql = (
    f"COPY (SELECT {select} FROM host_data h "
    f"WHERE h.host = ANY(%s::text[]) AND {time_sql} AND {scope}) "
    "TO STDOUT (FORMAT BINARY)"
  )
  return sql, params


def run_copy_to_bytes(sql: str, params: Sequence[Any]) -> bytes:
  """
  Execute a COPY TO STDOUT through Django's psycopg connection.

  Errors are wrapped into Django exceptions (``OperationalError`` for
  ``statement_timeout``) so the bisect helper can classify them.

  Args:
    sql (str): ``COPY (…) TO STDOUT (FORMAT BINARY)`` statement.
    params (Sequence[Any]): Query parameters.

  Returns:
    bytes: Complete binary COPY stream.

  Examples:
    >>> run_copy_to_bytes("COPY (SELECT 1) TO STDOUT (FORMAT BINARY)", [])
    ... # doctest: +SKIP
  """
  from django.db import connections

  conn = connections["default"]
  conn.ensure_connection()
  raw = conn.connection
  chunks: list[bytes] = []
  with (
    conn.wrap_database_errors,
    raw.cursor() as cur,
    cur.copy(sql, list(params)) as copy,
  ):
    for data in copy:
      chunks.append(bytes(data))
  return b"".join(chunks)


def fetch_job_frame(
  jt: Any,
  *,
  batch_size: int | None = None,
  slice_s: int | None = None,
  copy_runner: Callable[[str, Sequence[Any]], bytes] | None = None,
  job_scoped: bool = True,
) -> JobFrame:
  """
  Fetch a job's host_data once, in host x time chunks, into a JobFrame.

  Args:
    jt (Any): ``jid_table`` for the job (``jid``, ``acct_host_list``,
      ``_base_filter`` / ``_host_data_time_filter_kwargs``, ``_shared_node``).
    batch_size (int | None): Hosts per chunk (default
      ``METRICS_HOST_QUERY_BATCH``).
    slice_s (int | None): Seconds per time slice (default INI
      ``metrics_plot_aggregate_time_slice_s``).
    copy_runner (Callable | None): ``(sql, params) -> bytes`` override for
      tests; default ``run_copy_to_bytes``.
    job_scoped (bool): False fetches every row of the hosts (single-host
      plots without a job).

  Returns:
    JobFrame: Sorted frame (empty when the job has no window or hosts).

  Examples:
    >>> fetch_job_frame(jt)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    _job_scoped_host_data_types,
  )
  from hpcperfstats.analysis.metrics.lib.metrics import (
    METRICS_HOST_QUERY_BATCH,
  )

  runner = copy_runner or run_copy_to_bytes
  interner = NameInterner()
  jid = str(getattr(jt, "jid", "") or "")
  hosts = [str(h) for h in (getattr(jt, "acct_host_list", None) or []) if h]
  base = getattr(jt, "_base_filter", None) or {}
  if not hosts or not base:
    return _frame_from_chunks(jid, interner, [])
  tkw = jt._host_data_time_filter_kwargs()
  shared = bool(getattr(jt, "_shared_node", False)) and job_scoped
  scoped_types = sorted(_job_scoped_host_data_types()) if shared else ()
  if batch_size is None:
    batch_size = METRICS_HOST_QUERY_BATCH

  def run(hosts_list: Any, tf_cur: Any) -> DecodedChunk:
    """
    COPY + decode one host x time chunk.

    Args:
      hosts_list (Any): Hostnames for this attempt.
      tf_cur (Any): Time filter dict.

    Returns:
      DecodedChunk: Decoded rows for the chunk.

    Examples:
      >>> True
      True
    """
    sql, params = build_copy_sql(
      hosts_list,
      tf_cur,
      jid,
      shared=shared,
      job_scoped_types=scoped_types,
      job_scoped=job_scoped,
    )
    return decode_copy_binary(
      runner(sql, params), interner, has_owned_column=shared
    )

  def merge(left: DecodedChunk, right: DecodedChunk) -> DecodedChunk:
    """
    Concatenate two decoded chunks after a bisect.

    Args:
      left (DecodedChunk): Left half.
      right (DecodedChunk): Right half.

    Returns:
      DecodedChunk: Combined chunk.

    Examples:
      >>> True
      True
    """
    return concat_chunks([left, right])

  chunks: list[DecodedChunk] = []
  for host_chunk, tf in jt_mod._iter_host_time_query_chunks(
    hosts,
    tkw,
    batch_size=batch_size,
    slice_s=slice_s,
  ):
    chunks.append(
      jt_mod._run_with_host_time_timeout_retry(
        host_chunk,
        tf,
        run,
        merge,
        empty=empty_chunk(),
      )
    )
  return _frame_from_chunks(jid, interner, chunks)


def _frame_from_chunks(
  jid: str,
  interner: NameInterner,
  chunks: list[DecodedChunk],
) -> JobFrame:
  """
  Concatenate decoded chunks and build the sorted JobFrame.

  Args:
    jid (str): Job id.
    interner (NameInterner): Shared name tables.
    chunks (list[DecodedChunk]): Decoded chunks.

  Returns:
    JobFrame: Sorted frame.

  Examples:
    >>> len(_frame_from_chunks("1", NameInterner(), []))
    0
  """
  merged = concat_chunks(chunks)
  return build_job_frame(
    jid,
    interner,
    time_us=merged.time_us,
    host_id=merged.host_id,
    type_id=merged.type_id,
    event_id=merged.event_id,
    dev_id=merged.dev_id,
    owned=merged.owned,
    value=merged.value,
    arc=merged.arc,
    delta=merged.delta,
  )
