#!/usr/bin/env python3
"""
Live logical COPY of Timescale ``host_data`` chunks from PG15 to PG18.

Do **not** restore ``_timescaledb_catalog`` from the source. Target must already
have an empty Django-migrated ``host_data`` hypertable. Watermark filtering is
best-effort while writers stay on PG15; freeze + verify is required before
cutover (see ``docs/OPERATOR_PG18_MIGRATION.md``).

Uses **psycopg** (already in the ``web`` image) for catalog queries and binary
COPY streaming with an explicit, type-checked column list - no PostgreSQL client
binary is required on PATH. Per chunk: capture the source change signature
(``pg_stat_all_tables`` insert/update/delete counters of the chunk and its
compressed relation), skip when source and target row counts already match
(unless ``--force``), else decompress + delete the target range and COPY, then
recount the target and (``--verify``) the source. Copied target chunks older
than ``--compress-older-than-days`` are compressed right away so PG18 never
holds the full history uncompressed.

``--state-file`` records ``{rows, sig}`` per verified chunk. With
``--changed-only``, chunks whose signature and bounds still match the state are
not counted or copied at all - the freeze pass then touches only chunks written
since the previous pass. Up to ``--workers`` chunks run concurrently; a failed
chunk is logged and the run continues, exiting non-zero at the end.

Attributes:
  LOG: Module logger for chunk-copy progress and failures.
  _COUNT_ATTEMPTS: Retry count for source/target row-count queries.
  _NON_INSERT_SIG_SLOTS: Signature indexes that move only on UPDATE/DELETE.
  _STATE_VERSION: Schema version written into ``--state-file`` JSON.
  _ZSTD_DUMP_ARGS: ``zstd`` argv for optional ``--dump-dir`` audit files.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import Connection, errors as pg_errors

LOG = logging.getLogger("pg18_host_data_chunk_copy")
_COUNT_ATTEMPTS = 5
_STATE_VERSION = 1
_ZSTD_DUMP_ARGS = ("zstd", "-T4", "-3")


@dataclass(frozen=True)
class ChunkRow:
  """
  One ``timescaledb_information.chunks`` row for ``host_data``.

  Attributes:
    chunk_schema: Schema holding the chunk relation.
    chunk_name: Unqualified chunk relation name.
    range_start: Inclusive chunk time bound (UTC).
    range_end: Exclusive chunk time bound (UTC).
    is_compressed: True when the source chunk is compressed.
  """

  chunk_schema: str
  chunk_name: str
  range_start: datetime
  range_end: datetime
  is_compressed: bool

  @property
  def regclass(self) -> str:
    """
    Return the qualified chunk relation name for COPY.

    Returns:
      str: ``schema.name`` suitable for ``COPY (SELECT ... FROM ...)``.

    Examples:
      >>> ChunkRow(
      ...   "_timescaledb_internal",
      ...   "_hyper_1_1_chunk",
      ...   datetime(2026, 1, 1, tzinfo=UTC),
      ...   datetime(2026, 1, 2, tzinfo=UTC),
      ...   False,
      ... ).regclass
      '_timescaledb_internal._hyper_1_1_chunk'
    """
    return f"{self.chunk_schema}.{self.chunk_name}"


@dataclass(frozen=True)
class CopyOptions:
  """
  Connection and behavior settings shared by every chunk worker.

  Attributes:
    source_host: PG15 hostname (compose alias such as ``db``).
    target_host: PG18 hostname (compose alias such as ``db18``).
    port: Shared Postgres port.
    user: Role name.
    database: Database name.
    columns: Target ``host_data`` column names in attnum order.
    dump_dir: Optional directory for ``chunk_*.pgcopy.zst`` audit files.
    force: Re-copy even when row counts already match.
    verify: Writers are stopped; recount source after copy and fail on any
      mismatch or unknown count.
    compress_before: Compress target chunks whose ``range_end`` is at or
      before this instant; ``None`` disables compression.
    slice_minutes: Slice width for insert-only resync of a partially synced
      chunk (only mismatched slices are re-copied); ``0`` disables.
  """

  source_host: str
  target_host: str
  port: int
  user: str
  database: str
  columns: tuple[str, ...]
  dump_dir: str | None = None
  force: bool = False
  verify: bool = False
  compress_before: datetime | None = None
  slice_minutes: int = 60


@dataclass(frozen=True)
class ChunkResult:
  """
  Outcome of one chunk: whether it was skipped or copied, and what was verified.

  Attributes:
    outcome: ``"skipped"`` (counts matched) or ``"copied"``.
    rows: Target row count for the chunk range after the step.
    sig: Source change signature captured **before** counting/copying.
  """

  outcome: str
  rows: int
  sig: tuple[int, ...]


@dataclass(frozen=True)
class RunSummary:
  """
  Totals for one :func:`run_chunk_copies` invocation.

  Attributes:
    unchanged: Chunks skipped by ``--changed-only`` without any query.
    skipped: Chunks whose source/target counts already matched.
    copied: Chunks deleted + re-copied on the target.
    failed: Chunks whose worker raised (logged; run continued).
    rows_total: Sum of verified target rows over every processed chunk.
  """

  unchanged: int
  skipped: int
  copied: int
  failed: int
  rows_total: int


def parse_chunk_tsv(lines: Iterable[str]) -> list[ChunkRow]:
  """
  Parse pipe-delimited TSV lines into :class:`ChunkRow` values.

  Expected columns: chunk_schema, chunk_name, range_start, range_end,
  is_compressed (t/f). Kept for unit tests and offline fixtures.

  Args:
    lines (Iterable[str]): Raw TSV lines (five fields per line).

  Returns:
    list[ChunkRow]: Parsed chunk metadata rows.

  Raises:
    ValueError: Raised when a line does not have five pipe-separated fields.

  Examples:
    >>> parse_chunk_tsv([])
    []
  """
  rows: list[ChunkRow] = []
  for raw in lines:
    line = raw.strip()
    if not line:
      continue
    parts = line.split("|")
    if len(parts) != 5:
      raise ValueError(f"expected 5 TSV fields, got {len(parts)}: {line!r}")
    schema, name, start_s, end_s, compressed_s = parts
    rows.append(
      ChunkRow(
        chunk_schema=schema,
        chunk_name=name,
        range_start=_parse_pg_timestamptz(start_s),
        range_end=_parse_pg_timestamptz(end_s),
        is_compressed=compressed_s.strip().lower() in {"t", "true", "1"},
      )
    )
  return rows


def _parse_pg_timestamptz(value: str) -> datetime:
  """
  Parse a PostgreSQL timestamptz text form into an aware UTC datetime.

  Args:
    value (str): Timestamp text (space or ``T`` separator).

  Returns:
    datetime: Timezone-aware UTC datetime.

  Examples:
    >>> _parse_pg_timestamptz("2026-07-01 00:00:00+00").year
    2026
  """
  text = value.strip().replace(" ", "T")
  if text.endswith(("+00", "-00")):
    text = text[:-3] + "+00:00"
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  # Handle "+00:00" already; also bare timestamps -> assume UTC.
  if "+" not in text[10:] and "-" not in text[10:]:
    text = text + "+00:00"
  dt = datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=UTC)
  return dt.astimezone(UTC)


def filter_chunks_by_watermark(
  chunks: Sequence[ChunkRow],
  *,
  watermark: datetime,
) -> list[ChunkRow]:
  """
  Return chunks whose ``range_end`` is strictly before ``watermark``.

  Open/hot chunks (``range_end >= watermark``) are deferred to freeze/final
  dump. Never includes the parent hypertable ``host_data``.

  Args:
    chunks (Sequence[ChunkRow]): Catalog rows from the source.
    watermark (datetime): Exclusive upper bound on ``range_end``.

  Returns:
    list[ChunkRow]: Chunks safe for live watermarked copy.

  Examples:
    >>> filter_chunks_by_watermark(
    ...   [], watermark=datetime(2026, 1, 1, tzinfo=UTC)
    ... )
    []
  """
  return [c for c in chunks if c.range_end < watermark]


def watermark_from_now(*, days: float, now: datetime | None = None) -> datetime:
  """
  Compute ``now - days`` as an aware UTC watermark.

  Args:
    days (float): Age threshold in days.
    now (datetime | None): Override clock (tests); default ``datetime.now(UTC)``.

  Returns:
    datetime: Aware UTC watermark.

  Examples:
    >>> watermark_from_now(days=3, now=datetime(2026, 9, 4, tzinfo=UTC)).day
    1
  """
  base = now if now is not None else datetime.now(UTC)
  if base.tzinfo is None:
    base = base.replace(tzinfo=UTC)
  return base.astimezone(UTC) - timedelta(days=days)


def quote_ident(name: str) -> str:
  """
  Double-quote a SQL identifier (embedded quotes doubled).

  Args:
    name (str): Column or relation name.

  Returns:
    str: Quoted identifier safe to splice into SQL text.

  Examples:
    >>> quote_ident("time")
    '"time"'
    >>> quote_ident('a"b')
    '"a""b"'
  """
  return '"' + name.replace('"', '""') + '"'


def build_delete_range_sql(
  chunk: ChunkRow,
  start: datetime | None = None,
  end: datetime | None = None,
) -> str:
  """
  Build SQL that deletes target rows in ``[start, end)`` (default: the chunk).

  Args:
    chunk (ChunkRow): Source chunk whose time bounds define the delete window.
    start (datetime | None): Slice start; default ``chunk.range_start``.
    end (datetime | None): Slice end; default ``chunk.range_end``.

  Returns:
    str: ``DELETE FROM host_data WHERE ...`` statement.

  Examples:
    >>> "DELETE FROM host_data" in build_delete_range_sql(
    ...   ChunkRow(
    ...     "_timescaledb_internal",
    ...     "_hyper_1_2_chunk",
    ...     datetime(2026, 8, 10, tzinfo=UTC),
    ...     datetime(2026, 8, 11, tzinfo=UTC),
    ...     False,
    ...   )
    ... )
    True
  """
  return f"DELETE FROM host_data WHERE {_time_window_sql(chunk, start, end)};"


def _time_window_sql(
  chunk: ChunkRow, start: datetime | None, end: datetime | None
) -> str:
  """
  Render ``time >= ... AND time < ...`` with chunk bounds as defaults.

  Args:
    chunk (ChunkRow): Chunk supplying default bounds.
    start (datetime | None): Inclusive lower bound override.
    end (datetime | None): Exclusive upper bound override.

  Returns:
    str: SQL predicate over ``time`` with ``TIMESTAMPTZ`` literals.

  Examples:
    >>> _time_window_sql(
    ...   ChunkRow(
    ...     "s",
    ...     "c",
    ...     datetime(2026, 1, 1, tzinfo=UTC),
    ...     datetime(2026, 1, 2, tzinfo=UTC),
    ...     False,
    ...   ),
    ...   None,
    ...   None,
    ... ).startswith("time >= TIMESTAMPTZ '2026-01-01")
    True
  """
  lo = (start or chunk.range_start).isoformat()
  hi = (end or chunk.range_end).isoformat()
  return f"time >= TIMESTAMPTZ '{lo}' AND time < TIMESTAMPTZ '{hi}'"


def build_copy_out_sql(
  chunk: ChunkRow,
  columns: Sequence[str],
  start: datetime | None = None,
  end: datetime | None = None,
) -> str:
  """
  Build a binary ``COPY (SELECT <cols> FROM <chunk>) TO STDOUT``.

  Never the parent hypertable. Columns are listed explicitly so source and
  target attnum order cannot silently differ. With ``start``/``end`` narrower
  than the chunk only that slice is streamed (no ``WHERE`` for the full range).

  Args:
    chunk (ChunkRow): Source chunk to stream.
    columns (Sequence[str]): Column names in target order.
    start (datetime | None): Optional inclusive slice start.
    end (datetime | None): Optional exclusive slice end.

  Returns:
    str: ``COPY ... TO STDOUT (FORMAT binary)`` statement.

  Raises:
    ValueError: Raised when ``chunk`` names the empty parent ``host_data``.

  Examples:
    >>> build_copy_out_sql(
    ...   ChunkRow(
    ...     "_timescaledb_internal",
    ...     "_hyper_1_2_chunk",
    ...     datetime(2026, 1, 1, tzinfo=UTC),
    ...     datetime(2026, 1, 2, tzinfo=UTC),
    ...     False,
    ...   ),
    ...   ["time", "host"],
    ... )
    'COPY (SELECT "time", "host" FROM _timescaledb_internal._hyper_1_2_chunk) TO STDOUT (FORMAT binary)'
  """
  if chunk.chunk_name == "host_data" and chunk.chunk_schema in {"public", ""}:
    raise ValueError("refusing to COPY parent hypertable host_data")
  cols = ", ".join(quote_ident(c) for c in columns)
  where = ""
  bounds = (start or chunk.range_start, end or chunk.range_end)
  if bounds != (chunk.range_start, chunk.range_end):
    where = f" WHERE {_time_window_sql(chunk, start, end)}"
  return (
    f"COPY (SELECT {cols} FROM {chunk.regclass}{where}) TO STDOUT "
    "(FORMAT binary)"
  )


def build_copy_in_sql(columns: Sequence[str]) -> str:
  """
  Build binary ``COPY host_data (<cols>) FROM STDIN`` - Timescale routes rows.

  Args:
    columns (Sequence[str]): Column names in target order.

  Returns:
    str: ``COPY host_data (...) FROM STDIN (FORMAT binary)`` statement.

  Examples:
    >>> build_copy_in_sql(["time", "host"])
    'COPY host_data ("time", "host") FROM STDIN (FORMAT binary)'
  """
  cols = ", ".join(quote_ident(c) for c in columns)
  return f"COPY host_data ({cols}) FROM STDIN (FORMAT binary)"


def list_source_chunks_sql() -> str:
  """
  Return the catalog query for ``host_data`` chunks (excludes the empty parent).

  Returns:
    str: SQL selecting chunk_schema, chunk_name, range bounds, is_compressed.

  Examples:
    >>> "hypertable_name" in list_source_chunks_sql()
    True
  """
  return """
SELECT chunk_schema, chunk_name, range_start, range_end, is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'host_data'
ORDER BY range_start;
""".strip()


def chunk_signature_sql(*, one_chunk: bool) -> str:
  """
  Return SQL reading per-chunk change counters on the source.

  The signature is ``n_tup_ins/upd/del`` of the chunk heap plus its compressed
  relation (if any). Any insert, update, delete, (de)compression or stats reset
  changes it; untouched chunks keep it, so ``--changed-only`` can trust the
  state file for them. **Source only:** the catalog shape matches the Hub
  TimescaleDB 2.28 on PG15 (2.31 on PG18 dropped ``chunk.schema_name``).

  Args:
    one_chunk (bool): When True, filter by ``%s`` schema and table name.

  Returns:
    str: Query yielding ``regclass, ins, upd, del, c_ins, c_upd, c_del``.

  Examples:
    >>> "pg_stat_all_tables" in chunk_signature_sql(one_chunk=False)
    True
    >>> chunk_signature_sql(one_chunk=True).count("%s")
    2
  """
  sql = """
SELECT ch.schema_name || '.' || ch.table_name,
  coalesce(s.n_tup_ins, 0), coalesce(s.n_tup_upd, 0), coalesce(s.n_tup_del, 0),
  coalesce(cs.n_tup_ins, 0), coalesce(cs.n_tup_upd, 0),
  coalesce(cs.n_tup_del, 0)
FROM _timescaledb_catalog.chunk ch
JOIN _timescaledb_catalog.hypertable h ON h.id = ch.hypertable_id
LEFT JOIN _timescaledb_catalog.chunk cc ON cc.id = ch.compressed_chunk_id
LEFT JOIN pg_stat_all_tables s ON s.relid = to_regclass(
  quote_ident(ch.schema_name) || '.' || quote_ident(ch.table_name))
LEFT JOIN pg_stat_all_tables cs ON cs.relid = to_regclass(
  quote_ident(cc.schema_name) || '.' || quote_ident(cc.table_name))
WHERE h.schema_name = 'public' AND h.table_name = 'host_data'""".strip()
  if one_chunk:
    sql += " AND ch.schema_name = %s AND ch.table_name = %s"
  return sql


# Signature slots that move only on UPDATE/DELETE (heap upd/del, compressed
# upd/del). Unchanged => the chunk only gained rows since it was verified.
_NON_INSERT_SIG_SLOTS = (1, 2, 4, 5)


def inserts_only_since(
  chunk: ChunkRow,
  entry: dict[str, object] | None,
  sig: tuple[int, ...],
) -> bool:
  """
  Return True when a verified chunk has only gained rows since its record.

  ``host_data`` ingest is ``ON CONFLICT DO NOTHING`` (insert-only); operator
  repair tools UPDATE/DELETE and move the non-insert counters. Only then is a
  per-slice count diff an exact change detector.

  Args:
    chunk (ChunkRow): Source chunk under consideration.
    entry (dict[str, object] | None): Previous ``--state-file`` record.
    sig (tuple[int, ...]): Current source signature (six counters).

  Returns:
    bool: True when slice resync is safe for this chunk.

  Examples:
    >>> c = ChunkRow(
    ...   "s",
    ...   "c",
    ...   datetime(2026, 1, 1, tzinfo=UTC),
    ...   datetime(2026, 1, 2, tzinfo=UTC),
    ...   False,
    ... )
    >>> e = state_entry(c, ChunkResult("copied", 5, (5, 0, 0, 0, 0, 0)))
    >>> inserts_only_since(c, e, (9, 0, 0, 0, 0, 0))
    True
    >>> inserts_only_since(c, e, (9, 1, 0, 0, 0, 0))
    False
  """
  if entry is None or len(sig) != 6:
    return False
  prior = list(entry.get("sig") or [])
  rows = entry.get("rows")
  return (
    len(prior) == 6
    and isinstance(rows, int)
    and rows >= 0
    and entry.get("range_start") == chunk.range_start.isoformat()
    and entry.get("range_end") == chunk.range_end.isoformat()
    and all(prior[i] == sig[i] for i in _NON_INSERT_SIG_SLOTS)
  )


def fetch_slice_counts(
  conn: Connection,
  chunk: ChunkRow,
  minutes: int,
  *,
  source: bool,
) -> dict[datetime, int] | None:
  """
  Count rows per ``minutes``-wide slice of ``chunk`` (bins from range_start).

  Args:
    conn (Connection): Source or target connection.
    chunk (ChunkRow): Chunk whose range is binned.
    minutes (int): Slice width in minutes.
    source (bool): True reads the source chunk relation, False target
      ``host_data`` restricted to the chunk range.

  Returns:
    dict[datetime, int] | None: Slice start to row count, or ``None`` after
      a retryable cancel (caller falls back to a full chunk copy).

  Raises:
    Exception: Re-raised when the failure is not a retryable cancel.

  Examples:
    >>> fetch_slice_counts(src, chunk, 60, source=True)  # doctest: +SKIP
    {datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc): 3600}
  """
  binned = "SELECT date_bin(%s::interval, time, %s::timestamptz), count(*) "
  params: list[object] = [f"{minutes} minutes", chunk.range_start]
  if source:
    sql = binned + f"FROM {chunk.regclass} GROUP BY 1"
  else:
    sql = binned + "FROM host_data WHERE time >= %s AND time < %s GROUP BY 1"
    params += [chunk.range_start, chunk.range_end]
  try:
    with conn.cursor() as cur:
      cur.execute("SET statement_timeout = 0")
      cur.execute("SET max_parallel_workers_per_gather = 0")
      cur.execute(sql, params)
      return {_as_utc_datetime(b): int(n) for b, n in cur.fetchall()}
  except Exception as exc:
    if not _is_retryable_count_error(exc):
      raise
    LOG.warning("slice count canceled chunk=%s: %s", chunk.regclass, exc)
    return None


def mismatched_slices(
  chunk: ChunkRow,
  src: dict[datetime, int],
  tgt: dict[datetime, int],
  minutes: int,
) -> list[tuple[datetime, datetime]]:
  """
  Return ``[start, end)`` windows whose source and target counts differ.

  Args:
    chunk (ChunkRow): Chunk supplying the outer bounds.
    src (dict[datetime, int]): Source slice counts.
    tgt (dict[datetime, int]): Target slice counts.
    minutes (int): Slice width in minutes.

  Returns:
    list[tuple[datetime, datetime]]: Sorted windows clamped to the chunk.

  Examples:
    >>> c = ChunkRow(
    ...   "s",
    ...   "c",
    ...   datetime(2026, 1, 1, tzinfo=UTC),
    ...   datetime(2026, 1, 2, tzinfo=UTC),
    ...   False,
    ... )
    >>> h = datetime(2026, 1, 1, 5, tzinfo=UTC)
    >>> mismatched_slices(c, {h: 2}, {h: 1}, 60)[0][1].hour
    6
  """
  width = timedelta(minutes=minutes)
  return [
    (max(b, chunk.range_start), min(b + width, chunk.range_end))
    for b in sorted(set(src) | set(tgt))
    if src.get(b, 0) != tgt.get(b, 0)
  ]


def connect_pg(
  *,
  host: str,
  port: int,
  user: str,
  database: str,
) -> Connection:
  """
  Open an autocommit psycopg connection (password from ``PGPASSWORD``).

  Args:
    host (str): Postgres hostname (compose alias such as ``db`` / ``db18``).
    port (int): Postgres port.
    user (str): Role name.
    database (str): Database name.

  Returns:
    Connection: Open psycopg connection with ``autocommit=True``.

  Examples:
    >>> conn = connect_pg(  # doctest: +SKIP
    ...   host="db18", port=5432, user="hpcperfstats", database="hpcperfstats"
    ... )
  """
  return psycopg.connect(
    host=host,
    port=port,
    user=user,
    dbname=database,
    password=os.environ.get("PGPASSWORD") or "",
    autocommit=True,
  )


def _connect(opts: CopyOptions, host: str) -> Connection:
  """
  Open a connection to ``host`` using the shared port/user/database in ``opts``.

  Args:
    opts (CopyOptions): Shared connection settings.
    host (str): ``opts.source_host`` or ``opts.target_host``.

  Returns:
    Connection: Open autocommit connection.

  Examples:
    >>> _connect(opts, opts.target_host)  # doctest: +SKIP
  """
  return connect_pg(
    host=host, port=opts.port, user=opts.user, database=opts.database
  )


def _as_utc_datetime(value: object) -> datetime:
  """
  Normalize a DB timestamptz / string into an aware UTC datetime.

  Args:
    value (object): ``datetime`` from psycopg or a timestamp string.

  Returns:
    datetime: Timezone-aware UTC datetime.

  Raises:
    TypeError: Raised when ``value`` is neither datetime nor str.

  Examples:
    >>> _as_utc_datetime(datetime(2026, 1, 1, tzinfo=UTC)).year
    2026
  """
  if isinstance(value, datetime):
    dt = value
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
  if isinstance(value, str):
    return _parse_pg_timestamptz(value)
  raise TypeError(f"expected datetime or str, got {type(value)!r}")


def fetch_source_chunks(
  *,
  host: str,
  port: int,
  user: str,
  database: str,
) -> list[ChunkRow]:
  """
  Query ``timescaledb_information.chunks`` on the source via psycopg.

  Args:
    host (str): Source Postgres hostname.
    port (int): Source port.
    user (str): Role name.
    database (str): Database name.

  Returns:
    list[ChunkRow]: Parsed source chunks.

  Examples:
    >>> fetch_source_chunks(  # doctest: +SKIP
    ...   host="db", port=5432, user="hpcperfstats", database="hpcperfstats"
    ... )
  """
  with (
    connect_pg(host=host, port=port, user=user, database=database) as conn,
    conn.cursor() as cur,
  ):
    cur.execute(list_source_chunks_sql())
    raw_rows = cur.fetchall()
  rows: list[ChunkRow] = []
  for schema, name, start, end, compressed in raw_rows:
    rows.append(
      ChunkRow(
        chunk_schema=str(schema),
        chunk_name=str(name),
        range_start=_as_utc_datetime(start),
        range_end=_as_utc_datetime(end),
        is_compressed=bool(compressed),
      )
    )
  return rows


def fetch_column_layout(conn: Connection) -> list[tuple[str, str]]:
  """
  Return ``host_data`` ``(column, format_type)`` pairs in attnum order.

  Args:
    conn (Connection): Open source or target connection.

  Returns:
    list[tuple[str, str]]: Live (non-dropped) columns and their SQL types.

  Examples:
    >>> fetch_column_layout(conn)  # doctest: +SKIP
    [('time', 'timestamp with time zone'), ('host', 'character varying(64)')]
  """
  with conn.cursor() as cur:
    cur.execute(
      "SELECT a.attname, format_type(a.atttypid, a.atttypmod) "
      "FROM pg_attribute a "
      "WHERE a.attrelid = 'public.host_data'::regclass "
      "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
    )
    return [(str(n), str(t)) for n, t in cur.fetchall()]


def matched_copy_columns(
  source: Sequence[tuple[str, str]],
  target: Sequence[tuple[str, str]],
) -> tuple[str, ...]:
  """
  Return target column order after proving both layouts carry the same types.

  Binary COPY needs identical types per column; a missing or retyped column
  would drop or corrupt data, so any difference fails closed.

  Args:
    source (Sequence[tuple[str, str]]): Source ``(name, type)`` pairs.
    target (Sequence[tuple[str, str]]): Target ``(name, type)`` pairs.

  Returns:
    tuple[str, ...]: Column names in target attnum order.

  Raises:
    ValueError: Raised when names or types differ between the layouts.

  Examples:
    >>> matched_copy_columns(
    ...   [("host", "text"), ("time", "timestamptz")],
    ...   [("time", "timestamptz"), ("host", "text")],
    ... )
    ('time', 'host')
  """
  if not target or dict(source) != dict(target):
    raise ValueError(
      f"host_data column layout differs: source={list(source)} "
      f"target={list(target)}"
    )
  return tuple(name for name, _ in target)


def fetch_chunk_signatures(
  conn: Connection,
  chunk: ChunkRow | None = None,
) -> dict[str, tuple[int, ...]]:
  """
  Read source change signatures for every chunk, or only ``chunk``.

  Args:
    conn (Connection): Open source connection.
    chunk (ChunkRow | None): Restrict to this chunk; ``None`` reads all.

  Returns:
    dict[str, tuple[int, ...]]: ``regclass`` to six counter values.

  Examples:
    >>> fetch_chunk_signatures(src)  # doctest: +SKIP
    {'_timescaledb_internal._hyper_1_1_chunk': (10, 0, 0, 0, 0, 0)}
  """
  params = () if chunk is None else (chunk.chunk_schema, chunk.chunk_name)
  with conn.cursor() as cur:
    cur.execute(
      chunk_signature_sql(one_chunk=chunk is not None), params or None
    )
    return {str(r[0]): tuple(int(v) for v in r[1:]) for r in cur.fetchall()}


def chunk_row_counts_match(source_n: int, target_n: int) -> bool:
  """
  Return True when source and target row counts are equal (already synced).

  Negative counts mean a skip-check count failed after retries and must not
  be treated as synced.

  Args:
    source_n (int): ``count(*)`` from the source chunk relation, or ``-1``
      when the count could not be completed.
    target_n (int): ``count(*)`` on target ``host_data`` for the chunk time
      range, or ``-1`` when the count could not be completed.

  Returns:
    bool: True when both counts are non-negative and equal (including both
      zero).

  Examples:
    >>> chunk_row_counts_match(10, 10)
    True
    >>> chunk_row_counts_match(10, 9)
    False
    >>> chunk_row_counts_match(-1, -1)
    False
  """
  return source_n >= 0 and target_n >= 0 and source_n == target_n


def chunk_unchanged_since_state(
  chunk: ChunkRow,
  entry: dict[str, object] | None,
  sig: tuple[int, ...] | None,
) -> bool:
  """
  Return True when the state entry proves the chunk needs no work.

  Requires the same bounds, a recorded non-negative row count, and a current
  source signature identical to the one captured before that chunk was last
  verified. Missing signature (chunk vanished) or entry never counts.

  Args:
    chunk (ChunkRow): Source chunk under consideration.
    entry (dict[str, object] | None): ``--state-file`` record for the chunk.
    sig (tuple[int, ...] | None): Current source signature.

  Returns:
    bool: True when the chunk can be skipped without any query.

  Examples:
    >>> c = ChunkRow(
    ...   "s",
    ...   "c",
    ...   datetime(2026, 1, 1, tzinfo=UTC),
    ...   datetime(2026, 1, 2, tzinfo=UTC),
    ...   False,
    ... )
    >>> e = state_entry(c, ChunkResult("copied", 5, (5, 0, 0, 0, 0, 0)))
    >>> chunk_unchanged_since_state(c, e, (5, 0, 0, 0, 0, 0))
    True
    >>> chunk_unchanged_since_state(c, e, (6, 0, 0, 0, 0, 0))
    False
  """
  if entry is None or sig is None:
    return False
  rows = entry.get("rows")
  return (
    entry.get("range_start") == chunk.range_start.isoformat()
    and entry.get("range_end") == chunk.range_end.isoformat()
    and isinstance(rows, int)
    and rows >= 0
    and list(entry.get("sig") or []) == list(sig)
  )


def state_entry(chunk: ChunkRow, result: ChunkResult) -> dict[str, object]:
  """
  Build the ``--state-file`` record for a verified chunk.

  Args:
    chunk (ChunkRow): Chunk that was skipped or copied.
    result (ChunkResult): Verified outcome with rows and pre-copy signature.

  Returns:
    dict[str, object]: JSON-serializable record.

  Examples:
    >>> state_entry(
    ...   ChunkRow(
    ...     "s",
    ...     "c",
    ...     datetime(2026, 1, 1, tzinfo=UTC),
    ...     datetime(2026, 1, 2, tzinfo=UTC),
    ...     False,
    ...   ),
    ...   ChunkResult("skipped", 3, (3, 0, 0, 0, 0, 0)),
    ... )["rows"]
    3
  """
  return {
    "range_start": chunk.range_start.isoformat(),
    "range_end": chunk.range_end.isoformat(),
    "rows": result.rows,
    "sig": list(result.sig),
  }


def load_state(path: str | None) -> dict[str, dict[str, object]]:
  """
  Load per-chunk records from ``--state-file`` (empty when absent).

  Args:
    path (str | None): JSON path, or ``None`` when no state is kept.

  Returns:
    dict[str, dict[str, object]]: ``regclass`` to record.

  Raises:
    ValueError: Raised when the file has an unknown ``version``.

  Examples:
    >>> load_state(None)
    {}
  """
  if not path or not os.path.isfile(path):
    return {}
  with open(path, encoding="utf-8") as fh:
    doc = json.load(fh)
  if doc.get("version") != _STATE_VERSION:
    raise ValueError(f"unsupported state file version in {path}")
  return dict(doc.get("chunks") or {})


def save_state(path: str, chunks: dict[str, dict[str, object]]) -> None:
  """
  Atomically write per-chunk records (temp file + ``os.replace``).

  Args:
    path (str): Destination JSON path; parent directories are created.
    chunks (dict[str, dict[str, object]]): ``regclass`` to record.

  Returns:
    None

  Examples:
    >>> save_state("/tmp/pg18_state.json", {})  # doctest: +SKIP
  """
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  tmp = f"{path}.tmp"
  with open(tmp, "w", encoding="utf-8") as fh:
    json.dump({"version": _STATE_VERSION, "chunks": chunks}, fh)
  os.replace(tmp, path)


def _is_retryable_count_error(exc: BaseException) -> bool:
  """
  Return True for transient count failures (timeout / PG18 I/O cancel).

  Args:
    exc (BaseException): Exception raised during ``count(*)``.

  Returns:
    bool: True when the error is safe to retry or treat as unsynced.

  Examples:
    >>> _is_retryable_count_error(RuntimeError("x"))
    False
  """
  if isinstance(exc, pg_errors.QueryCanceled):
    return True
  if isinstance(exc, pg_errors.InternalError_):
    return "operation canceled" in str(exc).lower()
  return False


def _count_with_retry(
  conn: Connection,
  sql: str,
  params: Sequence[object] = (),
  *,
  label: str,
) -> int:
  """
  Run ``count(*)`` with timeout disabled, retries, and ``-1`` on give-up.

  Args:
    conn (Connection): Open Postgres connection.
    sql (str): Count SQL (``SELECT count(*) ...``).
    params (Sequence[object]): Bind parameters for ``sql``.
    label (str): Log label for retries (source/target).

  Returns:
    int: Row count, or ``-1`` when retryable cancels persist.

  Raises:
    Exception: Re-raised when the failure is not a retryable cancel.

  Examples:
    >>> _count_with_retry(  # doctest: +SKIP
    ...   conn, "SELECT count(*) FROM host_data", label="target"
    ... )
    0
  """
  delay = 0.25
  last: BaseException | None = None
  for attempt in range(1, _COUNT_ATTEMPTS + 1):
    try:
      with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET max_parallel_workers_per_gather = 0")
        cur.execute(sql, params)
        row = cur.fetchone()
      return int(row[0]) if row else 0
    except Exception as exc:
      last = exc
      if not _is_retryable_count_error(exc):
        raise
      LOG.warning(
        "%s count attempt %s/%s failed: %s",
        label,
        attempt,
        _COUNT_ATTEMPTS,
        exc,
      )
      if attempt == _COUNT_ATTEMPTS:
        break
      time.sleep(delay)
      delay = min(delay * 2.0, 4.0)
  LOG.warning(
    "%s count giving up after retries; treat range as unsynced: %s",
    label,
    last,
  )
  return -1


def count_source_chunk_rows(conn: Connection, chunk: ChunkRow) -> int:
  """
  Count rows in the source chunk relation.

  Retries transient cancels; returns ``-1`` if they persist so the caller
  re-copies instead of aborting the migrate.

  Args:
    conn (Connection): Open source connection.
    chunk (ChunkRow): Source chunk metadata.

  Returns:
    int: ``count(*)`` from ``chunk.regclass``, or ``-1`` on persistent
      cancel.

  Examples:
    >>> count_source_chunk_rows(src, chunk)  # doctest: +SKIP
    86400
  """
  return _count_with_retry(
    conn,
    f"SELECT count(*) FROM {chunk.regclass}",
    label=f"source {chunk.regclass}",
  )


def count_target_range_rows(conn: Connection, chunk: ChunkRow) -> int:
  """
  Count target ``host_data`` rows in ``[range_start, range_end)``.

  Retries transient cancels (statement timeout / PG18 ``Operation canceled``
  under parallel workers); returns ``-1`` if they persist so the caller
  re-copies instead of aborting the migrate.

  Args:
    conn (Connection): Open target connection.
    chunk (ChunkRow): Chunk whose time bounds define the range.

  Returns:
    int: ``count(*)`` on target for that time window, or ``-1`` on
      persistent cancel.

  Examples:
    >>> count_target_range_rows(tgt, chunk)  # doctest: +SKIP
    86400
  """
  return _count_with_retry(
    conn,
    "SELECT count(*) FROM host_data WHERE time >= %s AND time < %s",
    (chunk.range_start, chunk.range_end),
    label=f"target {chunk.regclass}",
  )


def _target_chunks_overlapping(
  conn: Connection, chunk: ChunkRow
) -> list[tuple[str, bool, datetime, datetime]]:
  """
  List target chunks overlapping ``chunk``'s time range.

  Args:
    conn (Connection): Open target connection.
    chunk (ChunkRow): Source chunk whose bounds define the range.

  Returns:
    list[tuple[str, bool, datetime, datetime]]: ``(regclass, is_compressed,
      range_start, range_end)`` per overlapping target chunk.

  Examples:
    >>> _target_chunks_overlapping(tgt, chunk)  # doctest: +SKIP
    [('_timescaledb_internal._hyper_1_9_chunk', False, ..., ...)]
  """
  with conn.cursor() as cur:
    cur.execute(
      "SELECT quote_ident(chunk_schema) || '.' || quote_ident(chunk_name), "
      "is_compressed, range_start, range_end "
      "FROM timescaledb_information.chunks "
      "WHERE hypertable_name = 'host_data' "
      "AND range_start < %s AND range_end > %s",
      (chunk.range_end, chunk.range_start),
    )
    return [
      (str(r[0]), bool(r[1]), _as_utc_datetime(r[2]), _as_utc_datetime(r[3]))
      for r in cur.fetchall()
    ]


def decompress_target_range(conn: Connection, chunk: ChunkRow) -> None:
  """
  Decompress compressed target chunks overlapping ``chunk`` before a delete.

  Deleting from compressed chunks would decompress per DML transaction and
  can hit ``max_tuples_decompressed_per_dml_transaction``; doing it up front
  keeps the delete + COPY path plain heap writes.

  Args:
    conn (Connection): Open target connection.
    chunk (ChunkRow): Source chunk whose range will be replaced.

  Returns:
    None

  Examples:
    >>> decompress_target_range(tgt, chunk)  # doctest: +SKIP
  """
  for regclass, compressed, _start, _end in _target_chunks_overlapping(
    conn, chunk
  ):
    if compressed:
      with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        cur.execute(
          "SELECT decompress_chunk(%s::regclass, if_compressed => true)",
          (regclass,),
        )


def compress_target_range(
  conn: Connection, chunk: ChunkRow, compress_before: datetime | None
) -> int:
  """
  Compress target chunks fully inside ``chunk`` that are old enough.

  Only when the source chunk's ``range_end`` is at or before
  ``compress_before`` (mirrors the ``compress_after`` policy); then every
  uncompressed target chunk lying within ``[range_start, range_end)`` is
  compressed. Hot chunks stay uncompressed.

  Args:
    conn (Connection): Open target connection.
    chunk (ChunkRow): Source chunk that was just verified.
    compress_before (datetime | None): Age cut-off; ``None`` disables.

  Returns:
    int: Number of target chunks compressed by this call.

  Examples:
    >>> compress_target_range(tgt, chunk, None)  # doctest: +SKIP
    0
  """
  if compress_before is None or chunk.range_end > compress_before:
    return 0
  done = 0
  for regclass, compressed, start, end in _target_chunks_overlapping(
    conn, chunk
  ):
    inside = start >= chunk.range_start and end <= chunk.range_end
    if compressed or not inside:
      continue
    with conn.cursor() as cur:
      cur.execute("SET statement_timeout = 0")
      cur.execute(
        "SELECT compress_chunk(%s::regclass, if_not_compressed => true)",
        (regclass,),
      )
    done += 1
  return done


def _stream_chunk(
  src: Connection,
  tgt: Connection,
  chunk: ChunkRow,
  opts: CopyOptions,
  window: tuple[datetime, datetime] | None = None,
) -> None:
  """
  Stream one source chunk into target ``host_data`` (binary COPY).

  With ``opts.dump_dir`` the stream is first written to a zstd audit file and
  restored from it; otherwise source COPY OUT feeds target COPY IN directly.

  Args:
    src (Connection): Open source connection.
    tgt (Connection): Open target connection.
    chunk (ChunkRow): Source chunk to stream.
    opts (CopyOptions): Column list and dump settings.
    window (tuple[datetime, datetime] | None): Optional ``[start, end)``
      slice; ``None`` streams the whole chunk.

  Returns:
    None

  Raises:
    RuntimeError: Raised when a zstd dump/restore stage fails.

  Examples:
    >>> _stream_chunk(src, tgt, chunk, opts)  # doctest: +SKIP
  """
  out_sql = build_copy_out_sql(chunk, opts.columns, *(window or (None, None)))
  in_sql = build_copy_in_sql(opts.columns)
  with src.cursor() as scur, tgt.cursor() as tcur:
    scur.execute("SET statement_timeout = 0")
    tcur.execute("SET statement_timeout = 0")
    if not opts.dump_dir:
      with scur.copy(out_sql) as copy_out, tcur.copy(in_sql) as copy_in:
        for data in copy_out:
          copy_in.write(data)
      return
    path = Path(opts.dump_dir) / f"chunk_{chunk.chunk_name}.pgcopy.zst"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as out_f:
      zstd = subprocess.Popen(
        list(_ZSTD_DUMP_ARGS), stdin=subprocess.PIPE, stdout=out_f
      )
      assert zstd.stdin is not None
      with scur.copy(out_sql) as copy_out:
        for data in copy_out:
          zstd.stdin.write(bytes(data))
      zstd.stdin.close()
      if zstd.wait() != 0:
        raise RuntimeError(f"dump failed chunk={chunk.regclass}")
    with path.open("rb") as in_f:
      unz = subprocess.Popen(
        ["zstd", "-dc"], stdin=in_f, stdout=subprocess.PIPE
      )
      assert unz.stdout is not None
      with tcur.copy(in_sql) as copy_in:
        while buf := unz.stdout.read(1024 * 1024):
          copy_in.write(buf)
      if unz.wait() != 0:
        raise RuntimeError(f"restore-from-dump failed chunk={chunk.regclass}")


def copy_one_chunk(
  chunk: ChunkRow,
  *,
  opts: CopyOptions,
  prior: dict[str, object] | None = None,
) -> ChunkResult:
  """
  Skip when row counts match, else decompress + delete-range + COPY + recount.

  The source signature is read **first**, so any write after it shows up as a
  changed signature on the next ``--changed-only`` pass. After a copy the
  target range is recounted; with ``opts.verify`` (writers stopped) the source
  is recounted too and any difference or unknown count raises. Eligible
  target chunks are compressed afterwards.

  When ``prior`` proves the chunk was verified and has only gained rows since
  (:func:`inserts_only_since`), only slices whose counts differ are deleted and
  re-copied - the hot chunk at freeze costs minutes of data, not a full day.

  Args:
    chunk (ChunkRow): Source chunk to copy.
    opts (CopyOptions): Connection, column, verify and compression settings.
    prior (dict[str, object] | None): Previous ``--state-file`` record.

  Returns:
    ChunkResult: Outcome, verified target rows and pre-copy signature.

  Raises:
    RuntimeError: Raised on verify mismatch or zstd dump/restore failure.
    ValueError: Raised when ``chunk`` names the parent ``host_data`` relation.

  Examples:
    >>> copy_one_chunk(chunk, opts=opts)  # doctest: +SKIP
    ChunkResult(outcome='skipped', rows=86400, sig=(86400, 0, 0, 0, 0, 0))
  """
  with (
    _connect(opts, opts.source_host) as src,
    _connect(opts, opts.target_host) as tgt,
  ):
    sig = fetch_chunk_signatures(src, chunk).get(chunk.regclass, ())
    src_n = count_source_chunk_rows(src, chunk)
    tgt_n = count_target_range_rows(tgt, chunk)
    if not opts.force and chunk_row_counts_match(src_n, tgt_n):
      LOG.info("skip chunk=%s rows=%s (already synced)", chunk.regclass, src_n)
      compress_target_range(tgt, chunk, opts.compress_before)
      return ChunkResult("skipped", src_n, sig)

    LOG.info(
      "copy chunk=%s range=[%s,%s) compressed=%s src_rows=%s tgt_rows=%s",
      chunk.regclass,
      chunk.range_start.isoformat(),
      chunk.range_end.isoformat(),
      chunk.is_compressed,
      src_n,
      tgt_n,
    )
    decompress_target_range(tgt, chunk)
    windows = _resync_windows(src, tgt, chunk, opts, prior, sig, tgt_n)
    for start, end in windows:
      with tgt.cursor() as tcur:
        tcur.execute("SET statement_timeout = 0")
        tcur.execute(build_delete_range_sql(chunk, start, end))
      _stream_chunk(src, tgt, chunk, opts, (start, end))
    rows = count_target_range_rows(tgt, chunk)
    if opts.verify:
      src_after = count_source_chunk_rows(src, chunk)
      if not chunk_row_counts_match(src_after, rows):
        raise RuntimeError(
          f"verify mismatch chunk={chunk.regclass} source={src_after} "
          f"target={rows}"
        )
    if rows < 0:
      raise RuntimeError(
        f"post-copy target count failed chunk={chunk.regclass}"
      )
    compress_target_range(tgt, chunk, opts.compress_before)
  return ChunkResult("copied", rows, sig)


def _resync_windows(
  src: Connection,
  tgt: Connection,
  chunk: ChunkRow,
  opts: CopyOptions,
  prior: dict[str, object] | None,
  sig: tuple[int, ...],
  tgt_n: int,
) -> list[tuple[datetime, datetime]]:
  """
  Choose the windows to delete + re-copy: mismatched slices or the chunk.

  Slices are used only for a non-forced, non-dump copy of a chunk the target
  already partly holds whose changes since ``prior`` are inserts only.

  Args:
    src (Connection): Open source connection.
    tgt (Connection): Open target connection.
    chunk (ChunkRow): Chunk being re-synced.
    opts (CopyOptions): Force, dump and slice settings.
    prior (dict[str, object] | None): Previous ``--state-file`` record.
    sig (tuple[int, ...]): Current source signature.
    tgt_n (int): Target row count for the chunk range before the copy.

  Returns:
    list[tuple[datetime, datetime]]: ``[start, end)`` windows to re-copy.

  Examples:
    >>> _resync_windows(src, tgt, chunk, opts, None, (), 0)  # doctest: +SKIP
    [(datetime(2026, 1, 1, ...), datetime(2026, 1, 2, ...))]
  """
  whole = [(chunk.range_start, chunk.range_end)]
  if (
    opts.force
    or opts.dump_dir
    or opts.slice_minutes <= 0
    or tgt_n <= 0
    or not inserts_only_since(chunk, prior, sig)
  ):
    return whole
  s_counts = fetch_slice_counts(src, chunk, opts.slice_minutes, source=True)
  t_counts = fetch_slice_counts(tgt, chunk, opts.slice_minutes, source=False)
  if s_counts is None or t_counts is None:
    return whole
  windows = mismatched_slices(chunk, s_counts, t_counts, opts.slice_minutes)
  LOG.info(
    "slice resync chunk=%s slices=%s of %s (%s min)",
    chunk.regclass,
    len(windows),
    len(set(s_counts) | set(t_counts)),
    opts.slice_minutes,
  )
  return windows


def run_chunk_copies(
  chunks: Sequence[ChunkRow],
  *,
  opts: CopyOptions,
  workers: int,
  state: dict[str, dict[str, object]] | None = None,
  state_path: str | None = None,
  unchanged: int = 0,
) -> RunSummary:
  """
  Copy or skip selected chunks with up to ``workers`` concurrent threads.

  Each worker opens its own source/target connections; disjoint chunk ranges
  are safe in parallel. Results are folded into ``state`` on the main thread
  and saved after every chunk (resume after interruption). A worker that
  raises is logged and counted in ``failed``; the run continues.

  Args:
    chunks (Sequence[ChunkRow]): Chunks that need examining.
    opts (CopyOptions): Settings passed to :func:`copy_one_chunk`.
    workers (int): Max concurrent chunk copies (``1`` = serial).
    state (dict[str, dict[str, object]] | None): Mutable state records.
    state_path (str | None): Where to persist ``state`` after each chunk.
    unchanged (int): Chunks already skipped by ``--changed-only``.

  Returns:
    RunSummary: Totals; ``rows_total`` covers examined chunks only.

  Raises:
    ValueError: Raised when ``workers`` is less than 1.

  Examples:
    >>> run_chunk_copies(
    ...   [],
    ...   opts=CopyOptions("db", "db18", 5432, "u", "d", ("time",)),
    ...   workers=2,
    ... )
    RunSummary(unchanged=0, skipped=0, copied=0, failed=0, rows_total=0)
  """
  if workers < 1:
    raise ValueError("workers must be >= 1")
  counts = {"skipped": 0, "copied": 0, "failed": 0}
  rows_total = 0
  if not chunks:
    return RunSummary(unchanged, 0, 0, 0, 0)
  with ThreadPoolExecutor(max_workers=workers) as pool:
    known = state or {}
    futures = {
      pool.submit(copy_one_chunk, c, opts=opts, prior=known.get(c.regclass)): c
      for c in chunks
    }
    for fut in as_completed(futures):
      chunk = futures[fut]
      try:
        result = fut.result()
      except Exception:
        LOG.exception("chunk failed chunk=%s", chunk.regclass)
        counts["failed"] += 1
        result = None
      if result is not None:
        counts[result.outcome] += 1
        rows_total += result.rows
      if state is None:
        continue
      if result is None:
        state.pop(chunk.regclass, None)
      else:
        state[chunk.regclass] = state_entry(chunk, result)
      if state_path:
        save_state(state_path, state)
  return RunSummary(
    unchanged, counts["skipped"], counts["copied"], counts["failed"], rows_total
  )


def split_unchanged(
  chunks: Sequence[ChunkRow],
  state: dict[str, dict[str, object]],
  sigs: dict[str, tuple[int, ...]],
) -> tuple[list[ChunkRow], list[ChunkRow]]:
  """
  Partition chunks into ``(examine, unchanged)`` using state + signatures.

  Args:
    chunks (Sequence[ChunkRow]): Watermark-selected source chunks.
    state (dict[str, dict[str, object]]): Records from ``--state-file``.
    sigs (dict[str, tuple[int, ...]]): Current source signatures.

  Returns:
    tuple[list[ChunkRow], list[ChunkRow]]: Chunks to examine, and chunks
      proven unchanged since they were last verified.

  Examples:
    >>> split_unchanged([], {}, {})
    ([], [])
  """
  examine: list[ChunkRow] = []
  same: list[ChunkRow] = []
  for c in chunks:
    ok = chunk_unchanged_since_state(
      c, state.get(c.regclass), sigs.get(c.regclass)
    )
    (same if ok else examine).append(c)
  return examine, same


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: list or copy watermarked ``host_data`` chunks.

  Args:
    argv (Sequence[str] | None): Optional argv override (tests); default
      ``None`` reads ``sys.argv``.

  Returns:
    int: ``0`` on success, ``1`` when any chunk failed, ``2`` on bad flags.

  Examples:
    >>> main(["--list-only"])  # doctest: +SKIP
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--source-host", default="db", help="PG15 hostname")
  parser.add_argument("--target-host", default="db18", help="PG18 hostname")
  parser.add_argument("--port", type=int, default=5432)
  parser.add_argument("--user", default="hpcperfstats")
  parser.add_argument("--database", default="hpcperfstats")
  parser.add_argument(
    "--watermark-days",
    type=float,
    default=3.0,
    help="Copy only chunks with range_end < now() - N days (default 3; "
    "negative includes open chunks)",
  )
  parser.add_argument(
    "--dump-dir",
    default=None,
    help="Optional directory for chunk_*.pgcopy.zst audit files",
  )
  parser.add_argument(
    "--list-only",
    action="store_true",
    help="Print selected chunks and exit without copying",
  )
  parser.add_argument(
    "--force",
    action="store_true",
    help="Re-copy even when source/target row counts already match",
  )
  parser.add_argument(
    "--workers",
    type=int,
    default=2,
    help="Max concurrent chunk copies (default 2; use 1 for serial)",
  )
  parser.add_argument(
    "--state-file",
    default=None,
    help="JSON of verified chunks {rows, sig}; saved after each chunk",
  )
  parser.add_argument(
    "--changed-only",
    action="store_true",
    help="Skip chunks whose source signature matches --state-file",
  )
  parser.add_argument(
    "--verify",
    action="store_true",
    help="Writers stopped: recount source after copy; fail on mismatch",
  )
  parser.add_argument(
    "--compress-older-than-days",
    type=float,
    default=8.0,
    help="Compress copied target chunks with range_end <= now - N days "
    "(default 8 = compress_after policy; negative disables)",
  )
  parser.add_argument(
    "--slice-minutes",
    type=int,
    default=60,
    help="Insert-only resync granularity for partly synced chunks "
    "(default 60; 0 = always re-copy whole chunks)",
  )
  parser.add_argument("-v", "--verbose", action="store_true")
  args = parser.parse_args(list(argv) if argv is not None else None)

  logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
  )
  if args.changed_only and not args.state_file:
    LOG.error("--changed-only requires --state-file")
    return 2

  wm = watermark_from_now(days=args.watermark_days)
  chunks = fetch_source_chunks(
    host=args.source_host,
    port=args.port,
    user=args.user,
    database=args.database,
  )
  selected = filter_chunks_by_watermark(chunks, watermark=wm)
  LOG.info(
    "source_chunks=%s selected=%s watermark=%s workers=%s",
    len(chunks),
    len(selected),
    wm.isoformat(),
    args.workers,
  )
  for c in selected:
    print(
      f"{c.regclass}\t{c.range_start.isoformat()}\t{c.range_end.isoformat()}\t"
      f"compressed={c.is_compressed}"
    )
  if args.list_only:
    return 0

  base = CopyOptions(
    args.source_host, args.target_host, args.port, args.user, args.database, ()
  )
  with (
    _connect(base, base.source_host) as src,
    _connect(base, base.target_host) as tgt,
  ):
    columns = matched_copy_columns(
      fetch_column_layout(src), fetch_column_layout(tgt)
    )
    sigs = fetch_chunk_signatures(src) if args.changed_only else {}
  compress_before = (
    None
    if args.compress_older_than_days < 0
    else watermark_from_now(days=args.compress_older_than_days)
  )
  opts = CopyOptions(
    args.source_host,
    args.target_host,
    args.port,
    args.user,
    args.database,
    columns,
    dump_dir=args.dump_dir,
    force=args.force,
    verify=args.verify,
    compress_before=compress_before,
    slice_minutes=args.slice_minutes,
  )
  state = load_state(args.state_file)
  examine, same = (
    split_unchanged(selected, state, sigs)
    if args.changed_only
    else (list(selected), [])
  )
  summary = run_chunk_copies(
    examine,
    opts=opts,
    workers=args.workers,
    state=state,
    state_path=args.state_file,
    unchanged=len(same),
  )
  unchanged_rows = sum(int(state[c.regclass]["rows"]) for c in same)
  LOG.info(
    "done unchanged=%s skipped=%s copied=%s failed=%s verified_rows=%s "
    "force=%s verify=%s workers=%s",
    summary.unchanged,
    summary.skipped,
    summary.copied,
    summary.failed,
    summary.rows_total + unchanged_rows,
    args.force,
    args.verify,
    args.workers,
  )
  return 1 if summary.failed else 0


if __name__ == "__main__":
  sys.exit(main())
