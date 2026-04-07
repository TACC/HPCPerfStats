"""Structured timings and PostgreSQL introspection for stress runs."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from django.db import connection


def _json_safe(obj: Any) -> Any:
  if isinstance(obj, dict):
    return {k: _json_safe(v) for k, v in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [_json_safe(x) for x in obj]
  if isinstance(obj, float):
    return float(obj)
  try:
    if hasattr(obj, "item"):
      return obj.item()
  except Exception:
    pass
  return obj


class StressProfiler:
  """Record phase timings, optional PG stats, and write ``stress_report_*.json``."""

  def __init__(self, report_dir: str | Path | None = None) -> None:
    raw = report_dir or os.environ.get("HPCPERFSTATS_STRESS_REPORT_DIR", "").strip()
    self.report_dir = Path(raw) if raw else Path("artifacts") / "stress"
    self.report_dir.mkdir(parents=True, exist_ok=True)
    self.phases: list[dict[str, Any]] = []
    self.counts: dict[str, Any] = {}
    self.metadata: dict[str, Any] = {}
    self.pg_snapshots: list[dict[str, Any]] = []

  def set_metadata(self, **kwargs: Any) -> None:
    self.metadata.update(kwargs)

  def set_counts(self, **kwargs: Any) -> None:
    self.counts.update(kwargs)

  def record_phase(self, name: str, seconds: float, **extra: Any) -> None:
    row: dict[str, Any] = {"name": name, "seconds": round(seconds, 6)}
    row.update(extra)
    self.phases.append(row)

  def phase(self, name: str, fn: Callable[[], Any] | None = None) -> Any:
    t0 = time.perf_counter()
    out = None
    try:
      if fn is not None:
        out = fn()
    finally:
      self.record_phase(name, time.perf_counter() - t0)
    return out

  def pg_relation_sizes(self) -> dict[str, Any] | None:
    if connection.vendor != "postgresql":
      return None
    out: dict[str, Any] = {}
    with connection.cursor() as cursor:
      for rel in ("host_data", "metrics_data", "job_plot_artifact"):
        try:
          cursor.execute(
              "SELECT pg_total_relation_size(%s::regclass)::bigint",
              [rel],
          )
          row = cursor.fetchone()
          out[rel] = int(row[0]) if row and row[0] is not None else None
        except Exception as exc:
          out[rel] = None
          out[rel + "_error"] = str(exc)
    return out

  def pg_stat_user_tables_rows(self) -> list[dict[str, Any]] | None:
    if connection.vendor != "postgresql":
      return None
    names = ("host_data", "metrics_data", "job_plot_artifact")
    with connection.cursor() as cursor:
      cursor.execute(
          """
          SELECT relname, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum
          FROM pg_stat_user_tables
          WHERE relname = ANY(%s)
          """,
          [list(names)],
      )
      cols = [c[0] for c in cursor.description]
      return [dict(zip(cols, r)) for r in cursor.fetchall()]

  def pg_timescale_host_data_chunk_stats(self) -> dict[str, Any] | None:
    """Best-effort chunk count + summed chunk sizes when TimescaleDB is present."""
    if connection.vendor != "postgresql":
      return None
    out: dict[str, Any] = {"timescale_chunks_available": False}
    with connection.cursor() as cursor:
      try:
        cursor.execute(
            """
            SELECT COUNT(*)::bigint
            FROM timescaledb_information.chunks
            WHERE hypertable_schema = ANY (current_schemas(true))
              AND hypertable_name = 'host_data'
            """
        )
        row = cursor.fetchone()
        out["host_data_chunk_count"] = (
            int(row[0]) if row and row[0] is not None else None
        )
        cursor.execute(
            """
            SELECT COALESCE(SUM(
              pg_total_relation_size(
                format('%I.%I', chunk_schema, chunk_name)::regclass
              )
            ), 0)::bigint
            FROM timescaledb_information.chunks
            WHERE hypertable_schema = ANY (current_schemas(true))
              AND hypertable_name = 'host_data'
            """
        )
        row2 = cursor.fetchone()
        out["host_data_chunks_total_bytes"] = (
            int(row2[0]) if row2 and row2[0] is not None else None
        )
        out["timescale_chunks_available"] = True
      except Exception as exc:
        out["error"] = str(exc)
    return out

  def maybe_explain_chunked_host_in(
      self,
      jid: str,
      sample_hosts: list[str],
      t_start,
      t_end,
  ) -> None:
    if os.environ.get("HPCPERFSTATS_STRESS_EXPLAIN", "").strip().lower() not in (
        "1", "true", "yes",
    ):
      return
    if connection.vendor != "postgresql" or len(sample_hosts) < 1:
      return
    ops = connection.ops
    tbl = ops.quote_name("host_data")
    col_host = ops.quote_name("host")
    col_time = ops.quote_name("time")
    col_jid = ops.quote_name("jid")
    sql = (
        "EXPLAIN (FORMAT JSON) SELECT COUNT(*) FROM {tbl} d "
        "WHERE d.{h} = ANY(%s::text[]) AND d.{t} >= %s::timestamptz "
        "AND d.{t} <= %s::timestamptz AND d.{j} = %s"
    ).format(tbl=tbl, h=col_host, t=col_time, j=col_jid)
    hosts = sample_hosts[: min(64, len(sample_hosts))]
    with connection.cursor() as cursor:
      cursor.execute(sql, [hosts, t_start, t_end, jid])
      rows = cursor.fetchall()
    self.pg_snapshots.append({
        "kind": "explain_json_host_in",
        "plan": rows[0][0] if rows else None,
    })

  def snapshot_pg(self, label: str) -> None:
    snap: dict[str, Any] = {"label": label}
    snap["relation_sizes"] = self.pg_relation_sizes()
    snap["stat_user_tables"] = self.pg_stat_user_tables_rows()
    snap["timescale_host_data"] = self.pg_timescale_host_data_chunk_stats()
    self.pg_snapshots.append(snap)

  def build_report(self) -> dict[str, Any]:
    return _json_safe({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": self.metadata,
        "counts": self.counts,
        "phases": self.phases,
        "pg_snapshots": self.pg_snapshots,
    })

  def write_report(self) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = self.report_dir / "stress_report_{}.json".format(stamp)
    path.write_text(
        json.dumps(self.build_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
