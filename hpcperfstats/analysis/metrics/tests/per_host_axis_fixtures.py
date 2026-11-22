"""Fixtures and independent NumPy oracles for the per-host metrics time axis.

Builders return deterministic ``ColumnarJobTable`` objects (``.frame`` is the
``JobFrame``) whose hosts sample on aligned, offset, mostly offset,
sub-second offset or irregular time axes. Every series carries a realistic
cumulative ``value`` and the ``arc`` ingest would store for it (float32 rate
over the interval ending at the sample, NaN on the first sample).

The oracles read the frame arrays directly and never call ``metrics.py`` or
the columnar pivot. They implement the target semantics of the per-host axis
design: each host on its own axis for endpoint and per-host rate metrics, and
for cross-host node imbalances the arc rates binned on one grid per job and
type (median period, circular-mean phase, end-sample rate bins, time-weighted
collision mean), then the relative-imbalance formula of HEAD.

``capture_fixture`` runs the real ``Metrics.compute_metrics`` path on a table
and returns the baseline record that
``fixtures/per_host_axis_baseline.json`` stores.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
  LLITE_METADATA_IOPS_EVENTS,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  INTEL_FP_ARITH_ALL_EVENTS,
)
from hpcperfstats.lib.dcgm_blank import nan_out_dcgm_numeric_blanks

US = 1_000_000
MIB = 1024 * 1024
# Minute-aligned epoch near the golden edge jobs.
BASE_EPOCH_S = 1_790_000_040
BASE_REV = "c2393d49"
BASELINE_PATH = (
  Path(__file__).parent / "fixtures" / "per_host_axis_baseline.json"
)
REPO_ROOT = Path(__file__).resolve().parents[4]

# Counter: cumulative value from a positive per-interval rate (per second).
# Gauge: snapshot value drawn per sample inside (lo, hi).
COUNTER = "counter"
GAUGE = "gauge"

# type -> event -> (kind, base rate per second | (lo, hi) gauge range).
TYPE_EVENTS: dict[str, dict[str, tuple[str, Any]]] = {
  "host_cpu": {
    "user": (COUNTER, 80.0),
    "system": (COUNTER, 5.0),
    "idle": (COUNTER, 15.0),
    "iowait": (COUNTER, 0.5),
  },
  "intel_x86_pmc_gpr8": {
    **{
      ev: (COUNTER, 2.0e8 / (1 + i))
      for i, ev in enumerate(INTEL_FP_ARITH_ALL_EVENTS)
    },
    "APERF": (COUNTER, 2.4e9),
    "MPERF": (COUNTER, 2.7e9),
    "instr_retired": (COUNTER, 3.0e9),
  },
  "intel_x86_uncore_imc_skx": {
    "dram_cas_reads": (COUNTER, 6.0e7),
    "dram_cas_writes": (COUNTER, 2.5e7),
  },
  "host_net": {
    "rx_bytes": (COUNTER, 1.5e6),
    "rx_packets": (COUNTER, 1.2e3),
    "tx_bytes": (COUNTER, 9.0e5),
    "tx_packets": (COUNTER, 8.0e2),
  },
  "host_ib": {
    "port_rcv_data": (COUNTER, 2.5e8),
    "port_rcv_pkts": (COUNTER, 1.1e5),
    "port_xmit_data": (COUNTER, 2.2e8),
    "port_xmit_pkts": (COUNTER, 1.0e5),
  },
  "host_lnet": {
    "rx_bytes": (COUNTER, 4.0e7),
    "tx_bytes": (COUNTER, 3.0e7),
  },
  "lustre_llite": {
    **{
      ev: (COUNTER, 3.0 + i) for i, ev in enumerate(LLITE_METADATA_IOPS_EVENTS)
    },
    "vfs_read_bytes": (COUNTER, 5.0e6),
    "vfs_write_bytes": (COUNTER, 2.0e6),
  },
  "host_opa": {
    "PortXmitWait": (COUNTER, 40.0),
    "SwPortCongestion": (COUNTER, 3.0),
    "PortRcvFECN": (COUNTER, 2.0),
    "PortRcvBECN": (COUNTER, 1.0),
  },
  "host_numa": {
    "numa_hit": (COUNTER, 2.0e6),
    "numa_miss": (COUNTER, 3.0e3),
    "numa_foreign": (COUNTER, 3.0e3),
    "local_node": (COUNTER, 2.0e6),
    "other_node": (COUNTER, 5.0e3),
  },
  "host_mem": {
    "mem_total": (GAUGE, (2.6e8, 2.6e8)),
    "mem_used": (GAUGE, (6.0e7, 1.8e8)),
    "slab": (GAUGE, (2.0e6, 4.0e6)),
    "file_pages": (GAUGE, (5.0e6, 2.0e7)),
  },
  "nvidia_gpu": {
    "gpu_util": (GAUGE, (20.0, 100.0)),
    "tensor_active": (GAUGE, (0.0, 60.0)),
    "power_usage": (GAUGE, (90.0, 650.0)),
    "clocks_event_reasons": (GAUGE, (0.0, 0.0)),
    "gpu_io_link_total_bytes": (COUNTER, 4.0e9),
  },
  "host_vm": {
    "pgpgin": (COUNTER, 1.0e4),
    "pgpgout": (COUNTER, 2.0e4),
  },
}
INTEGER_GAUGES = frozenset({"gpu_util"})
CLOCK_REASON_BITS = (0, 0, 1, 4, 8)

ONE_DEV = dict.fromkeys(TYPE_EVENTS, ("",)) | {"nvidia_gpu": ("0",)}
TWO_DEV = ONE_DEV | {
  "host_cpu": ("0", "1"),
  "intel_x86_pmc_gpr8": ("0", "1"),
  "intel_x86_uncore_imc_skx": ("0", "1"),
  "host_net": ("eth0", "eth1"),
  "host_ib": ("mlx5_0", "mlx5_1"),
  "lustre_llite": ("scratch", "work"),
  "host_numa": ("0", "1"),
  "host_mem": ("0", "1"),
  "nvidia_gpu": ("0", "1"),
}
# host_vm only appears in the single-host fixture, as the sparse slow tier
# the corpus jobs have.
SYNTH_TYPES = tuple(t for t in TYPE_EVENTS if t != "host_vm")

VEC_WIDTHS_64B = {
  "SSE_D_ALL": 1,
  "SIMD_D_256": 2,
  "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE": 1,
  "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE": 2,
  "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE": 4,
  "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE": 8,
  "SSE_DOUBLE_SCALAR": 1,
  "SSE_DOUBLE_PACKED": 2,
  "SIMD_DOUBLE_256": 4,
}
VEC_WIDTHS_32B = {
  "FP_ARITH_INST_RETIRED_SCALAR_SINGLE": 1,
  "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE": 4,
  "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE": 8,
  "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE": 16,
}
# Same value as metrics._IB_DATA_BYTES_PER_WORD at HEAD (host_ib U=4B words).
IB_BYTES_PER_WORD = 4
PMC_NOMINAL_GHZ = 2.7

# metric -> ("rate", type, [(event, weight)]) or ("snapshot", type, event):
# what each node imbalance compares on the synthetic schema (and, for
# edge_multihost, the FLOP / DRAM family the avg_flops / avg_mbw probe order
# picks).
SYNTH_IMBALANCE_SPECS: dict[str, tuple[str, str, Any]] = {
  "node_imbalance": ("rate", "host_cpu", [("user", 1.0)]),
  "flops_node_imbalance": (
    "rate",
    "intel_x86_pmc_gpr8",
    [(ev, 1.0) for ev in INTEL_FP_ARITH_ALL_EVENTS],
  ),
  "dram_bw_node_imbalance": (
    "rate",
    "intel_x86_uncore_imc_skx",
    [("dram_cas_reads", 1.0), ("dram_cas_writes", 1.0)],
  ),
  "lnet_node_imbalance": (
    "rate",
    "host_lnet",
    [("tx_bytes", 1.0), ("rx_bytes", 1.0)],
  ),
  "fabric_node_imbalance": (
    "rate",
    "host_ib",
    [("port_xmit_data", 1.0), ("port_rcv_data", 1.0)],
  ),
  "gpu_util_node_imbalance": ("snapshot", "nvidia_gpu", "gpu_util"),
  "tensor_node_imbalance": ("snapshot", "nvidia_gpu", "tensor_active"),
}
EDGE_IMBALANCE_SPECS: dict[str, tuple[str, str, Any]] = (
  SYNTH_IMBALANCE_SPECS
  | {
    "flops_node_imbalance": ("rate", "amd_x86_pmc", [("fp_ops_retired", 1.0)]),
    "dram_bw_node_imbalance": (
      "rate",
      "amd_x86_uncore_df",
      [(f"MBW_CHANNEL_{i}", 1.0) for i in range(8)],
    ),
  }
)


def _series_rows(
  rng: np.random.Generator,
  host: str,
  host_factor: float,
  typ: str,
  devs: Sequence[str],
  t_us: np.ndarray,
) -> list[tuple]:
  """Rows ``(time_us, host, type, event, dev, owned, value, arc, delta)`` of
  every event and device of ``typ`` on one host at times ``t_us``."""
  rows: list[tuple] = []
  t_us = np.asarray(t_us, dtype=np.int64)
  dt_s = np.diff(t_us) / US
  for ev, (kind, spec) in TYPE_EVENTS[typ].items():
    for d_i, dev in enumerate(devs):
      dev_factor = 1.0 - 0.15 * d_i
      if kind == COUNTER:
        noise = rng.lognormal(0.0, 0.35, size=dt_s.size)
        rate = spec * host_factor * dev_factor * noise
        start = float(rng.integers(10**9, 10**11))
        value = np.floor(
          start + np.concatenate(([0.0], np.cumsum(rate * dt_s)))
        )
      else:
        lo, hi = spec
        u = rng.random(t_us.size)
        value = lo + (hi - lo) * np.clip(u * host_factor * dev_factor, 0, 1)
        if ev in INTEGER_GAUGES:
          value = np.round(value)
        if ev == "clocks_event_reasons":
          value = np.array(
            [float(CLOCK_REASON_BITS[k]) for k in rng.integers(0, 5, t_us.size)]
          )
      delta = np.concatenate(([np.nan], np.diff(value)))
      arc = np.full(t_us.size, np.nan)
      ok = np.concatenate(([False], dt_s > 0))
      arc[ok] = (delta[ok] / np.concatenate(([1.0], dt_s))[ok]).astype(
        np.float32
      )
      for k in range(t_us.size):
        rows.append(
          (
            int(t_us[k]),
            host,
            typ,
            ev,
            dev,
            1,
            float(value[k]),
            float(arc[k]),
            float(delta[k]),
          )
        )
  return rows


def build_table(
  name: str,
  host_times_us: Mapping[str, np.ndarray],
  *,
  seed: int,
  devs: Mapping[str, Sequence[str]] = ONE_DEV,
  types: Sequence[str] = SYNTH_TYPES,
  type_masks: Mapping[str, Callable[[np.ndarray], np.ndarray]] | None = None,
  host_factors: Sequence[float] = (1.0, 0.72, 0.86, 0.55, 0.93),
) -> ColumnarJobTable:
  """Deterministic synthetic table: every host samples ``types`` at its own
  times; ``type_masks[typ](t)`` keeps a subset of a host's times for a
  sparsely sampled type."""
  rng = np.random.default_rng(seed)
  rows: list[tuple] = []
  masks = dict(type_masks or {})
  for h_i, host in enumerate(sorted(host_times_us)):
    t_all = np.asarray(host_times_us[host], dtype=np.int64)
    for typ in types:
      t = t_all[masks[typ](t_all)] if typ in masks else t_all
      rows.extend(_series_rows(rng, host, host_factors[h_i], typ, devs[typ], t))
  schema = {typ: list(TYPE_EVENTS[typ]) for typ in types}
  return table_from_records(rows, jid=name, schema=schema)


def _ticks(n: int, period_s: float = 60.0, offset_s: float = 0.0) -> np.ndarray:
  """Regular epoch-us sample times starting at ``BASE_EPOCH_S + offset_s``."""
  return np.round(
    (BASE_EPOCH_S + offset_s + period_s * np.arange(n)) * US
  ).astype(np.int64)


def aligned_2dev_table() -> ColumnarJobTable:
  """4 hosts, 30 samples at 60 s on identical times, 2 devices per host."""
  t = _ticks(30)
  return build_table(
    "aligned_2dev",
    {f"c{i:03d}": t for i in range(1, 5)},
    seed=101,
    devs=TWO_DEV,
  )


def unaligned_17_41_table() -> ColumnarJobTable:
  """3 hosts at 60 s, fixed offsets 0 / 17 / 41 s: no shared timestamp."""
  return build_table(
    "unaligned_17_41",
    {
      f"c{i:03d}": _ticks(30, offset_s=off)
      for i, off in ((1, 0), (2, 17), (3, 41))
    },
    seed=202,
  )


MOSTLY_OFFSET_SHARED_TICKS = (5, 10, 20)


def mostly_offset_table() -> ColumnarJobTable:
  """2 hosts, the second 17 s later, which also samples at 3 of the first
  host's ticks (the production false-0% time_imbalance shape)."""
  a = _ticks(30)
  b = np.sort(
    np.concatenate(
      (_ticks(30, offset_s=17), a[list(MOSTLY_OFFSET_SHARED_TICKS)])
    )
  )
  return build_table("mostly_offset", {"c001": a, "c002": b}, seed=303)


SUBSECOND_OFFSETS_S = (0.12, 0.47, 0.81, 0.95)
SUBSECOND_EXTRA_TICKS = {
  "c003": (3, 9, 15, 21),
  "c004": (4, 5, 6, 12, 13, 24, 25),
}
SUBSECOND_EXTRA_DELAY_S = 2.1


def subsecond_table() -> ColumnarJobTable:
  """4 hosts at 60 s, all at second 58 of the minute with sub-second offsets
  (no shared timestamp); two hosts add extra samples
  ``SUBSECOND_EXTRA_DELAY_S`` after some ticks."""
  times = {}
  for i, off in enumerate(SUBSECOND_OFFSETS_S, start=1):
    host = f"c{i:03d}"
    t = _ticks(30, offset_s=58 + off)
    extra = [
      t[k] + round(SUBSECOND_EXTRA_DELAY_S * US)
      for k in SUBSECOND_EXTRA_TICKS.get(host, ())
    ]
    times[host] = np.sort(
      np.concatenate((t, np.asarray(extra, dtype=np.int64)))
    )
  return build_table("subsecond", times, seed=404)


# Corpus-like irregular intervals (s): mostly 60, longer gaps, short extras,
# and a 0.6 s step that puts two samples in one floored second.
SINGLE_INTERVALS_S = (
  60,
  60,
  60,
  120,
  60,
  240,
  60,
  60,
  0.6,
  59.4,
  60,
  30,
  30,
  60,
  600,
  60,
  60,
  1.5,
  58.5,
  60,
  180,
  60,
  60,
  60,
  109,
  11,
  60,
  60,
  60,
  60,
)


def single_irregular_table() -> ColumnarJobTable:
  """1 host, irregular intervals; ``host_vm`` on every 5th sample and
  ``host_lnet`` on every 3rd (types sampled more sparsely than the union)."""
  t = np.round(
    (BASE_EPOCH_S + np.concatenate(([0.0], np.cumsum(SINGLE_INTERVALS_S)))) * US
  ).astype(np.int64)

  def every(n: int) -> Callable[[np.ndarray], np.ndarray]:
    def mask(tt: np.ndarray) -> np.ndarray:
      keep = np.zeros(tt.size, dtype=bool)
      keep[::n] = True
      keep[-1] = True
      return keep

    return mask

  return build_table(
    "single_irregular",
    {"c001": t},
    seed=505,
    types=(*SYNTH_TYPES, "host_vm"),
    type_masks={"host_vm": every(5), "host_lnet": every(3)},
  )


def golden_corpus_dir() -> Path | None:
  """Golden corpus directory (``HPCPERFSTATS_METRICS_GOLDEN_DIR``) or None."""
  path = Path(
    os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_DIR")
    or "/data/user/sharrell/hpcperfstats-metrics-golden"
  )
  return (
    path if (path / "jobs" / "edge_multihost.frame.npz").is_file() else None
  )


def edge_multihost_table() -> ColumnarJobTable | None:
  """The golden ``edge_multihost`` job (4 hosts, 60 s, aligned), loaded the
  way ``test_columnar_golden.py`` loads it; None without the corpus."""
  d = golden_corpus_dir()
  if d is None:
    return None
  name = "metrics_golden_corpus_per_host_axis"
  mod = sys.modules.get(name)
  if mod is None:
    spec = importlib.util.spec_from_file_location(
      name, REPO_ROOT / "scripts" / "metrics_golden_corpus.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
  with gzip.open(
    d / "jobs" / "edge_multihost.golden.json.gz", "rt", encoding="utf-8"
  ) as fh:
    meta = json.load(fh)["jid_table"]
  frame = mod.load_frame_npz(d / "jobs" / "edge_multihost.frame.npz")
  frame.check_contract()
  return ColumnarJobTable.from_golden("edge_multihost", frame, meta)


class AxisFixture(NamedTuple):
  """A fixture: builder, whether HEAD's union axis is exact for it (pinned
  in the baseline), and the node-imbalance specs its oracle uses."""

  build: Callable[[], ColumnarJobTable | None]
  baseline: bool
  imbalance_specs: Mapping[str, tuple[str, str, Any]]
  description: str


FIXTURES: dict[str, AxisFixture] = {
  "edge_multihost": AxisFixture(
    edge_multihost_table,
    True,
    EDGE_IMBALANCE_SPECS,
    "golden synthetic job: 4 hosts, 60 s, aligned, linear counters",
  ),
  "aligned_2dev": AxisFixture(
    aligned_2dev_table,
    True,
    SYNTH_IMBALANCE_SPECS,
    "4 hosts x 2 devices, 30 samples at 60 s, identical times",
  ),
  "single_irregular": AxisFixture(
    single_irregular_table,
    True,
    SYNTH_IMBALANCE_SPECS,
    "1 host, irregular intervals, duplicate floored second, sparse "
    "host_vm and host_lnet",
  ),
  "unaligned_17_41": AxisFixture(
    unaligned_17_41_table,
    False,
    SYNTH_IMBALANCE_SPECS,
    "3 hosts at 60 s with fixed 0 / 17 / 41 s offsets",
  ),
  "mostly_offset": AxisFixture(
    mostly_offset_table,
    False,
    SYNTH_IMBALANCE_SPECS,
    "2 hosts 17 s apart sharing 3 ticks, the production false-0% time_imbalance shape",
  ),
  "subsecond": AxisFixture(
    subsecond_table,
    False,
    SYNTH_IMBALANCE_SPECS,
    "4 hosts, same second, sub-second offsets, extra samples (production Horizon cadence)",
  ),
}
BASELINE_FIXTURES = tuple(k for k, f in FIXTURES.items() if f.baseline)
# Pinned baseline values that are wrong at HEAD on purpose-built shapes, so a
# correct per-host axis is expected to change them. max_lnetbw asks the
# cluster grids for "lnet" while the pivot is keyed "host_lnet", so it always
# takes the per-host value fallback on the union axis; with host_lnet sampled
# on every 3rd tick every union-axis interval has a NaN end and it is None.
KNOWN_HEAD_DEFECTS: dict[str, dict[str, str]] = {
  "single_irregular": {
    "max_lnetbw": "None at HEAD: sparse host_lnet on the union axis (value "
    "fallback, cluster lookup key 'lnet' misses 'host_lnet')",
  },
}


def frame_digest(table: ColumnarJobTable) -> str:
  """sha256 over the frame's name tables and columns (fixture drift check)."""
  f = table.frame
  h = hashlib.sha256()
  h.update(
    json.dumps(
      [list(f.hosts), [list(s) for s in f.series], list(f.devs)]
    ).encode()
  )
  for col in (
    f.time_us,
    f.host_id,
    f.series_id,
    f.dev_id,
    f.owned,
    f.value,
    f.arc,
  ):
    h.update(np.ascontiguousarray(col).tobytes())
  return h.hexdigest()


def _jsonable(x: Any) -> Any:
  """NaN -> None, +-inf -> "inf" / "-inf", NumPy scalars -> Python."""
  if x is None or isinstance(x, (bool, str)):
    return x
  if isinstance(x, (int, np.integer)):
    return int(x)
  if isinstance(x, (float, np.floating)):
    v = float(x)
    if math.isnan(v):
      return None
    if math.isinf(v):
      return "inf" if v > 0 else "-inf"
    return v
  if isinstance(x, np.ndarray):
    return [_jsonable(v) for v in x.tolist()]
  if isinstance(x, (list, tuple)):
    return [_jsonable(v) for v in x]
  raise TypeError(f"not JSON-encodable: {x!r}")


def decode_grid(rows: list[list[Any]]) -> np.ndarray:
  """Inverse of the grid encoding: None -> NaN, "inf" / "-inf" -> inf."""
  enc = {None: np.nan, "inf": np.inf, "-inf": -np.inf}
  return np.array(
    [
      [enc[v] if v is None or isinstance(v, str) else v for v in r]
      for r in rows
    ],
    dtype=np.float64,
  ).reshape(len(rows), -1 if rows and rows[0] else 0)


def compute_rows(table: ColumnarJobTable) -> list[dict[str, Any]]:
  """``Metrics.compute_metrics`` rows of one table (the golden test entry,
  with ``open_job_table`` patched to return ``table``)."""
  from unittest import mock

  from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod

  m = metrics_mod.Metrics()
  job = SimpleNamespace(
    jid=table.jid,
    nhosts=len(table.host_list),
    ncores=64 * len(table.host_list),
    artifact_only=False,
    telemetry_first_time=table.start_time,
    telemetry_last_time=table.end_time,
  )
  with mock.patch.object(
    metrics_mod, "open_job_table", lambda job, **_kw: table
  ):
    return m.compute_metrics(job)["rows"]


def cluster_grids(table: ColumnarJobTable) -> dict[str, Any]:
  """The current code's cluster value / arc grids of every type with rows
  (``metrics._cluster_grids`` over the table's metrics view)."""
  from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod
  from hpcperfstats.analysis.metrics.lib.gen.utils import utils

  u = utils(table.metrics_view())
  grids: dict[str, Any] = {}
  for typ in u.job.pivots.types:
    g = metrics_mod._cluster_grids(u, typ)
    grids[typ] = {"value": g.value, "arc": g.arc, "t_s": g.t_s}
  return grids


def capture_fixture(table: ColumnarJobTable) -> dict[str, Any]:
  """Baseline record of one table at the current code: every complex metric
  (:func:`compute_rows`) and the cluster value / arc grids of every type
  with rows (:func:`cluster_grids`)."""
  from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod

  rows = compute_rows(table)
  wanted = set(metrics_mod.Metrics().complex_metrics_list)
  metrics: dict[str, Any] = {}
  nan_metrics: list[str] = []
  for r in rows:
    if r["metric"] not in wanted:
      continue
    v = r["value"]
    if isinstance(v, (float, np.floating)) and not math.isfinite(float(v)):
      nan_metrics.append(r["metric"])
    metrics[r["metric"]] = _jsonable(v)
  assert set(metrics) == wanted, sorted(wanted ^ set(metrics))
  grids = {
    typ: {"value": _jsonable(g["value"]), "arc": _jsonable(g["arc"])}
    for typ, g in cluster_grids(table).items()
  }
  return {
    "frame_sha256": frame_digest(table),
    "metrics": dict(sorted(metrics.items())),
    "nan_metrics": sorted(nan_metrics),
    "cluster_grids": dict(sorted(grids.items())),
  }


def load_baseline() -> dict[str, Any]:
  """The committed baseline file."""
  return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


# Oracles. Plain NumPy over frame arrays; no metrics.py, no pivot kernels.


class HostSeries(NamedTuple):
  """One host's series of one type: distinct sample times (us), floored
  seconds, and the per-time device mean of each requested event."""

  t_us: np.ndarray
  t_s: np.ndarray
  grid: np.ndarray


def host_series(
  table: ColumnarJobTable, typ: str, events: Sequence[str], field: str = "value"
) -> dict[str, HostSeries]:
  """Per host with owned rows of ``typ``: its own times and the device-mean
  ``field`` ("value" or "arc") of each event (NaN where no finite sample)."""
  f = table.frame
  col = getattr(f, field)
  sid = {s: i for i, s in enumerate(f.series)}
  ev_sids = [sid.get((typ, e), -1) for e in events]
  rows_type = np.isin(f.series_id, [s for s in ev_sids if s >= 0]) & (
    f.owned == 1
  )
  out: dict[str, HostSeries] = {}
  for hid in np.unique(f.host_id[rows_type]).tolist():
    rows_h = rows_type & (f.host_id == hid)
    t_us = np.unique(f.time_us[rows_h])
    grid = np.full((t_us.size, len(events)), np.nan)
    for j, s in enumerate(ev_sids):
      r = rows_h & (f.series_id == s)
      if s < 0 or not r.any():
        continue
      pos = np.searchsorted(t_us, f.time_us[r])
      v = col[r]
      fin = np.isfinite(v)
      sums = np.bincount(pos[fin], weights=v[fin], minlength=t_us.size)
      cnt = np.bincount(pos[fin], minlength=t_us.size)
      with np.errstate(invalid="ignore", divide="ignore"):
        grid[:, j] = np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan)
    out[f.hosts[hid]] = HostSeries(
      t_us, np.floor_divide(t_us, US).astype(np.float64), grid
    )
  return out


class BinGrid(NamedTuple):
  """Bin grid of one job and type: period and phase (us), mean resultant
  length, and a function from epoch-us times to compacted bin ids."""

  period_us: float
  phase_us: float
  resultant: float
  raw_bins: np.ndarray
  bin_of: Callable[[np.ndarray], np.ndarray]


def bin_grid(times_by_host: Mapping[str, np.ndarray]) -> BinGrid | None:
  """Bin rule: P = median of every host's intervals between distinct
  samples; phase = circular mean of ``t mod P`` (0 when R < 0.5); raw bin
  ``floor((t - phase + P/2) / P)``, compacted over the bins sampled. None
  when no host has two distinct samples."""
  uniq = [
    np.unique(np.asarray(t, dtype=np.int64)) for t in times_by_host.values()
  ]
  gaps = np.concatenate([np.diff(u) for u in uniq] or [np.empty(0)])
  if gaps.size == 0:
    return None
  period = float(np.median(gaps))
  all_t = np.concatenate(uniq).astype(np.float64)
  ang = 2 * np.pi * np.mod(all_t, period) / period
  c, s = float(np.mean(np.cos(ang))), float(np.mean(np.sin(ang)))
  r = math.hypot(c, s)
  phase = period / (2 * np.pi) * math.atan2(s, c) if r >= 0.5 else 0.0

  def raw(t: np.ndarray) -> np.ndarray:
    return np.floor(
      (np.asarray(t, dtype=np.float64) - phase + period / 2) / period
    ).astype(np.int64)

  raw_bins = np.unique(raw(all_t))

  def bin_of(t: np.ndarray) -> np.ndarray:
    return np.searchsorted(raw_bins, raw(t))

  return BinGrid(period, phase, r, raw_bins, bin_of)


def relative_imbalance_percent(
  per_host: Mapping[str, Mapping[int, float]],
) -> float | None:
  """HEAD's formula on per-host ``{bin: value}`` (finite values only): per
  bin the max over the hosts present (and 0); per host the mean of
  ``(max - v) / max`` over its bins with max > 0 (no such bin counts 0);
  100 x the largest host mean. None without hosts."""
  if not per_host:
    return None
  peak: dict[int, float] = {}
  for series in per_host.values():
    for b, v in series.items():
      peak[b] = max(peak.get(b, 0.0), v)
  worst = 0.0
  for series in per_host.values():
    rel = [(peak[b] - v) / peak[b] for b, v in series.items() if peak[b] > 0]
    if rel:
      worst = max(worst, float(np.mean(rel)))
  return 100 * worst


def rate_imbalance_oracle(
  table: ColumnarJobTable,
  typ: str,
  weighted_events: Sequence[tuple[str, float]],
) -> float | None:
  """Node imbalance from arc rates (the ingest arc, not diff(value) /
  diff(t)): per host the rate of interval k is the weighted sum of the
  device-mean arcs at sample k+1.
  One host: its own intervals, no binning. Several: rates go to the bin of
  the interval's end sample, a host's rates in one bin are time-weighted."""
  events = [e for e, _ in weighted_events]
  w = np.array([wt for _, wt in weighted_events])
  series = host_series(table, typ, events, "arc")
  if not series:
    return None
  rates: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
  for host, s in series.items():
    total = s.grid @ w if s.grid.size else np.empty(0)
    rates[host] = (s.t_us[1:], np.diff(s.t_us) / US, total[1:])
  if len(series) == 1:
    ((_t_end, _dt, r),) = rates.values()
    host = next(iter(rates))
    return relative_imbalance_percent(
      {host: {k: float(v) for k, v in enumerate(r) if np.isfinite(v)}}
    )
  grid = bin_grid({h: s.t_us for h, s in series.items()})
  if grid is None:
    return None
  per_host: dict[str, dict[int, float]] = {}
  for host, (t_end, dt, r) in rates.items():
    fin = np.isfinite(r)
    b = grid.bin_of(t_end[fin])
    num: dict[int, float] = {}
    den: dict[int, float] = {}
    for bi, ri, di in zip(
      b.tolist(), r[fin].tolist(), dt[fin].tolist(), strict=True
    ):
      num[bi] = num.get(bi, 0.0) + ri * di
      den[bi] = den.get(bi, 0.0) + di
    per_host[host] = {bi: num[bi] / den[bi] for bi in num}
  return relative_imbalance_percent(per_host)


def snapshot_imbalance_oracle(
  table: ColumnarJobTable, typ: str, event: str
) -> float | None:
  """Node imbalance of a snapshot value (GPU util / tensor): DCGM blanks
  dropped; one host uses its samples directly; several hosts put each sample
  in the bin of its time, a host's samples in one bin are averaged."""
  series = host_series(table, typ, [event], "value")
  if not series:
    return None
  vals = {
    h: nan_out_dcgm_numeric_blanks(s.grid[:, 0].copy())
    for h, s in series.items()
  }
  if len(series) == 1:
    ((host, v),) = vals.items()
    return relative_imbalance_percent(
      {host: {k: float(x) for k, x in enumerate(v) if np.isfinite(x)}}
    )
  grid = bin_grid({h: s.t_us for h, s in series.items()})
  if grid is None:
    return None
  per_host = {}
  for host, s in series.items():
    v = vals[host]
    fin = np.isfinite(v)
    b = grid.bin_of(s.t_us[fin])
    acc: dict[int, list[float]] = {}
    for bi, x in zip(b.tolist(), v[fin].tolist(), strict=True):
      acc.setdefault(bi, []).append(x)
    per_host[host] = {bi: float(np.mean(xs)) for bi, xs in acc.items()}
  return relative_imbalance_percent(per_host)


def node_imbalance_oracles(
  table: ColumnarJobTable, specs: Mapping[str, tuple[str, str, Any]]
) -> dict[str, float | None]:
  """Every node imbalance of ``specs`` from the oracles above."""
  out = {}
  for metric, (kind, typ, arg) in specs.items():
    out[metric] = (
      rate_imbalance_oracle(table, typ, arg)
      if kind == "rate"
      else snapshot_imbalance_oracle(table, typ, arg)
    )
  return out


def time_imbalance_oracle(
  table: ColumnarJobTable, typ: str = "host_cpu", event: str = "user"
) -> float | None:
  """``time_imbalance`` on each host's own axis with the historical split
  loop (``np.trapezoid`` per split): rate = diff(value) / diff(floored
  seconds), NaN / inf -> 0, clipped at 0; per host the minimum of
  mean-after / mean-before over splits 2 .. n-2; 100 x min over hosts."""
  vals = []
  for s in host_series(table, typ, [event]).values():
    dt = np.diff(s.t_s)
    rate = np.zeros(dt.size)
    ok = dt > 0
    rate[ok] = np.diff(s.grid[:, 0])[ok] / dt[ok]
    rate = np.maximum(np.nan_to_num(rate, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
    tmid = (s.t_s[:-1] + s.t_s[1:]) / 2.0
    n = rate.size
    best = None
    for i in range(2, n - 1) if n >= 4 else ():
      before_w = tmid[i] - tmid[0]
      after_w = tmid[-1] - tmid[i]
      if before_w <= 0 or after_w <= 0:
        continue
      a = np.trapezoid(rate[:i], tmid[:i]) / before_w
      if not (a > 0) or not np.isfinite(a):
        continue
      b = np.trapezoid(rate[i:], tmid[i:]) / after_w
      ratio = b / a
      if not np.isfinite(ratio) or ratio < 0 or ratio > 1e9:
        continue
      best = ratio if best is None else min(best, ratio)
    if best is not None:
      vals.append(best)
  return 100 * min(vals) if vals else None


def _endpoint_delta(col: np.ndarray) -> float | None:
  """Last finite minus first finite sample of one host's column."""
  fin = np.flatnonzero(np.isfinite(col))
  return None if fin.size == 0 else float(col[fin[-1]] - col[fin[0]])


def avg_ethbw_oracle(
  table: ColumnarJobTable, typ: str = "host_net"
) -> float | None:
  """Mean over hosts of (rx + tx bytes over the host's endpoints) / (its own
  span x MiB); hosts with a negative / missing delta or zero span skipped;
  0 counts as no data."""
  per_host = []
  for s in host_series(table, typ, ["rx_bytes", "tx_bytes"]).values():
    d = [_endpoint_delta(s.grid[:, j]) for j in range(2)]
    span = s.t_s[-1] - s.t_s[0]
    if None in d or span <= 0 or sum(d) < 0:
      continue
    per_host.append(sum(d) / (span * MIB))
  value = float(np.mean(per_host)) if per_host else None
  return value or None


def avg_freq_oracle(
  table: ColumnarJobTable,
  typ: str = "intel_x86_pmc_gpr8",
  num: str = "APERF",
  den: str = "MPERF",
  nominal_ghz: float = PMC_NOMINAL_GHZ,
) -> float | None:
  """Mean over hosts of nominal x d(num) / d(den) over the host endpoints."""
  per_host = []
  for s in host_series(table, typ, [num, den]).values():
    dn, dd = _endpoint_delta(s.grid[:, 0]), _endpoint_delta(s.grid[:, 1])
    if dn is None or not dd:
      continue
    per_host.append(nominal_ghz * dn / dd)
  return float(np.mean(per_host)) if per_host else None


def avg_packetsize_oracle(
  table: ColumnarJobTable,
  typ: str = "host_ib",
  pkts: tuple[str, str] = ("port_xmit_pkts", "port_rcv_pkts"),
  data: tuple[str, str] = ("port_xmit_data", "port_rcv_data"),
  bytes_per_word: float = IB_BYTES_PER_WORD,
) -> float | None:
  """Mean over hosts of data bytes / (packets x MiB) over the host
  endpoints (host_ib data counters are 4-byte words)."""
  per_host = []
  for s in host_series(table, typ, [*pkts, *data]).values():
    d = [_endpoint_delta(s.grid[:, j]) for j in range(4)]
    if None in d or d[0] + d[1] == 0:
      continue
    per_host.append((d[2] + d[3]) * bytes_per_word / ((d[0] + d[1]) * MIB))
  return float(np.mean(per_host)) if per_host else None


def vecpercent_oracle(
  table: ColumnarJobTable,
  widths: Mapping[str, int],
  typ: str = "intel_x86_pmc_gpr8",
) -> float | None:
  """100 x vector FLOPs / all FLOPs summed over hosts, FLOPs = endpoint
  instruction delta x lanes."""
  events = [e for e in table.schema.get(typ, []) if e in widths]
  vec = scalar = 0.0
  for s in host_series(table, typ, events).values():
    for j, e in enumerate(events):
      d = _endpoint_delta(s.grid[:, j])
      if d is None:
        continue
      if widths[e] > 1:
        vec += d * widths[e]
      else:
        scalar += d * widths[e]
  return None if vec + scalar == 0 else 100 * vec / (vec + scalar)


def avg_vector_width_oracle(
  table: ColumnarJobTable,
  widths: Mapping[str, int],
  typ: str = "intel_x86_pmc_gpr8",
) -> float | None:
  """Mean over hosts of FLOPs / instructions over the host endpoints."""
  events = [e for e in table.schema.get(typ, []) if e in widths]
  per_host = []
  for s in host_series(table, typ, events).values():
    flops = instr = 0.0
    for j, e in enumerate(events):
      d = _endpoint_delta(s.grid[:, j])
      if d is None:
        continue
      instr += d
      flops += d * widths[e]
    if instr:
      per_host.append(flops / instr)
  return float(np.mean(per_host)) if per_host else None


def per_host_oracles(table: ColumnarJobTable) -> dict[str, float | None]:
  """Per-host-axis oracle of ``time_imbalance`` and the endpoint metrics on
  the synthetic schema."""
  return {
    "time_imbalance": time_imbalance_oracle(table),
    "avg_ethbw": avg_ethbw_oracle(table),
    "avg_freq": avg_freq_oracle(table),
    "avg_packetsize": avg_packetsize_oracle(table),
    "vecpercent_64b": vecpercent_oracle(table, VEC_WIDTHS_64B),
    "avg_vector_width_64b": avg_vector_width_oracle(table, VEC_WIDTHS_64B),
    "vecpercent_32b": vecpercent_oracle(table, VEC_WIDTHS_32B),
    "avg_vector_width_32b": avg_vector_width_oracle(table, VEC_WIDTHS_32B),
  }


def cluster_arc_peak_oracle(
  table: ColumnarJobTable,
  typ: str,
  events: Sequence[str],
  divisor: float,
  max_sane: float | None = None,
) -> float | None:
  """Peak of the cluster arc series: per bin (one host: each distinct sample
  time; several: ``bin_grid`` of the hosts' times) the mean of each event's
  finite arcs over every (host, device) row, summed over ``events``; the
  largest positive sum divided by ``divisor`` that is at most ``max_sane``.
  A host's first owned sample of ``typ`` (any event) is left out of the
  arc: its ingest rate covers the interval before the job."""
  f = table.frame
  sid = {s: i for i, s in enumerate(f.series)}
  sids = [sid[(typ, e)] for e in events]
  typ_rows = np.isin(
    f.series_id, [i for s, i in sid.items() if s[0] == typ]
  ) & (f.owned == 1)
  rows = np.isin(f.series_id, sids) & (f.owned == 1)
  t = f.time_us[rows]
  host = f.host_id[rows]
  first_t = {
    h: f.time_us[typ_rows & (f.host_id == h)].min() for h in np.unique(host)
  }
  pre_job = t == np.array([first_t[h] for h in host.tolist()], dtype=t.dtype)
  by_host = {h: np.unique(t[host == h]) for h in np.unique(host).tolist()}
  if len(by_host) == 1:
    keys = np.unique(t)
    key = np.searchsorted(keys, t)
    n = keys.size
  else:
    grid = bin_grid(by_host)
    key = grid.bin_of(t)
    n = grid.raw_bins.size
  total = np.zeros(n)
  for s in sids:
    r = f.series_id[rows] == s
    a = np.where(pre_job[r], np.nan, f.arc[rows][r])
    fin = np.isfinite(a)
    cnt = np.bincount(key[r][fin], minlength=n)
    sums = np.bincount(key[r][fin], weights=a[fin], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
      total = total + np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan)
  pos = total[np.isfinite(total) & (total > 0)] / divisor
  if max_sane is not None:
    pos = pos[pos <= max_sane]
  return float(pos.max()) if pos.size else None


def host_value_peak_oracle(
  table: ColumnarJobTable, typ: str, events: Sequence[str]
) -> float | None:
  """Largest per-host peak of diff(sum of device-mean values) / diff(floored
  seconds) on each host's own axis (counter units per second)."""
  peaks = []
  for s in host_series(table, typ, list(events)).values():
    total = s.grid.sum(axis=1)
    dt = np.diff(s.t_s)
    ok = dt > 0
    rate = np.diff(total)[ok] / dt[ok]
    rate = rate[np.isfinite(rate) & (rate > 0)]
    if rate.size:
      peaks.append(float(rate.max()))
  return max(peaks) if peaks else None
