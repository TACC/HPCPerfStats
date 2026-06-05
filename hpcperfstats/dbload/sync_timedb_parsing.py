"""Pure parsing helpers for stats files (no Django). Used by sync_timedb and by unit tests."""
import os
import numpy as np
import pandas as pd
from pandas import DataFrame, concat, to_datetime

from hpcperfstats.dbload import sync_timedb_parsing_legacy as legacy_parsing
from hpcperfstats.file_locking import LOCK_SUFFIX, file_read_lock_wait
from hpcperfstats.monitor_naming.canonical import (
    DCGM_CPU_POWER_LIMIT_W,
    DCGM_CPU_POWER_UTIL_W,
    HOST_CPU_HW_TYPE,
)
from hpcperfstats.monitor_naming.legacy import LEGACY_HOST_CPU_HW_TYPE
from hpcperfstats.monitor_naming.resolve import schema_needs_legacy_hardware_decode

# Types skipped on ingest (canonical monitor names).
exclude_types = [
    "host_ib",
    "host_ib_sw",
    "intel_x86_uncore_cha_skx",
    "host_ps",
    "host_sysv_shm",
    "host_tmpfs",
    "host_vfs",
    # Legacy archives may still use old schema labels.
    "ib",
    "ib_sw",
    "intel_skx_cha",
    "ps",
    "sysv_shm",
    "tmpfs",
    "vfs",
]

# Back-compat re-export for callers/tests that referenced legacy eventmaps.
EVENTMAPS_BY_TYPE = legacy_parsing.EVENTMAPS_BY_TYPE
map_hardware_counter_vals = legacy_parsing.map_hardware_counter_vals

_NVIDIA_GPU_SUM_EVENTS = frozenset({
    "gpu_util",
    "gpu_io_link_total_bytes",
    "mem_util",
    "mem_used_mb",
    "mem_total_mb",
    "gpu_mem_util",
    "gpu_mem_used_mb",
    "gpu_mem_total_mb",
    "fp64_active",
    "fp32_active",
    "fp16_active",
    "sm_active",
    "sm_occupancy",
    "tensor_active",
    "power_usage",
})
_NVIDIA_GPU_MAX_EVENTS = frozenset({
    "module_power_usage",
    "sysio_power_usage",
})
_NVIDIA_GPU_MEAN_EVENTS = frozenset({"temperature"})
_NVIDIA_GPU_OR_EVENTS = frozenset({"clocks_event_reasons"})

_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS = frozenset({
    DCGM_CPU_POWER_UTIL_W,
    DCGM_CPU_POWER_LIMIT_W,
    "DCGM_CPU_POWER_UTIL_W",
    "DCGM_CPU_POWER_LIMIT_W",
})

_HOST_CPU_HW_TYPES = frozenset({HOST_CPU_HW_TYPE, LEGACY_HOST_CPU_HW_TYPE})

_COLLAPSE_GROUP_COLS = ["host", "type", "event", "unit", "time"]
_NVIDIA_GROUP_KEY_EVENT_INDEX = _COLLAPSE_GROUP_COLS.index("event")


def _cluster_mean_sum_sorted(values, gap_threshold):
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


def _vals_dict_from_line(typ, schema, vals, use_legacy_decode):
  if use_legacy_decode:
    decoded = legacy_parsing.decode_counter_line(typ, schema, vals)
    if decoded is None:
      return None
    return decoded
  if typ not in schema:
    return None
  return dict(zip(schema[typ], vals))


def _append_stats_rows(stats, rec, vals_dict):
  for eve, val in vals_dict.items():
    eve_parts = eve.split(",")
    width = 64
    mult = 1
    unit = "#"
    for ele in eve_parts[1:]:
      if "W=" in ele:
        width = int(ele.lstrip("W="))
      if "U=" in ele:
        ele = ele.lstrip("U=")
        try:
          mult = float("".join(filter(str.isdigit, ele)))
        except Exception:
          pass
        try:
          unit = "".join(filter(str.isalpha, ele))
        except Exception:
          pass
    stats.append({
        **rec,
        "event": eve_parts[0],
        "value": float(val),
        "wid": width,
        "mult": mult,
        "unit": unit,
    })


def parse_stats_file_path(stats_file):
  parts = stats_file.split("/")
  if len(parts) >= 2:
    return parts[-2], parts[-1]
  return None, None


def load_stats_file_lines(stats_file, stats_file_contents=None):
  if stats_file_contents is not None:
    return stats_file_contents, None
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
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


def parse_stats_lines(lines, start_idx, eventmaps_by_type=None, exclude_types_list=None):
  """Parse stats and proc_stats from lines starting at start_idx.

  Legacy archives (CTL/CTR or legacy st_name) use sync_timedb_parsing_legacy.
  eventmaps_by_type is ignored (kept for API compat); detection is automatic.
  """
  del eventmaps_by_type  # noqa: F841 — auto-detect legacy vs canonical
  exclude_types_list = exclude_types_list if exclude_types_list is not None else exclude_types

  schema = {}
  stats = []
  proc_stats = []
  insert = False
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

      if typ in ("proc", "host_proc"):
        proc_name = (s.split()[1]).split("/")[0]
        proc_stats.append({**line_ctx["tags2"], "proc": proc_name})
        continue

      if typ not in schema:
        continue

      use_legacy = schema_needs_legacy_hardware_decode(typ, schema[typ])
      vals_dict = _vals_dict_from_line(typ, schema, vals, use_legacy)
      if vals_dict is None:
        continue

      out_typ = legacy_parsing.legacy_output_type(typ) if use_legacy else typ
      rec = {**line_ctx["tags"], "type": out_typ, "dev": dev}
      _append_stats_rows(stats, rec, vals_dict)

    elif i >= start_idx and s[0].isdigit():
      t, jid, host = s.split()
      insert = True
      line_ctx["tags"] = {"time": float(t), "host": host}
      line_ctx["tags2"] = {"time": float(t), "host": host, "jid": jid}
    elif s[0] == "!":
      label, events = s.split(maxsplit=1)
      typ, events = label[1:], events.split()
      schema[typ] = events

  return stats, proc_stats


def build_stats_dataframes(stats_list, proc_stats_list):
  proc_stats_df = DataFrame.from_records(proc_stats_list).drop_duplicates()
  stats_df = DataFrame.from_records(stats_list)
  return stats_df, proc_stats_df


def compute_deltas_and_arc(stats_df):
  stats_df = stats_df.copy()

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
        rest_df["type"].isin(_HOST_CPU_HW_TYPES)
        & rest_df["event"].isin(_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS))
    ccm_power_df = rest_df[ccm_power_mask]
    rest_other = rest_df[~ccm_power_mask]
    if not rest_other.empty:
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
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)
  stats_df["arc"] = _arc
  stats_df["time"] = to_datetime(stats_df["time"], unit="s").dt.tz_localize("UTC")
  stats_df = stats_df.dropna(subset=["host", "type", "event", "time", "value"])
  return stats_df
