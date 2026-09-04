#!/usr/bin/env python3
"""
Live logical COPY of Timescale ``host_data`` chunks from PG15 to PG18.

Do **not** restore ``_timescaledb_catalog`` from the source. Target must already
have an empty Django-migrated ``host_data`` hypertable. Watermark filtering is
best-effort while writers stay on PG15; freeze + recount is required before
cutover (see ``docs/OPERATOR_PG18_MIGRATION.md``).

Attributes:
  LOG: Module logger for chunk-copy progress and failures.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

LOG = logging.getLogger("pg18_host_data_chunk_copy")


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
  Parse ``psql -At`` TSV lines into :class:`ChunkRow` values.

  Expected columns: chunk_schema, chunk_name, range_start, range_end,
  is_compressed (t/f).

  Args:
    lines (Iterable[str]): Raw TSV lines from ``psql``.

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
    value (str): Timestamp text from ``psql`` (space or ``T`` separator).

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


def _psql_env_cmd(
    *,
    host: str,
    port: int,
    user: str,
    database: str,
    extra: Sequence[str],
) -> list[str]:
  """
  Build a ``psql`` argv list for the given connection and extra flags.

  Args:
    host (str): Postgres hostname.
    port (int): Postgres port.
    user (str): Role name.
    database (str): Database name.
    extra (Sequence[str]): Additional argv tokens (``-c``, ``-At``, …).

  Returns:
    list[str]: Complete ``psql`` command argv.

  Examples:
    >>> _psql_env_cmd(host='db', port=5432, user='u', database='d', extra=['-c', 'SELECT 1'])[0]
    'psql'
  """
  return [
      "psql",
      "-h",
      host,
      "-p",
      str(port),
      "-U",
      user,
      "-d",
      database,
      "-v",
      "ON_ERROR_STOP=1",
      *extra,
  ]


def fetch_source_chunks(
    *,
    host: str,
    port: int,
    user: str,
    database: str,
) -> list[ChunkRow]:
  """
  Run the catalog query on the source and parse TSV rows.

  Args:
    host (str): Source Postgres hostname.
    port (int): Source port.
    user (str): Role name.
    database (str): Database name.

  Returns:
    list[ChunkRow]: Parsed source chunks.

  Raises:
    subprocess.CalledProcessError: Raised when ``psql`` exits non-zero.

  Examples:
    >>> fetch_source_chunks  # doctest: +SKIP
  """
  cmd = _psql_env_cmd(
      host=host,
      port=port,
      user=user,
      database=database,
      extra=["-At", "-F", "|", "-c", list_source_chunks_sql()],
  )
  proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
  return parse_chunk_tsv(proc.stdout.splitlines())


def copy_one_chunk(
    chunk: ChunkRow,
    *,
    source_host: str,
    target_host: str,
    port: int,
    user: str,
    database: str,
    dump_dir: str | None,
) -> None:
  """
  Delete-range on target then pipe COPY out → COPY in (optional zstd dump).

  Args:
    chunk (ChunkRow): Source chunk to copy.
    source_host (str): PG15 hostname.
    target_host (str): PG18 hostname.
    port (int): Shared Postgres port.
    user (str): Role name.
    database (str): Database name.
    dump_dir (str | None): Optional directory for ``chunk_*.pgcopy.zst``.

  Returns:
    None: This function does not return a value.

  Raises:
    RuntimeError: Raised when pipe, zstd, or ``psql`` stages fail.
    subprocess.CalledProcessError: Raised when the delete-range ``psql`` fails.

  Examples:
    >>> copy_one_chunk  # doctest: +SKIP
  """
  del_sql = build_delete_range_sql(chunk)
  out_sql = build_copy_out_sql(chunk)
  in_sql = build_copy_in_sql()

  timeout_sql = "SET statement_timeout = 0;"
  src = _psql_env_cmd(
      host=source_host,
      port=port,
      user=user,
      database=database,
      extra=["-c", timeout_sql, "-c", out_sql],
  )
  tgt_del = _psql_env_cmd(
      host=target_host,
      port=port,
      user=user,
      database=database,
      extra=["-c", timeout_sql, "-c", del_sql],
  )
  tgt_in = _psql_env_cmd(
      host=target_host,
      port=port,
      user=user,
      database=database,
      extra=["-c", timeout_sql, "-c", in_sql],
  )

  LOG.info(
      "copy chunk=%s range=[%s,%s) compressed=%s",
      chunk.regclass,
      chunk.range_start.isoformat(),
      chunk.range_end.isoformat(),
      chunk.is_compressed,
  )
  subprocess.run(tgt_del, check=True)

  if dump_dir:
    from pathlib import Path

    path = Path(dump_dir) / f"chunk_{chunk.chunk_name}.pgcopy.zst"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as out_f:
      src_p = subprocess.Popen(src, stdout=subprocess.PIPE)
      assert src_p.stdout is not None
      zstd = subprocess.Popen(
          ["zstd", "-T0", "-19"],
          stdin=src_p.stdout,
          stdout=out_f,
      )
      src_p.stdout.close()
      z_rc = zstd.wait()
      s_rc = src_p.wait()
      if s_rc != 0 or z_rc != 0:
        raise RuntimeError(f"dump failed chunk={chunk.regclass} src={s_rc} zstd={z_rc}")
    with path.open("rb") as in_f:
      zstd_d = subprocess.Popen(
          ["zstd", "-dc"],
          stdin=in_f,
          stdout=subprocess.PIPE,
      )
      assert zstd_d.stdout is not None
      tgt_p = subprocess.Popen(tgt_in, stdin=zstd_d.stdout)
      zstd_d.stdout.close()
      t_rc = tgt_p.wait()
      d_rc = zstd_d.wait()
      if t_rc != 0 or d_rc != 0:
        raise RuntimeError(
            f"restore-from-dump failed chunk={chunk.regclass} tgt={t_rc} zstd={d_rc}"
        )
    return

  src_p = subprocess.Popen(src, stdout=subprocess.PIPE)
  assert src_p.stdout is not None
  tgt_p = subprocess.Popen(tgt_in, stdin=src_p.stdout)
  src_p.stdout.close()
  t_rc = tgt_p.wait()
  s_rc = src_p.wait()
  if s_rc != 0 or t_rc != 0:
    raise RuntimeError(f"pipe copy failed chunk={chunk.regclass} src={s_rc} tgt={t_rc}")


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: list or copy watermarked ``host_data`` chunks.

  Args:
    argv (Sequence[str] | None): Optional argv override (tests); default ``None``.

  Returns:
    int: Process exit code (``0`` on success).

  Examples:
    >>> main(['--help'])  # doctest: +SKIP
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
      "source_chunks=%s selected=%s watermark=%s",
      len(chunks),
      len(selected),
      wm.isoformat(),
  )
  for c in selected:
    print(
        f"{c.regclass}\t{c.range_start.isoformat()}\t{c.range_end.isoformat()}\t"
        f"compressed={c.is_compressed}"
    )
  if args.list_only:
    return 0

  for chunk in selected:
    copy_one_chunk(
        chunk,
        source_host=args.source_host,
        target_host=args.target_host,
        port=args.port,
        user=args.user,
        database=args.database,
        dump_dir=args.dump_dir,
    )
  return 0


if __name__ == "__main__":
  sys.exit(main())
