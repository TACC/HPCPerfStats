"""Keep-``dev`` type sets (D34) and ``host_power_socket`` ingest (D38).

Locks the canonical GPU / keep-``dev`` collapse sets in
``monitor_naming.canonical`` against their literal Codon mirrors in
``dbload/codon/feed_collapse.py`` (the Codon compile unit cannot import
Python modules), and checks every collapse site keeps per-socket ``dev``:
the CPython collapse split, the Codon ``collapse_full_cols`` port and the
per-core repair mask. Shared-node stamping still matches GPUs only.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pandas as pd

from hpcperfstats.dbload.lib import (
  monitor_mark_side_ingest as mark_side,
  sync_timedb_parsing as parsing,
)
from hpcperfstats.dbload.lib.host_data_percore_collapse import (
  KEEP_DEV_TYPES,
  percore_mask,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  GPU_STATS_TYPES,
  HOST_POWER_SOCKET_TYPE,
  KEEP_DEV_COLLAPSE_TYPES,
)

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)

_SOCKET_SCHEMA = (
  "!host_power_socket cpu_power_w,U=W socket_power_w,U=W "
  "sysio_power_w,U=W module_power_w,U=W"
)


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_keep_dev_parity", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _gb200_equal_socket_lines() -> list[str]:
  """Two GB200 sockets with equal watts (CPU 45, Grace 45, SysIO 6, no module)."""
  return [
    "!cpu user,W=64,U=1",
    _SOCKET_SCHEMA,
    "1000.0 J1 hostA",
    "cpu 0 @full 10.0",
    "cpu 1 @full 20.0",
    "host_power_socket 0 @full 45 45 6 0",
    "host_power_socket 1 @full 45 45 6 0",
    "1001.0 J1 hostA",
    "cpu 0 @full 15.0",
    "cpu 1 @full 25.0",
    "host_power_socket 0 @full 45 45 6 0",
    "host_power_socket 1 @full 45 45 6 0",
  ]


def _shared_tick_lines() -> list[str]:
  """Marked two-jid tick: socket power only in the node-wide ``-`` payload."""
  return [
    "!cpu user,W=64,U=1",
    _SOCKET_SCHEMA,
    "%jobs jids=41,42",
    "%affinity jid=41 cpus=0 gpus=-",
    "1000.0 41 hostA",
    "cpu 0 @full 10.0",
    "%jobs jids=41,42",
    "%affinity jid=42 cpus=1 gpus=-",
    "1000.0 42 hostA",
    "cpu 1 @full 20.0",
    "1000.0 - hostA",
    "host_power_socket 0 @full 44 88 6 0",
    "host_power_socket 1 @full 46 90 6 0",
  ]


def _cpython_rows(lines: list[str]) -> pd.DataFrame:
  parser = parsing.IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, _procs = parser.finish()
  records = parsing.stats_payload_to_records(stats)
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
  df = pd.DataFrame(records, columns=cols)
  return parsing.compute_deltas_and_arc(df)


def _codon_rows(lines: list[str]) -> pd.DataFrame:
  mod = _load_feed_collapse()
  cols, _proc, _cr, _ca, _marks, _np = mod.feed_delta_collapse_arc(
    list(lines), {}, {}
  )
  times, hosts, jids, types, devs, events, _units, values, deltas, arcs = cols
  return pd.DataFrame(
    {
      "time": times,
      "host": hosts,
      "jid": jids,
      "type": types,
      "dev": devs,
      "event": events,
      "value": values,
      "delta": deltas,
      "arc": arcs,
    }
  )


def _socket_rows(df: pd.DataFrame, event: str) -> pd.DataFrame:
  sel = (df["type"] == HOST_POWER_SOCKET_TYPE) & (df["event"] == event)
  return df.loc[sel].sort_values(["time", "dev"]).reset_index(drop=True)


def test_codon_literal_mirrors_match_canonical_sets() -> None:
  mod = _load_feed_collapse()
  assert set(mod.GPU_TYPES) == set(GPU_STATS_TYPES)
  assert len(mod.GPU_TYPES) == len(set(mod.GPU_TYPES))
  assert set(mod.KEEP_DEV_TYPES) == set(KEEP_DEV_COLLAPSE_TYPES)
  assert len(mod.KEEP_DEV_TYPES) == len(set(mod.KEEP_DEV_TYPES))


def test_keep_dev_set_is_gpu_set_plus_host_power_socket() -> None:
  assert GPU_STATS_TYPES | {HOST_POWER_SOCKET_TYPE} == KEEP_DEV_COLLAPSE_TYPES
  assert set(KEEP_DEV_TYPES) == set(KEEP_DEV_COLLAPSE_TYPES)


def test_every_python_gpu_copy_is_the_canonical_set() -> None:
  from hpcperfstats.analysis.metrics.lib import job_host_data_scope
  from hpcperfstats.analysis.metrics.lib.columnar import job_table

  assert parsing._GPU_STATS_TYPES is GPU_STATS_TYPES
  assert mark_side._GPU_TYPES is GPU_STATS_TYPES
  assert job_table._GPU_TYPES is GPU_STATS_TYPES
  assert job_host_data_scope._GPU_HOST_DATA_TYPES is GPU_STATS_TYPES


def test_shared_stamping_still_matches_gpus_only() -> None:
  mod = _load_feed_collapse()
  # A host_power_socket row on a jid payload (never emitted, D38) would be
  # stamped like any non-GPU row; stamping did not join the keep-dev set.
  for stamp in (parsing._shared_row_dev, mod._shared_row_dev):
    assert stamp("0", "nvidia_gpu", "42", True) == "0"
    assert stamp("0", HOST_POWER_SOCKET_TYPE, "42", True) == "j:42\x1f0"
    assert stamp("1", HOST_POWER_SOCKET_TYPE, "-", True) == "1"
  assert mark_side.shared_dev_for_row("nvidia_gpu", "0", "42", True) == "0"
  assert (
    mark_side.shared_dev_for_row(HOST_POWER_SOCKET_TYPE, "0", "42", True)
    == "j:42"
  )


def test_percore_repair_skips_host_power_socket() -> None:
  frame = pd.DataFrame(
    {
      "type": [HOST_POWER_SOCKET_TYPE, HOST_POWER_SOCKET_TYPE, "cpu"],
      "dev": ["0", "1", "3"],
    }
  )
  assert percore_mask(frame).tolist() == [False, False, True]


def test_cpython_collapse_keeps_socket_dev_and_sums_equal_sockets() -> None:
  out = _cpython_rows(_gb200_equal_socket_lines())
  sock = _socket_rows(out, "socket_power_w")
  assert sock["dev"].tolist() == ["0", "1", "0", "1"]
  assert sock["value"].tolist() == [45.0, 45.0, 45.0, 45.0]
  # Consumer sum by dev per (host, time): 90 W, not the 45 W a value dedup
  # of equal sockets gave on the retired per-CPU key.
  per_time = sock.groupby("time")["value"].sum().tolist()
  assert per_time == [90.0, 90.0]
  # Whole-node types still collapse to dev=''.
  cpu = out[out["type"] == "cpu"]
  assert set(cpu["dev"]) == {""}


def test_codon_collapse_matches_cpython_for_socket_rows() -> None:
  lines = _gb200_equal_socket_lines()
  py = _cpython_rows(lines)
  cd = _codon_rows(lines)
  for event in ("cpu_power_w", "socket_power_w", "sysio_power_w"):
    p = _socket_rows(py, event)
    c = _socket_rows(cd, event)
    assert c["dev"].tolist() == p["dev"].tolist() == ["0", "1", "0", "1"]
    for got, want in zip(c["value"], p["value"], strict=True):
      assert math.isclose(got, want)
  sock = _socket_rows(cd, "socket_power_w")
  assert sock.groupby("time")["value"].sum().tolist() == [90.0, 90.0]


def test_node_wide_dash_payload_keeps_socket_dev_without_job_stamp() -> None:
  for out in (
    _cpython_rows(_shared_tick_lines()),
    _codon_rows(_shared_tick_lines()),
  ):
    sock = _socket_rows(out, "socket_power_w")
    assert sock["dev"].tolist() == ["0", "1"]
    assert not any(str(d).startswith("j:") for d in sock["dev"])
    assert sock["value"].tolist() == [88.0, 90.0]
    # The jid payloads' CPU rows are stamped per job (shared-node contract).
    cpu_devs = set(out.loc[out["type"] == "cpu", "dev"])
    assert cpu_devs == {"j:41", "j:42"}


def test_collapse_unit_keeps_dev_for_stamped_and_plain_socket_rows() -> None:
  df = pd.DataFrame(
    {
      "time": [1.0, 1.0, 1.0, 1.0],
      "host": ["h"] * 4,
      "type": [HOST_POWER_SOCKET_TYPE] * 2 + ["cpu"] * 2,
      "dev": ["0", "1", "0", "1"],
      "event": ["socket_power_w"] * 2 + ["user"] * 2,
      "unit": ["W", "W", "", ""],
      "value": [45.0, 45.0, 1.0, 2.0],
      "delta": [float("nan")] * 4,
    }
  )
  out = parsing._collapse_stats_with_deltas(df)
  sock = out[out["type"] == HOST_POWER_SOCKET_TYPE]
  assert sorted(sock["dev"]) == ["0", "1"]
  assert sock["value"].sum() == 90.0
  cpu = out[out["type"] == "cpu"]
  assert cpu["dev"].tolist() == [""]
  assert cpu["value"].tolist() == [3.0]
