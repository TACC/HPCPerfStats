"""Pure parsing helpers for stats files (no Django). Used by sync_timedb and by unit tests."""
import os
import numpy as np
import pandas as pd
from pandas import DataFrame, concat, to_datetime

from hpcperfstats.file_locking import LOCK_SUFFIX, file_read_lock_wait

amd64_pmc_eventmap = {
    0x43ff03: "FLOPS,W=48",
    0x4300c2: "BRANCH_INST_RETIRED,W=48",
    0x4300c3: "BRANCH_INST_RETIRED_MISS,W=48",
    0x4308af: "DISPATCH_STALL_CYCLES1,W=48",
    0x43ffae: "DISPATCH_STALL_CYCLES0,W=48"
}

amd64_df_eventmap = {
    0x403807: "MBW_CHANNEL_0,W=48,U=64B",
    0x403847: "MBW_CHANNEL_1,W=48,U=64B",
    0x403887: "MBW_CHANNEL_2,W=48,U=64B",
    0x4038c7: "MBW_CHANNEL_3,W=48,U=64B",
    0x433907: "MBW_CHANNEL_4,W=48,U=64B",
    0x433947: "MBW_CHANNEL_5,W=48,U=64B",
    0x433987: "MBW_CHANNEL_6,W=48,U=64B",
    0x4339c7: "MBW_CHANNEL_7,W=48,U=64B",
}

intel_8pmc3_eventmap = {
    0x4301c7: 'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1',
    0x4302c7: 'FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1',
    0x4304c7: 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2',
    0x4308c7: 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4',
    0x4310c7: 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4',
    0x4320c7: 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8',
    0x4340c7: 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8',
    0x4380c7: 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16',
    # SNB/IVB-style SSE/AVX double FLOP proxies (intel_8pmc3_eventmap below).
    0x438010: 'SSE_DOUBLE_SCALAR,W=48,U=1',
    0x431010: 'SSE_DOUBLE_PACKED,W=48,U=2',
    0x430211: 'SIMD_DOUBLE_256,W=48,U=4',
    0x439010: 'SSE_DOUBLE_ALL,W=48,U=1',
    "FIXED_CTR0": 'INST_RETIRED,W=48',
    "FIXED_CTR1": 'APERF,W=48',
    "FIXED_CTR2": 'MPERF,W=48'
}

intel_skx_imc_eventmap = {
    0x400304: "CAS_READS,W=48",
    0x400c04: "CAS_WRITES,W=48",
    0x400b01: "ACT_COUNT,W=48",
    0x400102: "PRE_COUNT_MISS,W=48",
}

# Older Intel IMC generations use different perf-event encodings for the same
# logical CAS_* signals. Keep these mappings in sync with
# intel_*_imc_eventmap tables below so roofline/avg_mbw can rely on
# CAS_READS/CAS_WRITES across SNB/IVB/HSW/BDW/KNL/SKX.
intel_snb_imc_eventmap = {
    # IMC_PERF_EVENT(0x04, 0x03)
    0x400304: "CAS_READS,W=48",
    # IMC_PERF_EVENT(0x04, 0x0b)
    0x400b04: "CAS_WRITES,W=48",
}

intel_ivb_imc_eventmap = intel_snb_imc_eventmap

intel_hsw_imc_eventmap = {
    # IMC_PERF_EVENT(0x04, 0x03)
    0x400304: "CAS_READS,W=48",
    # IMC_PERF_EVENT(0x04, 0x0b)
    0x400b04: "CAS_WRITES,W=48",
}

intel_bdw_imc_eventmap = intel_hsw_imc_eventmap

intel_knl_mc_dclk_eventmap = {
    # KNL_MC_DCLK_PERF_EVENT(0x03, 0x01)
    0x300301: "CAS_READS,W=48",
    # KNL_MC_DCLK_PERF_EVENT(0x03, 0x09)
    0x300309: "CAS_WRITES,W=48",
}

exclude_types = [
    "ib", "ib_sw", "intel_skx_cha", "ps", "sysv_shm", "tmpfs", "vfs"
]

# Collapse multi-GPU nvidia_gpu rows (same host/type/event/unit/time) before DB insert.
# See plan: sum util/activity/power; mean temperature; bitwise OR for clock bitmask.
_NVIDIA_GPU_SUM_EVENTS = frozenset({
    "gpu_util",
    "gpu_io_link_total_bytes",
    "mem_util",
    "mem_used_mb",
    "mem_total_mb",
    "fp64_active",
    "fp32_active",
    "fp16_active",
    "sm_active",
    "sm_occupancy",
    "tensor_active",
    "power_usage",
})
# Module / SysIO power is duplicated across GPU entities on superchips; do not sum.
_NVIDIA_GPU_MAX_EVENTS = frozenset({
    "module_power_usage",
    "sysio_power_usage",
})
_NVIDIA_GPU_MEAN_EVENTS = frozenset({"temperature"})
_NVIDIA_GPU_OR_EVENTS = frozenset({"clocks_event_reasons"})

# Grace DCGM CPU power: same watt reading on every logical CPU row per socket.
_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS = frozenset({
    "DCGM_CPU_POWER_UTIL_W",
    "DCGM_CPU_POWER_LIMIT_W",
})

_COLLAPSE_GROUP_COLS = ["host", "type", "event", "unit", "time"]
# Pandas groupby.apply passes subframes without the grouping columns; event is in ``group.name``.
_NVIDIA_GROUP_KEY_EVENT_INDEX = _COLLAPSE_GROUP_COLS.index("event")


def _cluster_mean_sum_sorted(values, gap_threshold):
  """Cluster sorted finite samples and sum cluster means (socket-level gauges).

  Used when each socket repeats the same reading on many logical CPUs: clusters
  are separated by a gap larger than ``gap_threshold``. If two sockets report
  identical wattage, they form one cluster and this **underestimates** (rare).
  """
  v = np.asarray(values, dtype=np.float64)
  v = v[np.isfinite(v)]
  if v.size == 0:
    return float("nan")
  v.sort()
  total = 0.0
  cluster = [float(v[0])]
  for i in range(1, v.size):
    if v[i] - v[i - 1] <= gap_threshold:
      cluster.append(float(v[i]))
    else:
      total += float(np.mean(cluster))
      cluster = [float(v[i])]
  total += float(np.mean(cluster))
  return total


def _collapse_dcg_cpu_power_gauge_group(group):
  """Collapse per-CPU Grace DCGM power rows to one host sample (sum of sockets)."""
  vals = group["value"].to_numpy(dtype=np.float64, copy=False)
  dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
  vtot = _cluster_mean_sum_sorted(vals, 1.0)
  d_gap = 1e-6
  if np.any(np.isfinite(dvals)):
    dabs = np.nanmax(np.abs(dvals[np.isfinite(dvals)]))
    if np.isfinite(dabs) and dabs > 0:
      d_gap = max(1e-9, 0.05 * float(dabs))
  dtot = _cluster_mean_sum_sorted(dvals, d_gap)
  return pd.Series({"value": vtot, "delta": dtot})


def _collapse_nvidia_gpu_group(group):
  """Return one row (value, delta) for a (host, type, event, unit, time) group."""
  key = group.name
  event_name = key[_NVIDIA_GROUP_KEY_EVENT_INDEX] if isinstance(key, tuple) else key
  if event_name in _NVIDIA_GPU_MAX_EVENTS:
    return pd.Series({
        "value": float(group["value"].max()),
        "delta": group["delta"].mean(),
    })
  if event_name in _NVIDIA_GPU_SUM_EVENTS:
    return pd.Series({
        "value": group["value"].sum(min_count=1),
        "delta": group["delta"].sum(min_count=1),
    })
  if event_name in _NVIDIA_GPU_MEAN_EVENTS:
    return pd.Series({
        "value": group["value"].mean(),
        "delta": group["delta"].mean(),
    })
  if event_name in _NVIDIA_GPU_OR_EVENTS:
    acc = 0
    mask64 = (1 << 64) - 1
    for v in group["value"]:
      if pd.notna(v):
        acc |= int(v) & mask64
    return pd.Series({
        "value": float(acc & mask64),
        "delta": group["delta"].sum(min_count=1),
    })
  return pd.Series({
      "value": group["value"].sum(min_count=1),
      "delta": group["delta"].sum(min_count=1),
  })

EVENTMAPS_BY_TYPE = {
    "amd64_pmc": amd64_pmc_eventmap,
    "amd64_df": amd64_df_eventmap,
    "intel_8pmc3": intel_8pmc3_eventmap,
    # Same CTL/fixed layout and event programming family as intel_8pmc3.
    "intel_4pmc3": intel_8pmc3_eventmap,
    # Intel IMC / KNL MC: expose CAS_READS/CAS_WRITES everywhere so
    # INTEL_IMC_STATS_TYPES can treat generations uniformly.
    "intel_snb_imc": intel_snb_imc_eventmap,
    "intel_ivb_imc": intel_ivb_imc_eventmap,
    "intel_hsw_imc": intel_hsw_imc_eventmap,
    "intel_bdw_imc": intel_bdw_imc_eventmap,
    "intel_knl_mc_dclk": intel_knl_mc_dclk_eventmap,
    "intel_skx_imc": intel_skx_imc_eventmap,
}


def parse_stats_file_path(stats_file):
  """Parse stats file path into (hostname, create_time). Path is expected as '.../hostname/create_time'."""
  parts = stats_file.split('/')
  if len(parts) >= 2:
    return parts[-2], parts[-1]
  return None, None


def load_stats_file_lines(stats_file, stats_file_contents=None):
  """Load stats file as list of lines. Uses stats_file_contents if provided, else reads from disk. Returns (lines, error_msg). error_msg is None on success."""
  if stats_file_contents is not None:
    return stats_file_contents, None
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, 'r') as fd:
        return fd.readlines(), None
  except FileNotFoundError:
    return None, "Stats file disappeared: %s" % stats_file
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass


def parse_first_timestamp_line(lines):
  """Find first line that starts with a digit and parse as 't jid host'. Returns (t, jid, host) or (None, None, None)."""
  for l in lines:
    if not l:
      continue
    try:
      s = l.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        t, jid, host = s.split()
        return (t, jid, host)
    except Exception:
      pass
  return (None, None, None)


def find_processing_start_index(lines, itimes_set):
  """Find index where processing should start and whether raw file still needs archival.

  ``itimes_set`` contains Unix-second timestamps already present in DB.
  Returns ``(start_idx, need_archival)`` where:
  - ``start_idx`` is ``-1`` when all timestamps are already in DB.
  - ``need_archival`` is ``True`` whenever the file should still be archived
    after this ingest pass (including partially-new files and fully-duplicate
    files that still exist on disk).
  """
  start_idx = -1
  last_idx = 0
  need_archival = True
  for i, line in enumerate(lines):
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      t, _jid, _host = s.split()
      if int(float(t)) not in itimes_set:
        start_idx = last_idx
        need_archival = True
        break
      last_idx = i
  return start_idx, need_archival


def map_hardware_counter_vals(typ, schema_events, vals, eventmap):
  """Map raw hardware counter values to event names using schema and eventmap. Returns dict event -> value."""
  n = {}
  rm_idx = []
  schema_mod = []
  for idx, eve in enumerate(schema_events):
    eve = eve.split(',')[0]
    if "CTL" in eve:
      try:
        n[eve.lstrip("CTL")] = eventmap[int(vals[idx])]
      except Exception:
        n[eve.lstrip("CTL")] = "OTHER"
      rm_idx.append(idx)
    elif "FIXED_CTR" in eve:
      schema_mod.append(eventmap[eve])
    elif "CTR" in eve:
      schema_mod.append(n[eve.lstrip("CTR")])
    else:
      schema_mod.append(eve)
  for idx in sorted(rm_idx, reverse=True):
    del vals[idx]
  return dict(zip(schema_mod, vals))


def parse_stats_lines(lines, start_idx, eventmaps_by_type=None, exclude_types_list=None):
  """Parse stats and proc_stats from lines starting at start_idx. Returns (stats_list, proc_stats_list).
  eventmaps_by_type: dict typ -> eventmap for hardware counters. exclude_types_list: types to skip."""
  eventmaps_by_type = eventmaps_by_type or EVENTMAPS_BY_TYPE
  exclude_types_list = exclude_types_list if exclude_types_list is not None else exclude_types

  schema = {}
  stats = []
  proc_stats = []
  insert = False
  # Digit lines set tags for the following alpha lines; same-iteration F841
  # false positives if plain locals are used across loop iterations.
  line_ctx = {"tags": None, "tags2": None}

  for i, line in enumerate(lines):
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue

    if s[0].isalpha() and insert:
      typ, dev, vals = s.split(maxsplit=2)
      vals = vals.split()
      if typ in exclude_types_list:
        continue

      if typ in eventmaps_by_type:
        # If we see a hardware-counter type without a preceding schema line
        # (e.g. corrupted or truncated file), skip it instead of raising KeyError.
        if typ not in schema:
          continue
        eventmap = eventmaps_by_type[typ]
        vals = map_hardware_counter_vals(typ, schema[typ], vals, eventmap)
      elif typ == "proc":
        proc_name = (s.split()[1]).split('/')[0]
        proc_stats.append({**line_ctx["tags2"], "proc": proc_name})
        continue
      else:
        if typ in schema:
          vals = dict(zip(schema[typ], vals))
        else:
          continue

      rec = {**line_ctx["tags"], "type": typ, "dev": dev}
      for eve, val in vals.items():
        eve_parts = eve.split(',')
        width = 64
        mult = 1
        unit = "#"
        for ele in eve_parts[1:]:
          if "W=" in ele:
            width = int(ele.lstrip("W="))
          if "U=" in ele:
            ele = ele.lstrip("U=")
            try:
              mult = float(''.join(filter(str.isdigit, ele)))
            except Exception:
              pass
            try:
              unit = ''.join(filter(str.isalpha, ele))
            except Exception:
              pass
        stats.append({
            **rec, "event": eve_parts[0],
            "value": float(val),
            "wid": width,
            "mult": mult,
            "unit": unit
        })

    elif i >= start_idx and s[0].isdigit():
      t, jid, host = s.split()
      # host_data stats are time/host scoped (ORM has no jid on host_data).
      # Do not put jid on stats rows so compute_deltas_and_arc groupby stays
      # stable and '-' placeholders are not carried into host_data paths.
      # proc_stats lines still need jid (including '-').
      insert = True
      line_ctx["tags"] = {"time": float(t), "host": host}
      line_ctx["tags2"] = {"time": float(t), "host": host, "jid": jid}
    elif s[0] == '!':
      label, events = s.split(maxsplit=1)
      typ, events = label[1:], events.split()
      schema[typ] = events

  return stats, proc_stats


def build_stats_dataframes(stats_list, proc_stats_list):
  """Build deduplicated DataFrames from parsed stats and proc_stats lists. Returns (stats_df, proc_stats_df)."""
  proc_stats_df = DataFrame.from_records(proc_stats_list).drop_duplicates()
  stats_df = DataFrame.from_records(stats_list)
  return stats_df, proc_stats_df


def compute_deltas_and_arc(stats_df):
  """Compute delta and arc columns from value and time; returns tz-aware time.

  Rows with a valid ``value`` are kept even when ``delta``/``arc`` are NaN (first
  sample per host/type/event), so cumulative counters remain in ``host_data``
  for complex metrics that pivot on ``value``.
  """
  stats_df = stats_df.copy()

  # If stats_df is empty or missing expected columns (e.g. when no usable
  # stats lines were parsed), return an empty DataFrame with the expected
  # output schema instead of raising KeyError during groupby.
  required_cols = {
      "host", "type", "dev", "event", "unit", "time", "value", "wid", "mult"
  }
  if stats_df.empty or not required_cols.issubset(stats_df.columns):
    return DataFrame(columns=[
        "time", "host", "type", "dev", "event", "unit", "value", "delta", "arc"
    ])

  stats_df["delta"] = (
      stats_df.groupby(["host", "type", "dev", "event"])["value"].diff())
  stats_df["delta"] = stats_df["delta"].mask(
      stats_df["delta"] < 0, 2**stats_df["wid"] + stats_df["delta"])
  stats_df["delta"] = stats_df["delta"] * stats_df["mult"]
  stats_df.drop(columns=["wid", "mult"], inplace=True)

  gcols = _COLLAPSE_GROUP_COLS
  nv_mask = stats_df["type"] == "nvidia_gpu"
  nv_df = stats_df[nv_mask]
  rest_df = stats_df[~nv_mask]
  parts = []
  if not rest_df.empty:
    ccm_power_mask = (
        (rest_df["type"] == "cpu_counter_metrics")
        & rest_df["event"].isin(_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS))
    ccm_power_df = rest_df[ccm_power_mask]
    rest_other = rest_df[~ccm_power_mask]
    if not rest_other.empty:
      # Non-nvidia types (cpu, mem/NUMA, IB, IMC, …): sum across dev is intentional for counters.
      parts.append(
          rest_other.groupby(gcols, observed=True).sum(min_count=1).reset_index()
      )
    if not ccm_power_df.empty:
      ccm_collapsed = ccm_power_df.groupby(
          gcols, observed=True).apply(_collapse_dcg_cpu_power_gauge_group)
      ccm_collapsed = ccm_collapsed.reset_index()
      parts.append(ccm_collapsed)
  if not nv_df.empty:
    nv_collapsed = nv_df.groupby(gcols, observed=True).apply(
        _collapse_nvidia_gpu_group,
    )
    nv_collapsed = nv_collapsed.reset_index()
    parts.append(nv_collapsed)

  if not parts:
    stats_df = DataFrame(columns=gcols + ["value", "delta"])
  else:
    stats_df = concat(parts, ignore_index=True)

  stats_df = stats_df.sort_values(by=["host", "type", "event", "time"])
  deltat = stats_df.groupby(["host", "type", "event"])["time"].diff()
  # Rate is undefined for non-positive Δt (duplicate timestamps, clock skew). A
  # negative Δt with positive Δ(counter) yields a spurious negative arc (e.g. IB MB/s).
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)
  stats_df["arc"] = _arc
  stats_df["time"] = to_datetime(stats_df["time"], unit='s').dt.tz_localize('UTC')
  stats_df = stats_df.dropna(subset=["host", "type", "event", "time", "value"])
  return stats_df
