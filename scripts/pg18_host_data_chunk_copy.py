#!/usr/bin/env python3
"""
Live logical COPY of Timescale ``host_data`` chunks from PG15 to PG18.

Do **not** restore ``_timescaledb_catalog`` from the source. Target must already
have an empty Django-migrated ``host_data`` hypertable. Watermark filtering is
best-effort while writers stay on PG15; freeze + recount is required before
cutover (see ``docs/OPERATOR_PG18_MIGRATION.md``).

Uses **psycopg** (already in the ``web`` image) for catalog queries and COPY
streaming — no PostgreSQL client binary is required on PATH. Chunks whose
source and target row counts already match are skipped unless ``--force``.
Up to ``--workers`` chunks copy concurrently (default 2).

Attributes:
  LOG: Module logger for chunk-copy progress and failures.
  _COUNT_ATTEMPTS: Retry count for source/target row-count queries.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import psycopg
from psycopg import Connection
from psycopg import errors as pg_errors

LOG = logging.getLogger("pg18_host_data_chunk_copy")
_COUNT_ATTEMPTS = 5


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
      str: ``schema.name`` suitable for ``COPY (SELECT * FROM …)``.

    Examples:
      >>> ChunkRow('_timescaledb_internal', '_hyper_1_1_chunk', datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), False).regclass
      '_timescaledb_internal._hyper_1_1_chunk'
    """
    return f"{self.chunk_schema}.{self.chunk_name}"


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
    >>> _parse_pg_timestamptz('2026-07-01 00:00:00+00').year
    2026
  """
  text = value.strip().replace(" ", "T")
  if text.endswith("+00") or text.endswith("-00"):
    text = text[:-3] + "+00:00"
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  # Handle "+00:00" already; also bare timestamps → assume UTC.
  if "+" not in text[10:] and "-" not in text[10:]:
    text = text + "+00:00"
  dt = datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


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
    >>> filter_chunks_by_watermark([], watermark=datetime(2026, 1, 1, tzinfo=timezone.utc))
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
    >>> watermark_from_now(days=3, now=datetime(2026, 9, 4, tzinfo=timezone.utc)).day
    1
  """
  base = now if now is not None else datetime.now(timezone.utc)
  if base.tzinfo is None:
    base = base.replace(tzinfo=timezone.utc)
  return base.astimezone(timezone.utc) - timedelta(days=days)


def build_delete_range_sql(chunk: ChunkRow) -> str:
  """
  Build SQL that deletes target rows in ``[range_start, range_end)`` for retries.

  Args:
    chunk (ChunkRow): Source chunk whose time bounds define the delete window.

  Returns:
    str: ``DELETE FROM host_data WHERE …`` statement.

  Examples:
    >>> 'DELETE FROM host_data' in build_delete_range_sql(ChunkRow('_timescaledb_internal', '_hyper_1_2_chunk', datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 11, tzinfo=timezone.utc), False))
    True
  """
  start = chunk.range_start.isoformat()
  end = chunk.range_end.isoformat()
  return (
      "DELETE FROM host_data "
      f"WHERE time >= TIMESTAMPTZ '{start}' AND time < TIMESTAMPTZ '{end}';"
  )


def build_copy_out_sql(chunk: ChunkRow) -> str:
  """
  Build ``COPY (SELECT * FROM <chunk>) TO STDOUT`` — never the parent hypertable.

  Args:
    chunk (ChunkRow): Source chunk to stream.

  Returns:
    str: ``COPY … TO STDOUT`` statement.

  Raises:
    ValueError: Raised when ``chunk`` names the empty parent ``host_data``.

  Examples:
    >>> build_copy_out_sql(ChunkRow('_timescaledb_internal', '_hyper_1_2_chunk', datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), False))
    'COPY (SELECT * FROM _timescaledb_internal._hyper_1_2_chunk) TO STDOUT'
  """
  if chunk.chunk_name == "host_data" and chunk.chunk_schema in {"public", ""}:
    raise ValueError("refusing to COPY parent hypertable host_data")
  return f"COPY (SELECT * FROM {chunk.regclass}) TO STDOUT"


def build_copy_in_sql() -> str:
  """
  Build ``COPY host_data FROM STDIN`` — Timescale routes into new chunks.

  Returns:
    str: ``COPY host_data FROM STDIN`` statement.

  Examples:
    >>> build_copy_in_sql()
    'COPY host_data FROM STDIN'
  """
  return "COPY host_data FROM STDIN"


def list_source_chunks_sql() -> str:
  """
  Return the catalog query for ``host_data`` chunks (excludes the empty parent).

  Returns:
    str: SQL selecting chunk_schema, chunk_name, range bounds, is_compressed.

  Examples:
    >>> 'hypertable_name' in list_source_chunks_sql()
    True
  """
  return """
SELECT chunk_schema, chunk_name, range_start, range_end, is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'host_data'
ORDER BY range_start;
""".strip()


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
    >>> connect_pg.__name__
    'connect_pg'
  """
  return psycopg.connect(
      host=host,
      port=port,
      user=user,
      dbname=database,
      password=os.environ.get("PGPASSWORD") or "",
      autocommit=True,
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
    >>> _as_utc_datetime(datetime(2026, 1, 1, tzinfo=timezone.utc)).year
    2026
  """
  if isinstance(value, datetime):
    dt = value
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
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
    >>> callable(fetch_source_chunks)
    True
  """
  with connect_pg(host=host, port=port, user=user, database=database) as conn:
    with conn.cursor() as cur:
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


def _is_retryable_count_error(exc: BaseException) -> bool:
  """
  Return True for transient count failures (timeout / PG18 I/O cancel).

  Args:
    exc (BaseException): Exception raised during ``count(*)``.

  Returns:
    bool: True when the error is safe to retry or treat as unsynced.

  Examples:
    >>> _is_retryable_count_error(RuntimeError('x'))
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
    sql (str): Count SQL (``SELECT count(*) …``).
    params (Sequence[object]): Bind parameters for ``sql``.
    label (str): Log label for retries (source/target).

  Returns:
    int: Row count, or ``-1`` when retryable cancels persist.

  Raises:
    Exception: Re-raised when the failure is not a retryable cancel.

  Examples:
    >>> _count_with_retry.__name__
    '_count_with_retry'
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
    >>> count_source_chunk_rows.__name__
    'count_source_chunk_rows'
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
    >>> count_target_range_rows.__name__
    'count_target_range_rows'
  """
  return _count_with_retry(
      conn,
      "SELECT count(*) FROM host_data WHERE time >= %s AND time < %s",
      (chunk.range_start, chunk.range_end),
      label=f"target {chunk.regclass}",
  )


def copy_one_chunk(
    chunk: ChunkRow,
    *,
    source_host: str,
    target_host: str,
    port: int,
    user: str,
    database: str,
    dump_dir: str | None,
    force: bool = False,
) -> str:
  """
  Skip when row counts match, else delete-range + COPY (optional zstd dump).

  Compares ``count(*)`` on the source chunk to ``count(*)`` on target
  ``host_data`` for the same time range. Matching non-negative counts skip
  the expensive delete/COPY (resume-friendly). Failed counts (``-1`` after
  retries) never skip. ``force=True`` always re-copies. Optional
  ``dump_dir`` still shells out to the ``zstd`` CLI when present.

  Args:
    chunk (ChunkRow): Source chunk to copy.
    source_host (str): PG15 hostname.
    target_host (str): PG18 hostname.
    port (int): Shared Postgres port.
    user (str): Role name.
    database (str): Database name.
    dump_dir (str | None): Optional directory for ``chunk_*.pgcopy.zst``.
    force (bool): When True, ignore matching counts and re-copy.

  Returns:
    str: ``"skipped"`` when counts already match, else ``"copied"``.

  Raises:
    RuntimeError: Raised when zstd dump/restore stages fail.
    ValueError: Raised when ``chunk`` names the parent ``host_data`` relation.

  Examples:
    >>> callable(copy_one_chunk)
    True
  """
  del_sql = build_delete_range_sql(chunk)
  out_sql = build_copy_out_sql(chunk)
  in_sql = build_copy_in_sql()

  with connect_pg(
      host=source_host, port=port, user=user, database=database
  ) as src, connect_pg(
      host=target_host, port=port, user=user, database=database
  ) as tgt:
    src_n = count_source_chunk_rows(src, chunk)
    tgt_n = count_target_range_rows(tgt, chunk)
    if not force and chunk_row_counts_match(src_n, tgt_n):
      LOG.info(
          "skip chunk=%s range=[%s,%s) rows=%s (already synced)",
          chunk.regclass,
          chunk.range_start.isoformat(),
          chunk.range_end.isoformat(),
          src_n,
      )
      return "skipped"

    LOG.info(
        "copy chunk=%s range=[%s,%s) compressed=%s src_rows=%s tgt_rows=%s",
        chunk.regclass,
        chunk.range_start.isoformat(),
        chunk.range_end.isoformat(),
        chunk.is_compressed,
        src_n,
        tgt_n,
    )

    with tgt.cursor() as tcur:
      tcur.execute("SET statement_timeout = 0")
      tcur.execute(del_sql)

    if dump_dir:
      path = Path(dump_dir) / f"chunk_{chunk.chunk_name}.pgcopy.zst"
      path.parent.mkdir(parents=True, exist_ok=True)
      with path.open("wb") as out_f, src.cursor() as scur:
        scur.execute("SET statement_timeout = 0")
        zstd = subprocess.Popen(
            ["zstd", "-T0", "-19"],
            stdin=subprocess.PIPE,
            stdout=out_f,
        )
        assert zstd.stdin is not None
        with scur.copy(out_sql) as copy_out:
          for data in copy_out:
            zstd.stdin.write(bytes(data))
        zstd.stdin.close()
        z_rc = zstd.wait()
        if z_rc != 0:
          raise RuntimeError(
              f"dump failed chunk={chunk.regclass} zstd={z_rc}"
          )
      with path.open("rb") as in_f, tgt.cursor() as tcur:
        tcur.execute("SET statement_timeout = 0")
        zstd_d = subprocess.Popen(
            ["zstd", "-dc"],
            stdin=in_f,
            stdout=subprocess.PIPE,
        )
        assert zstd_d.stdout is not None
        with tcur.copy(in_sql) as copy_in:
          while True:
            buf = zstd_d.stdout.read(1024 * 1024)
            if not buf:
              break
            copy_in.write(buf)
        d_rc = zstd_d.wait()
        if d_rc != 0:
          raise RuntimeError(
              f"restore-from-dump failed chunk={chunk.regclass} zstd={d_rc}"
          )
      return "copied"

    with src.cursor() as scur, tgt.cursor() as tcur:
      scur.execute("SET statement_timeout = 0")
      tcur.execute("SET statement_timeout = 0")
      with scur.copy(out_sql) as copy_out, tcur.copy(in_sql) as copy_in:
        for data in copy_out:
          copy_in.write(data)
  return "copied"


def run_chunk_copies(
    chunks: Sequence[ChunkRow],
    *,
    source_host: str,
    target_host: str,
    port: int,
    user: str,
    database: str,
    dump_dir: str | None,
    force: bool,
    workers: int,
) -> tuple[int, int]:
  """
  Copy or skip selected chunks with up to ``workers`` concurrent threads.

  Each worker opens its own source/target connections. Disjoint chunk time
  ranges are safe to run in parallel; raise if ``workers`` is less than 1.

  Args:
    chunks (Sequence[ChunkRow]): Watermark-selected chunks to process.
    source_host (str): PG15 hostname.
    target_host (str): PG18 hostname.
    port (int): Shared Postgres port.
    user (str): Role name.
    database (str): Database name.
    dump_dir (str | None): Optional zstd dump directory.
    force (bool): Passed through to :func:`copy_one_chunk`.
    workers (int): Max concurrent chunk copies (``1`` = serial).

  Returns:
    tuple[int, int]: ``(skipped_count, copied_count)``.

  Raises:
    ValueError: Raised when ``workers`` is less than 1.
    RuntimeError: Raised when a worker's copy/dump stage fails.
    ValueError: Raised when a chunk names the parent ``host_data`` relation.

  Examples:
    >>> run_chunk_copies([], source_host='db', target_host='db18', port=5432, user='u', database='d', dump_dir=None, force=False, workers=2)
    (0, 0)
  """
  if workers < 1:
    raise ValueError("workers must be >= 1")
  if not chunks:
    return 0, 0

  skipped = 0
  copied = 0
  with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [
        pool.submit(
            copy_one_chunk,
            chunk,
            source_host=source_host,
            target_host=target_host,
            port=port,
            user=user,
            database=database,
            dump_dir=dump_dir,
            force=force,
        )
        for chunk in chunks
    ]
    for fut in as_completed(futures):
      outcome = fut.result()
      if outcome == "skipped":
        skipped += 1
      else:
        copied += 1
  return skipped, copied


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: list or copy watermarked ``host_data`` chunks.

  Args:
    argv (Sequence[str] | None): Optional argv override (tests); default
      ``None`` reads ``sys.argv``.

  Returns:
    int: Process exit code (``0`` on success).

  Examples:
    >>> callable(main)
    True
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--source-host", default="db", help="PG15 compose hostname")
  parser.add_argument("--target-host", default="db18", help="PG18 compose hostname")
  parser.add_argument("--port", type=int, default=5432)
  parser.add_argument("--user", default="hpcperfstats")
  parser.add_argument("--database", default="hpcperfstats")
  parser.add_argument(
      "--watermark-days",
      type=float,
      default=3.0,
      help="Copy only chunks with range_end < now() - N days (default 3)",
  )
  parser.add_argument(
      "--dump-dir",
      default=None,
      help="Optional directory for chunk_*.pgcopy.zst audit/resume files",
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
  parser.add_argument("-v", "--verbose", action="store_true")
  args = parser.parse_args(list(argv) if argv is not None else None)

  logging.basicConfig(
      level=logging.DEBUG if args.verbose else logging.INFO,
      format="%(asctime)s %(levelname)s %(message)s",
  )

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

  skipped, copied = run_chunk_copies(
      selected,
      source_host=args.source_host,
      target_host=args.target_host,
      port=args.port,
      user=args.user,
      database=args.database,
      dump_dir=args.dump_dir,
      force=args.force,
      workers=args.workers,
  )
  LOG.info(
      "done skipped=%s copied=%s force=%s workers=%s",
      skipped,
      copied,
      args.force,
      args.workers,
  )
  return 0


if __name__ == "__main__":
  sys.exit(main())
