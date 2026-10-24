"""Byte-identical Postgres COPY bodies: Codon kernel vs CPython parser.

Rows are ordered by the COPY conflict key before encoding so the comparison
locks cell values, not emit order.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_host_data_insert import (
  host_rows_to_copy_bytes,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  HOST_PROC_KEYS,
  IncrementalStatsParser,
  compute_deltas_and_arc,
  stats_payload_to_records,
)
from hpcperfstats.dbload.lib.sync_timedb_proc_data_insert import (
  proc_rows_to_copy_bytes,
)

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)

_HOST_SORT = ("host", "type", "dev", "event", "unit", "time")
_PROC_SORT = ("jid", "host", "proc")


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_copy_bytes_under_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _corpus() -> list[str]:
  return [
    "!cpu user,W=64,U=1",
    "!host_cpu_hw dcgm_cpu_power_util_w,W=64,U=1",
    (
      "!nvidia_gpu gpu_util,W=64,U=1 temperature,W=64,U=1 "
      "module_power_usage,W=64,U=1 clocks_event_reasons,W=64,U=1"
    ),
    "!host_mem_job rss,W=64,U=kB",
    "!host_proc vm_size,W=64,U=kB vm_stk,W=64,U=kB threads,W=64,U=1",
    "1000.0 J1 hostA",
    "cpu 0 @full 10.0",
    "cpu 1 @full 20.0",
    "host_cpu_hw 0 @full 100.0",
    "host_cpu_hw 1 @full 100.5",
    "host_cpu_hw 2 @full 500.0",
    "nvidia_gpu 0 @full 1.0 60.0 5.0 3.0",
    "nvidia_gpu 1 @full 2.0 70.0 7.0 5.0",
    "host_mem_job 0 @full 42.0",
    "host_proc bash/1 @full 100 50 2",
    "host_proc bash/2 @full 80 10 4",
    "1001.0 J1 hostA",
    "cpu 0 @full 15.0",
    "cpu 1 @full 25.0",
    "host_cpu_hw 0 @full 150.0",
    "nvidia_gpu 0 @full 1.5 65.0 6.0 2.0",
    "host_mem_job 0 @full 50.0",
    "host_proc bash/3 @full 90 70 1",
  ]


def _first_mismatch(left: bytes, right: bytes) -> str:
  a = left.decode().splitlines()
  b = right.decode().splitlines()
  for i, (x, y) in enumerate(zip(a, b, strict=False)):
    if x != y:
      return f"line {i}\nCPYTHON {x}\nCODON   {y}"
  return f"line count cpython={len(a)} codon={len(b)}"


def test_codon_copy_bytes_match_cpython() -> None:
  mod = _load_feed_collapse()
  lines = _corpus()
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, procs = parser.finish()
  records = stats_payload_to_records(stats)
  cols = [
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "value",
    "wid",
    "mult",
    "unit",
  ]
  frame = (
    pd.DataFrame(records, columns=cols)
    if records
    else pd.DataFrame(columns=cols)
  )
  finished = compute_deltas_and_arc(frame)
  finished = finished.sort_values(list(_HOST_SORT)).reset_index(drop=True)
  py_host = host_rows_to_copy_bytes(finished)

  host_cols, proc, _cr, _ca, _marks = mod.feed_delta_collapse_arc(lines, {}, {})
  names = (
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "unit",
    "value",
    "delta",
    "arc",
  )
  soa = dict(zip(names, host_cols, strict=True))
  order = sorted(
    range(len(soa["time"])),
    key=lambda i: tuple(
      str(soa[k][i] if soa[k][i] is not None else "") for k in _HOST_SORT
    ),
  )
  soa_s = {k: [soa[k][i] for i in order] for k in names}
  cd_host = host_rows_to_copy_bytes(soa_s)
  assert py_host == cd_host, _first_mismatch(py_host, cd_host)

  py_proc_rows = sorted(
    procs,
    key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT),
  )
  py_proc = proc_rows_to_copy_bytes(py_proc_rows)

  jids, hosts, procs_cd, fields, devices = proc
  cd_rows = []
  for i in range(len(jids)):
    row = {
      "jid": jids[i],
      "host": hosts[i],
      "proc": procs_cd[i],
      "device": devices[i],
    }
    fld = fields[i]
    for metric in HOST_PROC_KEYS:
      row[metric] = fld.get(metric)
    cd_rows.append(row)
  cd_rows.sort(key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT))
  cd_proc = proc_rows_to_copy_bytes(cd_rows)
  assert py_proc == cd_proc, _first_mismatch(py_proc, cd_proc)
  assert b"bash/3" in py_proc
  assert b"\t90\t" in py_proc


def _marks_corpus() -> list[str]:
  return [
    "!cpu user,W=64,U=1",
    "!host_proc vm_size,W=64,U=kB threads,W=64,U=1",
    "%jobs jids=9,10",
    "%affinity jid=9 cpus=0-1 gpus=- cg=sched",
    "1710000001.0 9 hosta",
    "cpu 0 @full 1.0",
    "cpu 1 @full 2.0",
    "host_proc bash/1 @full 20 1",
    "1710000002.0 9 hosta",
    "cpu 0 @full 3.0",
  ]


def _sorted_copy_bytes(lines: list[str]) -> tuple[bytes, bytes, bytes, bytes]:
  mod = _load_feed_collapse()
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, procs = parser.finish()
  records = stats_payload_to_records(stats)
  cols = [
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "value",
    "wid",
    "mult",
    "unit",
  ]
  frame = (
    pd.DataFrame(records, columns=cols)
    if records
    else pd.DataFrame(columns=cols)
  )
  finished = compute_deltas_and_arc(frame)
  finished = finished.sort_values(list(_HOST_SORT)).reset_index(drop=True)
  py_host = host_rows_to_copy_bytes(finished)
  host_cols, proc, _cr, _ca, _marks = mod.feed_delta_collapse_arc(lines, {}, {})
  names = (
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "unit",
    "value",
    "delta",
    "arc",
  )
  soa = dict(zip(names, host_cols, strict=True))
  order = sorted(
    range(len(soa["time"])),
    key=lambda i: tuple(
      str(soa[k][i] if soa[k][i] is not None else "") for k in _HOST_SORT
    ),
  )
  soa_s = {k: [soa[k][i] for i in order] for k in names}
  cd_host = host_rows_to_copy_bytes(soa_s)
  py_proc_rows = sorted(
    procs,
    key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT),
  )
  py_proc = proc_rows_to_copy_bytes(py_proc_rows)
  jids, hosts, procs_cd, fields, devices = proc
  cd_rows = []
  for i in range(len(jids)):
    row = {
      "jid": jids[i],
      "host": hosts[i],
      "proc": procs_cd[i],
      "device": devices[i],
    }
    fld = fields[i]
    for metric in HOST_PROC_KEYS:
      row[metric] = fld.get(metric)
    cd_rows.append(row)
  cd_rows.sort(key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT))
  cd_proc = proc_rows_to_copy_bytes(cd_rows)
  return py_host, cd_host, py_proc, cd_proc


def test_codon_copy_bytes_match_cpython_with_marks() -> None:
  lines = _marks_corpus()
  py_host, cd_host, py_proc, cd_proc = _sorted_copy_bytes(lines)
  assert py_host == cd_host, _first_mismatch(py_host, cd_host)
  assert py_proc == cd_proc, _first_mismatch(py_proc, cd_proc)
  assert b"j:9" in py_host
  mod = _load_feed_collapse()
  _c, _p, _cr, _ca, codon_marks = mod.feed_delta_collapse_arc(lines, {}, {})
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  ready = parser.take_ready_marks()
  codon_aff = next(m for m in codon_marks if m["kind"] == "affinity")
  py_aff = next(m for m in ready if m.get("kind") == "affinity")
  assert codon_aff["jid"] == py_aff["jid"] == "9"
  assert codon_aff["host"] == py_aff["host"]
  assert float(codon_aff["time"]) == float(py_aff["time"])
  codon_jobs = next(m for m in codon_marks if m["kind"] == "jobs")
  py_jobs = next(m for m in ready if m.get("kind") == "jobs")

  def _jid_set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple)):
      return {str(item) for item in value}
    return {part for part in str(value).split(",") if part}

  assert (
    _jid_set(codon_jobs["jids"])
    == _jid_set(py_jobs.get("jids"))
    == {
      "9",
      "10",
    }
  )


def _insert_sql_bytes(model: Any, fields: list[Any], obj: Any) -> bytes:
  from django.db.models.sql.subqueries import InsertQuery

  query = InsertQuery(model)
  query.insert_values(fields, [obj])
  compiler = query.get_compiler(using="default")
  compiled = compiler.as_sql()
  sql, params = compiled[0]
  return (sql + "\n" + repr(tuple(params))).encode()


def test_codon_insert_sql_bytes_match_cpython_with_marks() -> None:
  from hpcperfstats.dbload.lib.io_helpers import (
    host_data_instance_from_stats_row,
  )
  from hpcperfstats.dbload.lib.sync_timedb_host_data_insert import (
    HOST_DATA_COPY_COLUMNS,
  )
  from hpcperfstats.dbload.sync_timedb import _proc_data_row_kwargs
  from hpcperfstats.site.lib.machine.models import proc_data

  lines = _marks_corpus()
  mod = _load_feed_collapse()
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, procs = parser.finish()
  records = stats_payload_to_records(stats)
  cols = [
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "value",
    "wid",
    "mult",
    "unit",
  ]
  frame = pd.DataFrame(records, columns=cols)
  finished = compute_deltas_and_arc(frame)
  finished = finished.sort_values(list(_HOST_SORT)).reset_index(drop=True)
  host_cols, proc, _cr, _ca, _marks = mod.feed_delta_collapse_arc(lines, {}, {})
  names = (
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "unit",
    "value",
    "delta",
    "arc",
  )
  soa = dict(zip(names, host_cols, strict=True))
  order = sorted(
    range(len(soa["time"])),
    key=lambda i: tuple(
      str(soa[k][i] if soa[k][i] is not None else "") for k in _HOST_SORT
    ),
  )
  soa_s = {k: [soa[k][i] for i in order] for k in names}
  codon_frame = pd.DataFrame(soa_s)
  py_sql = b""
  cd_sql = b""
  py_rows = list(finished.itertuples(index=False))
  cd_rows = list(codon_frame.itertuples(index=False))
  assert len(py_rows) == len(cd_rows)
  for py_row, cd_row in zip(py_rows, cd_rows, strict=True):
    py_obj = host_data_instance_from_stats_row(py_row)
    cd_obj = host_data_instance_from_stats_row(cd_row)
    fields = [py_obj._meta.get_field(name) for name in HOST_DATA_COPY_COLUMNS]
    py_sql += _insert_sql_bytes(py_obj.__class__, fields, py_obj) + b"\n"
    cd_sql += _insert_sql_bytes(cd_obj.__class__, fields, cd_obj) + b"\n"
  assert py_sql == cd_sql
  assert b"j:9" in py_sql
  jids, hosts, procs_cd, fields_cd, devices = proc
  proc_fields = [
    proc_data._meta.get_field(name)
    for name in ("jid", "host", "proc", "device", *HOST_PROC_KEYS)
  ]
  py_proc_rows = sorted(
    procs,
    key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT),
  )
  cd_proc_rows = []
  for i in range(len(jids)):
    row = {
      "jid": jids[i],
      "host": hosts[i],
      "proc": procs_cd[i],
      "device": devices[i],
    }
    fld = fields_cd[i]
    for metric in HOST_PROC_KEYS:
      row[metric] = fld.get(metric)
    cd_proc_rows.append(row)
  cd_proc_rows.sort(
    key=lambda row: tuple(str(row.get(k) or "") for k in _PROC_SORT),
  )
  py_proc_sql = b""
  cd_proc_sql = b""
  for py_row, cd_row in zip(py_proc_rows, cd_proc_rows, strict=True):
    py_obj = proc_data(**_proc_data_row_kwargs(_row_ns(py_row)))
    cd_obj = proc_data(**_proc_data_row_kwargs(_row_ns(cd_row)))
    py_proc_sql += _insert_sql_bytes(proc_data, proc_fields, py_obj) + b"\n"
    cd_proc_sql += _insert_sql_bytes(proc_data, proc_fields, cd_obj) + b"\n"
  assert py_proc_sql == cd_proc_sql


def _row_ns(row: dict) -> Any:
  from types import SimpleNamespace

  return SimpleNamespace(**row)
