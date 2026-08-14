"""
Pure parsing helpers for stats files (no Django). Used by sync_timedb and by
unit tests.

Attributes:
  EVENTMAPS_BY_TYPE: Attribute.
  HOST_PROC_KEYS: Attribute.
  HOST_PROC_PEAK_KEYS: Attribute.
  STREAM_PARSE_LINE_BATCH: Attribute.
  _ARC_GROUP_COLS: Attribute.
  _COLLAPSE_GROUP_COLS: Attribute.
  _COLLAPSE_GROUP_COLS_WITH_DEV: Attribute.
  _COUNTER_GROUP_COLS: Attribute.
  _DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS: Attribute.
  _EMPTY_DELTA_ARC_COLUMNS: Attribute.
  _GPU_STATS_TYPES: Attribute.
  _HOST_CPU_HW_TYPES: Attribute.
  _NVIDIA_GPU_KNOWN_EVENTS: Attribute.
  _NVIDIA_GPU_MAX_EVENTS: Attribute.
  _NVIDIA_GPU_MEAN_EVENTS: Attribute.
  _NVIDIA_GPU_OR_EVENTS: Attribute.
  _NVIDIA_GPU_SUM_EVENTS: Attribute.
  _NVIDIA_GROUP_KEY_EVENT_INDEX: Attribute.
  _READ_LOOP_DEADLINE_EVERY_BYTES: Attribute.
  _READ_LOOP_DEADLINE_EVERY_LINES: Attribute.
  _SLOW_TIER_OPT: Attribute.
  _TIER_MARKERS: Attribute.
  exclude_types: Attribute.
  map_hardware_counter_vals: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import os
import warnings
import numpy as np
import pandas as pd
from pandas import DataFrame, concat, to_datetime

from hpcperfstats.dbload.lib import sync_timedb_parsing_legacy as legacy_parsing
from hpcperfstats.dbload.lib.file_locking import LOCK_SUFFIX, file_read_lock_wait
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    DCGM_CPU_POWER_LIMIT_W,
    DCGM_CPU_POWER_UTIL_W,
    HOST_CPU_HW_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.legacy import LEGACY_HOST_CPU_HW_TYPE
from hpcperfstats.dbload.lib.monitor_naming.resolve import schema_needs_legacy_hardware_decode
from hpcperfstats.lib.dcgm_blank import (
    DCGM_FP64_BLANK,
    is_dcgm_numeric_blank,
    nan_out_dcgm_numeric_blanks,
)

# Types skipped on ingest (canonical monitor names).
exclude_types = [
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

# Default host_proc KEYS matching monitor/src/proc.c. Proc-field ingest is a
# T0 smoke contract (docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md), not a stall fix.
HOST_PROC_KEYS = (
    "uid",
    "vm_peak",
    "vm_size",
    "vm_lck",
    "vm_hwm",
    "vm_rss",
    "vm_data",
    "vm_stk",
    "vm_exe",
    "vm_lib",
    "vm_pte",
    "vm_swap",
    "threads",
)

# Instantaneous gauges and kernel peaks retained as high-water marks across
# samples / upserts for the same ``(jid, host, proc)`` name. Includes kernel
# VmPeak/VmHWM so a later zero or lower sample (or new PID same name) cannot
# erase the job-level high water.
HOST_PROC_PEAK_KEYS = frozenset({
    "vm_peak",
    "vm_hwm",
    "vm_stk",
    "vm_exe",
    "vm_lib",
})


def schema_key_basename(token: str) -> str:
  """
  Strip monitor schema option suffixes (``,U=kB``, ``,E``, …).

  Args:
    token (str): Full schema entry from a ``!host_proc`` line (or bare key).

  Returns:
    str: Key basename before the first comma (empty string when ``token`` empty).

  Examples:
    >>> schema_key_basename("vm_peak,U=kB")
    'vm_peak'
    >>> schema_key_basename("uid")
    'uid'
    >>> schema_key_basename("vm_rss,E,U=kB")
    'vm_rss'
  """
  if not token:
    return ""
  return token.split(",", 1)[0]


def _nullable_int_max(left: Any, right: Any) -> Any:
  """
  Return the greater of two nullable integer-like values.

  Args:
    left (Any): First candidate (``None`` or int-like).
    right (Any): Second candidate (``None`` or int-like).

  Returns:
    Any: ``None`` when both missing; otherwise the max of convertible ints,
    or the sole non-``None`` side when the other cannot convert.

  Examples:
    >>> _nullable_int_max(None, 5)
    5
    >>> _nullable_int_max(10, 3)
    10
    >>> _nullable_int_max(None, None) is None
    True
  """
  left_ok: int | None
  right_ok: int | None
  try:
    left_ok = None if left is None else int(left)
  except (TypeError, ValueError):
    left_ok = None
  try:
    right_ok = None if right is None else int(right)
  except (TypeError, ValueError):
    right_ok = None
  if left_ok is None:
    return right_ok
  if right_ok is None:
    return left_ok
  return max(left_ok, right_ok)


def merge_proc_row_dicts(
    earlier: dict[str, Any],
    later: dict[str, Any],
) -> dict[str, Any]:
  """
  Merge two host_proc row dicts for the same ``(jid, host, proc)``.

  Non-peak fields take ``later`` (last-write). Peak keys
  (``HOST_PROC_PEAK_KEYS``) take the max of non-null values.

  Args:
    earlier (dict[str, Any]): Prior sample for the unique key.
    later (dict[str, Any]): Newer sample (last-write source).

  Returns:
    dict[str, Any]: Merged row (new dict); peak fields are GREATEST.

  Examples:
    >>> merge_proc_row_dicts(
    ...     {"vm_stk": 100, "vm_peak": 900, "threads": 1},
    ...     {"vm_stk": 50, "vm_peak": 800, "threads": 4},
    ... )["vm_stk"]
    100
    >>> merge_proc_row_dicts(
    ...     {"vm_stk": 100, "vm_peak": 900, "threads": 1},
    ...     {"vm_stk": 50, "vm_peak": 800, "threads": 4},
    ... )["vm_peak"]
    900
    >>> merge_proc_row_dicts(
    ...     {"vm_hwm": 7000, "threads": 1},
    ...     {"vm_hwm": 0, "threads": 4},
    ... )["vm_hwm"]
    7000
  """
  out = dict(earlier)
  out.update(later)
  for key in HOST_PROC_PEAK_KEYS:
    out[key] = _nullable_int_max(earlier.get(key), later.get(key))
  return out


def dedupe_proc_stats_peak_merge(
    proc_stats_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  """
  Collapse duplicate ``(jid, host, proc)`` rows with peak-aware merge.

  Args:
    proc_stats_list (list[dict[str, Any]]): Parsed host_proc rows in time order.

  Returns:
    list[dict[str, Any]]: One row per unique key; peaks retained across samples.

  Examples:
    >>> rows = dedupe_proc_stats_peak_merge([
    ...     {"jid": "j", "host": "h", "proc": "p", "vm_stk": 9, "threads": 1},
    ...     {"jid": "j", "host": "h", "proc": "p", "vm_stk": 3, "threads": 8},
    ... ])
    >>> rows[0]["vm_stk"], rows[0]["threads"]
    (9, 8)
  """
  by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
  for row in proc_stats_list:
    key = (row.get("jid"), row.get("host"), row.get("proc"))
    if key in by_key:
      by_key[key] = merge_proc_row_dicts(by_key[key], row)
    else:
      by_key[key] = dict(row)
  return list(by_key.values())


def apply_proc_peak_attrs_from_earlier(earlier: Any, later: Any) -> Any:
  """
  Copy GREATEST peak attrs from ``earlier`` onto ``later`` (ORM or namespace).

  Args:
    earlier (Any): Prior object with optional peak KEYS attrs
      (``vm_peak`` / ``vm_hwm`` / ``vm_stk`` / ``vm_exe`` / ``vm_lib``).
    later (Any): Incoming object mutated in place (last-write for other fields).

  Returns:
    Any: The ``later`` object after peak fields are raised when needed.

  Examples:
    >>> from types import SimpleNamespace
    >>> a = SimpleNamespace(vm_peak=9000, vm_hwm=7000, vm_stk=10, vm_exe=1, vm_lib=2)
    >>> b = SimpleNamespace(vm_peak=0, vm_hwm=100, vm_stk=3, vm_exe=9, vm_lib=None)
    >>> apply_proc_peak_attrs_from_earlier(a, b).vm_peak
    9000
    >>> b.vm_hwm
    7000
    >>> b.vm_stk
    10
    >>> b.vm_exe
    9
  """
  for key in HOST_PROC_PEAK_KEYS:
    cur = getattr(later, key, None)
    prev = getattr(earlier, key, None)
    setattr(later, key, _nullable_int_max(prev, cur))
  return later

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
    # Node GPU count is emitted on every device row; MAX avoids N×N when
    # identity collapses without a distinct ``dev`` (legacy / empty-dev path).
    "gpu_count",
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
_GPU_STATS_TYPES = frozenset({"nvidia_gpu", "amd_gpu", "intel_gpu"})

# Non-GPU collapse drops device identity (sum across ``dev``).
_COLLAPSE_GROUP_COLS = ["host", "type", "event", "unit", "time"]
# GPU types keep monitor ``dev`` so Job Detail can inventorize per device.
_COLLAPSE_GROUP_COLS_WITH_DEV = ["host", "type", "dev", "event", "unit", "time"]
_COUNTER_GROUP_COLS = ["host", "type", "dev", "event"]
# Arc continuity must match counter grain (include ``dev`` for multi-GPU).
_ARC_GROUP_COLS = ["host", "type", "dev", "event"]
_NVIDIA_GROUP_KEY_EVENT_INDEX = _COLLAPSE_GROUP_COLS_WITH_DEV.index("event")

_SLOW_TIER_OPT = "R=S"
_TIER_MARKERS = frozenset({"@fast", "@full"})


def _schema_token_is_slow_tier(token: str) -> bool:
  """
  True when a schema entry is marked slow-tier via ,R=S (monitor two-tier.
  
    collect).
  
  Args:
    token (str): String for token.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _schema_token_is_slow_tier("x")  # doctest: +SKIP
  """
  return _SLOW_TIER_OPT in token.split(",")[1:]


def _fast_schema_keys(full_events: list[str]) -> list[str]:
  """
  Fast-tier schema keys in order (entries without ,R=S).
  
  Args:
    full_events (list[str]): Sequence for full events.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _fast_schema_keys([])  # doctest: +SKIP
  """
  return [e for e in full_events if not _schema_token_is_slow_tier(e)]


def _zip_schema_vals(
  schema_keys: Any,
  vals: Any,
  typ: Any | None = None,
  dev: Any | None = None,
) -> Any:
  """
  Zip value tokens to schema keys; None when counts disagree (no silent.
  
    truncation).
  
  Args:
    schema_keys (Any): Schema keys passed to this helper.
    vals (Any): Vals passed to this helper.
    typ (Any | None): One of ``Any``, ``None``.
    dev (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _zip_schema_vals(None, None, None, None)  # doctest: +SKIP
  """
  if len(vals) != len(schema_keys):
    warnings.warn(
        "stats line value count %d != schema key count %d for type=%s dev=%s"
        % (len(vals), len(schema_keys), typ, dev),
        stacklevel=3,
    )
    return None
  return dict(zip(schema_keys, vals))


def _cluster_mean_sum_sorted(values: Any, gap_threshold: Any) -> Any:
  """
  Internal helper to handle cluster mean sum sorted.
  
  Args:
    values (Any): Values passed to this helper.
    gap_threshold (Any): Gap threshold passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cluster_mean_sum_sorted(None, None)  # doctest: +SKIP
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


def _dcg_delta_gap_threshold(dvals: Any) -> Any:
  """
  Dynamic delta clustering gap for DCGM CPU power gauge collapse.
  
  Args:
    dvals (Any): Dvals passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _dcg_delta_gap_threshold(None)  # doctest: +SKIP
  """
  d_gap = 1e-6
  finite = dvals[np.isfinite(dvals)]
  if finite.size == 0:
    return d_gap
  dabs = np.nanmax(np.abs(finite))
  if np.isfinite(dabs) and dabs > 0:
    d_gap = max(1e-9, 0.05 * float(dabs))
  return d_gap


def _collapse_dcg_cpu_power_gauge_group(group: Any) -> Any:
  """
  Apply-reference DCGM collapse; production path uses vectorized helper.
  
  Args:
    group (Any): Group passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collapse_dcg_cpu_power_gauge_group(None)  # doctest: +SKIP
  """
  vals = group["value"].to_numpy(dtype=np.float64, copy=False)
  dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
  vtot = _cluster_mean_sum_sorted(vals, 1.0)
  dtot = _cluster_mean_sum_sorted(dvals, _dcg_delta_gap_threshold(dvals))
  return pd.Series({"value": vtot, "delta": dtot})


def _collapse_dcg_cpu_power_vectorized(ccm_df: Any, gcols: Any) -> Any:
  """
  Collapse DCGM CPU power gauges via explicit group loop (not groupby.apply).
  
  Args:
    ccm_df (Any): Ccm df passed to this helper.
    gcols (Any): Gcols passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collapse_dcg_cpu_power_vectorized(None, None)  # doctest: +SKIP
  """
  rows = []
  for key, group in ccm_df.groupby(gcols, observed=True, sort=False):
    vals = group["value"].to_numpy(dtype=np.float64, copy=False)
    dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
    key_tuple = key if isinstance(key, tuple) else (key,)
    row = dict(zip(gcols, key_tuple))
    row["value"] = _cluster_mean_sum_sorted(vals, 1.0)
    row["delta"] = _cluster_mean_sum_sorted(dvals, _dcg_delta_gap_threshold(dvals))
    if "jid" in group.columns and len(group):
      row["jid"] = group["jid"].iloc[0]
    rows.append(row)
  if not rows:
    return _empty_delta_arc_frame()
  return DataFrame(rows)


def _collapse_nvidia_gpu_group(group: Any) -> Any:
  """
  Apply-reference NVIDIA collapse; production path uses vectorized helper.
  
  Args:
    group (Any): Group passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collapse_nvidia_gpu_group(None)  # doctest: +SKIP
  """
  # Prefer the event column so this works with/without ``dev`` in the group key.
  if "event" in group.columns and len(group):
    event_name = group["event"].iloc[0]
  else:
    key = group.name
    event_name = (
        key[_NVIDIA_GROUP_KEY_EVENT_INDEX] if isinstance(key, tuple) else key
    )
  group = group.copy()
  group["value"] = nan_out_dcgm_numeric_blanks(group["value"].to_numpy(dtype=np.float64))
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
      if pd.notna(v) and not is_dcgm_numeric_blank(v):
        acc |= int(v) & mask64
    return pd.Series({
        "value": float(acc & mask64),
        "delta": group["delta"].sum(min_count=1),
    })
  return pd.Series({
      "value": group["value"].sum(min_count=1),
      "delta": group["delta"].sum(min_count=1),
  })


_NVIDIA_GPU_KNOWN_EVENTS = frozenset().union(
    _NVIDIA_GPU_SUM_EVENTS,
    _NVIDIA_GPU_MAX_EVENTS,
    _NVIDIA_GPU_MEAN_EVENTS,
    _NVIDIA_GPU_OR_EVENTS,
)


def _nvidia_bitwise_or_values(series: Any) -> Any:
  """
  Bitwise OR of finite non-blank ``clocks_event_reasons`` within one collapse.
  
    group.
  
  Args:
    series (Any): Series passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _nvidia_bitwise_or_values(None)  # doctest: +SKIP
  """
  acc = 0
  mask64 = (1 << 64) - 1
  for v in series:
    if pd.notna(v) and not is_dcgm_numeric_blank(v):
      acc |= int(v) & mask64
  return float(acc & mask64)


def _optional_jid_first_agg(df: Any) -> dict[str, tuple[str, str]]:
  """
  Return ``jid`` ``first`` agg kwargs when the column is present.

  Sample-header jobid is constant within a collapse group; do not add ``jid``
  to group keys (DB uniqueness remains time/host/type/event[/dev]).

  Args:
    df (Any): Stats frame that may include a ``jid`` column.

  Returns:
    dict[str, tuple[str, str]]: Empty or ``{"jid": ("jid", "first")}``.

  Examples:
    >>> _optional_jid_first_agg(DataFrame({"jid": ["1"]}))
    {'jid': ('jid', 'first')}
  """
  if "jid" in getattr(df, "columns", ()):
    return {"jid": ("jid", "first")}
  return {}


def _groupby_sum_min_count(df: Any, gcols: Any) -> Any:
  """
  Sum value/delta across devs with pandas ``sum(min_count=1)`` NaN semantics.
  
  Args:
    df (Any): Df passed to this helper.
    gcols (Any): Gcols passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _groupby_sum_min_count(None, None)  # doctest: +SKIP
  """
  if df.empty:
    return _empty_delta_arc_frame()
  grouped = df.groupby(gcols, observed=True).agg(
      value=("value", "sum"),
      delta=("delta", "sum"),
      _value_n=("value", "count"),
      _delta_n=("delta", "count"),
      **_optional_jid_first_agg(df),
  ).reset_index()
  grouped["value"] = grouped["value"].where(grouped["_value_n"] > 0)
  grouped["delta"] = grouped["delta"].where(grouped["_delta_n"] > 0)
  return grouped.drop(columns=["_value_n", "_delta_n"])


def _nvidia_nan_out_dcgm_blanks(nv_df: Any) -> Any:
  """
  Replace DCGM blank-family ``value`` entries with NaN before NVIDIA collapse.
  
  Args:
    nv_df (Any): Nv df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _nvidia_nan_out_dcgm_blanks(None)  # doctest: +SKIP
  """
  if nv_df.empty or "value" not in nv_df.columns:
    return nv_df
  vals = nv_df["value"].to_numpy(dtype=np.float64, copy=False)
  # FP64 blank base also excludes INT64 blank family (larger magnitude).
  blank = np.isfinite(vals) & (vals >= DCGM_FP64_BLANK)
  if not blank.any():
    return nv_df
  out = nv_df.copy()
  out["value"] = nan_out_dcgm_numeric_blanks(vals)
  return out


def _collapse_nvidia_gpu_vectorized(nv_df: Any, gcols: Any) -> Any:
  """
  Collapse NVIDIA GPU metrics via native groupby aggregations (not.
  
    groupby.apply).
  
  Args:
    nv_df (Any): Nv df passed to this helper.
    gcols (Any): Gcols passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collapse_nvidia_gpu_vectorized(None, None)  # doctest: +SKIP
  """
  nv_df = _nvidia_nan_out_dcgm_blanks(nv_df)
  parts = []
  sum_mask = (
      nv_df["event"].isin(_NVIDIA_GPU_SUM_EVENTS)
      | ~nv_df["event"].isin(_NVIDIA_GPU_KNOWN_EVENTS))
  sum_df = nv_df.loc[sum_mask]
  if not sum_df.empty:
    parts.append(_groupby_sum_min_count(sum_df, gcols))

  max_df = nv_df.loc[nv_df["event"].isin(_NVIDIA_GPU_MAX_EVENTS)]
  if not max_df.empty:
    parts.append(
        max_df.groupby(gcols, observed=True).agg(
            value=("value", "max"),
            delta=("delta", "mean"),
            **_optional_jid_first_agg(max_df),
        ).reset_index()
    )

  mean_df = nv_df.loc[nv_df["event"].isin(_NVIDIA_GPU_MEAN_EVENTS)]
  if not mean_df.empty:
    parts.append(
        mean_df.groupby(gcols, observed=True).agg(
            value=("value", "mean"),
            delta=("delta", "mean"),
            **_optional_jid_first_agg(mean_df),
        ).reset_index()
    )

  or_df = nv_df.loc[nv_df["event"].isin(_NVIDIA_GPU_OR_EVENTS)]
  if not or_df.empty:
    or_collapsed = or_df.groupby(gcols, observed=True).agg(
        value=("value", _nvidia_bitwise_or_values),
        delta=("delta", "sum"),
        _delta_n=("delta", "count"),
        **_optional_jid_first_agg(or_df),
    ).reset_index()
    or_collapsed["delta"] = or_collapsed["delta"].where(or_collapsed["_delta_n"] > 0)
    parts.append(or_collapsed.drop(columns=["_delta_n"]))

  if not parts:
    return _empty_delta_arc_frame()
  if len(parts) == 1:
    return parts[0]
  return concat(parts, ignore_index=True)


def _vals_dict_from_line(
  typ: Any,
  schema: Any,
  schema_keys: Any,
  vals: Any,
  use_legacy_decode: bool,
  dev: Any | None = None,
) -> Any:
  """
  Internal helper to handle vals dict from line.
  
  Args:
    typ (Any): Typ passed to this helper.
    schema (Any): Schema passed to this helper.
    schema_keys (Any): Schema keys passed to this helper.
    vals (Any): Vals passed to this helper.
    use_legacy_decode (bool): Whether to enable use legacy decode.
    dev (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _vals_dict_from_line(None, None, None, None, True, None)
  """
  if use_legacy_decode:
    decoded = legacy_parsing.decode_counter_line(typ, schema, vals)
    if decoded is None:
      return None
    return decoded
  return _zip_schema_vals(schema_keys, vals, typ=typ, dev=dev)


def _append_stats_rows(stats: Any, rec: Any, vals_dict: Any) -> None:
  """
  Internal helper to handle append stats rows.
  
  Args:
    stats (Any): Stats passed to this helper.
    rec (Any): Rec passed to this helper.
    vals_dict (Any): Vals dict passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _append_stats_rows(None, None, None)  # doctest: +SKIP
  """
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


def parse_stats_file_path(stats_file: str) -> Any:
  """
  Parse the stats file path.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_stats_file_path("x")  # doctest: +SKIP
  """
  parts = stats_file.split("/")
  if len(parts) >= 2:
    return parts[-2], parts[-1]
  return None, None


STREAM_PARSE_LINE_BATCH = 50000


def stats_file_size_bytes(stats_file: str) -> Any:
  """
  Return on-disk size in bytes (0 when missing or unreadable).
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> stats_file_size_bytes("x")  # doctest: +SKIP
  """
  try:
    return int(os.path.getsize(stats_file))
  except OSError:
    return 0


_READ_LOOP_DEADLINE_EVERY_LINES = 1000
_READ_LOOP_DEADLINE_EVERY_BYTES = 1 << 20


def _maybe_raise_ingest_read_deadline(line_idx: Any, bytes_read: Any) -> None:
  """
  Internal helper to handle maybe raise ingest read deadline.
  
  Args:
    line_idx (Any): Line idx passed to this helper.
    bytes_read (Any): Bytes read passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _maybe_raise_ingest_read_deadline(None, None)  # doctest: +SKIP
  """
  if line_idx and line_idx % _READ_LOOP_DEADLINE_EVERY_LINES == 0:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        _raise_if_ingest_deadline_exceeded,
    )

    _raise_if_ingest_deadline_exceeded()
  if bytes_read and bytes_read % _READ_LOOP_DEADLINE_EVERY_BYTES == 0:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        _raise_if_ingest_deadline_exceeded,
    )

    _raise_if_ingest_deadline_exceeded()


def load_stats_file_lines(
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Load the stats file lines.
  
  Args:
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> load_stats_file_lines("x", None)  # doctest: +SKIP
  """
  if stats_file_contents is not None:
    return stats_file_contents, None
  lines = []
  bytes_read = 0
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        line_idx = 0
        while True:
          line = fd.readline()
          if not line:
            break
          lines.append(line)
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
    return lines, None
  except FileNotFoundError:
    return None, "Stats file disappeared: %s" % stats_file
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass


def iter_stats_file_lines(stats_file: str) -> Iterator[Any]:
  """
  Yield lines from a stats file under the read lock (streaming).
  
  Args:
    stats_file (str): String for stats file.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_stats_file_lines("x")  # doctest: +SKIP
  """
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        line_idx = 0
        bytes_read = 0
        while True:
          line = fd.readline()
          if not line:
            break
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
          yield line
  except FileNotFoundError:
    return
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass


def _digit_line_identity(s: Any) -> Any:
  """
  Return ``(t, jid, host)`` from a digit-leading line, or ``None`` if malformed.
  
  Accepts extra trailing tokens (monitor lines may carry more than three
    fields).
  
  Args:
    s (Any): S passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _digit_line_identity(None)  # doctest: +SKIP
  """
  try:
    parts = s.split()
    if len(parts) < 3:
      return None
    return (parts[0], parts[1], parts[2])
  except (TypeError, ValueError, AttributeError):
    return None


def _digit_line_unix_second(s: Any) -> Any:
  """
  Return unix-second from a digit-leading line, or ``None`` if malformed.
  
  Args:
    s (Any): S passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _digit_line_unix_second(None)  # doctest: +SKIP
  """
  parsed = _digit_line_identity(s)
  if parsed is None:
    return None
  try:
    return int(float(parsed[0]))
  except (TypeError, ValueError):
    return None


def parse_first_timestamp_line(lines: Any) -> Any:
  """
  Parse the first timestamp line.
  
  Args:
    lines (Any): Lines passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_first_timestamp_line(None)  # doctest: +SKIP
  """
  for l in lines:
    if not l:
      continue
    try:
      s = l.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        parsed = _digit_line_identity(s)
        if parsed is None:
          continue
        return parsed
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line(lines: Any) -> Any:
  """
  Return last digit-leading stats line identity from an in-memory line list.
  
  Args:
    lines (Any): Lines passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_last_timestamp_line(None)  # doctest: +SKIP
  """
  for line in reversed(lines or ()):
    if not line:
      continue
    try:
      s = line.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        parsed = _digit_line_identity(s)
        if parsed is None:
          continue
        return parsed
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line_streaming(
  stats_file: str,
  *,
  tail_read_bytes: int = 65536,
) -> Any:
  """
  Return last digit-leading stats line identity without a full-file scan.
  
  Args:
    stats_file (str): String for stats file.
    tail_read_bytes (int): Integer value for tail read bytes.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_last_timestamp_line_streaming("x", 0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("parse:tail")
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return (None, None, None)
  if size <= 0:
    return (None, None, None)
  chunk_size = max(4096, int(tail_read_bytes))
  carry = b""
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "rb") as fd:
        offset = size
        while offset > 0:
          read_size = min(chunk_size, offset)
          offset -= read_size
          fd.seek(offset)
          block = fd.read(read_size) + carry
          parts = block.split(b"\n")
          if offset > 0:
            carry = parts[0]
            parts = parts[1:]
          else:
            carry = b""
          for raw in reversed(parts):
            if not raw:
              continue
            try:
              line = raw.decode("utf-8", errors="replace")
            except Exception:
              continue
            s = line.lstrip()
            if not s or not s[0].isdigit():
              continue
            parsed = _digit_line_identity(s)
            if parsed is not None:
              return parsed
  except FileNotFoundError:
    return (None, None, None)
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return (None, None, None)


def _timestamp_present_for_duplicate(
  itimes_set: Any,
  timestamp_present: Any,
  unix_second: Any,
) -> Any:
  """
  Internal helper to handle timestamp present for duplicate.
  
  Args:
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any): Timestamp present passed to this helper.
    unix_second (Any): Unix second passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _timestamp_present_for_duplicate(None, None, None)  # doctest: +SKIP
  """
  if timestamp_present is not None:
    return bool(timestamp_present(unix_second))
  return int(unix_second) in itimes_set


def find_processing_start_index(
  lines: Any,
  itimes_set: Any,
  timestamp_present: Any | None = None,
) -> Any:
  """
  Find the processing start index.
  
  Args:
    lines (Any): Lines passed to this helper.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> find_processing_start_index(None, None, None)  # doctest: +SKIP
  """
  start_idx = -1
  last_idx = 0
  need_archival = True
  for i, line in enumerate(lines):
    if i and i % 1000 == 0:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          _raise_if_ingest_deadline_exceeded,
      )
      from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
          update_worker_substage,
      )

      update_worker_substage("duplicate_scan_lines")
      _raise_if_ingest_deadline_exceeded()
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      unix_sec = _digit_line_unix_second(s)
      if unix_sec is None:
        continue
      if not _timestamp_present_for_duplicate(
          itimes_set, timestamp_present, unix_sec):
        start_idx = last_idx
        need_archival = True
        break
      last_idx = i
  return start_idx, need_archival


def find_processing_start_index_streaming(
  stats_file: str,
  itimes_set: Any,
  *,
  timestamp_present: Any | None = None,
) -> Any:
  """
  Scan a stats file without loading it into memory.
  
  Args:
    stats_file (str): String for stats file.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> find_processing_start_index_streaming("x", None, None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _raise_if_ingest_deadline_exceeded,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("duplicate_scan_streaming")
  start_idx = -1
  last_idx = 0
  line_idx = 0
  for line in iter_stats_file_lines(stats_file):
    if line_idx and line_idx % 1000 == 0:
      update_worker_substage("duplicate_scan_streaming")
      _raise_if_ingest_deadline_exceeded()
    if not line:
      line_idx += 1
      continue
    s = line.lstrip()
    if not s:
      line_idx += 1
      continue
    if s[0].isdigit():
      unix_sec = _digit_line_unix_second(s)
      if unix_sec is None:
        line_idx += 1
        continue
      if not _timestamp_present_for_duplicate(
          itimes_set, timestamp_present, unix_sec):
        start_idx = last_idx
        return start_idx, True
      last_idx = line_idx
    line_idx += 1
  return start_idx, True


def parse_first_timestamp_line_streaming(stats_file: str) -> Any:
  """
  Return first digit-leading stats line identity without ``readlines()``.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_first_timestamp_line_streaming("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("parse:head")
  for line in iter_stats_file_lines(stats_file):
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      parsed = _digit_line_identity(s)
      if parsed is not None:
        return parsed
  return (None, None, None)


def _collect_tail_timestamp_lines(
  stats_file: str,
  *,
  max_lines: Any,
  tail_read_bytes: int = 65536,
) -> Any:
  """
  Collect up to ``max_lines`` digit-leading lines from the file tail (newest.
  
    first).
  
  Args:
    stats_file (str): String for stats file.
    max_lines (Any): Max lines passed to this helper.
    tail_read_bytes (int): Integer value for tail read bytes.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collect_tail_timestamp_lines("x", None, 0)  # doctest: +SKIP
  """
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return []
  if size <= 0 or max_lines <= 0:
    return []
  chunk_size = max(4096, int(tail_read_bytes))
  carry = b""
  collected = []
  offset = size
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "rb") as fd:
        while offset > 0 and len(collected) < max_lines:
          read_size = min(chunk_size, offset)
          offset -= read_size
          fd.seek(offset)
          block = fd.read(read_size) + carry
          parts = block.split(b"\n")
          if offset > 0:
            carry = parts[0]
            parts = parts[1:]
          else:
            carry = b""
          for raw in reversed(parts):
            if len(collected) >= max_lines:
              break
            if not raw:
              continue
            try:
              line = raw.decode("utf-8", errors="replace")
            except Exception:
              continue
            s = line.lstrip()
            if not s or not s[0].isdigit():
              continue
            collected.append(line)
  except FileNotFoundError:
    return []
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return collected


def tail_window_timestamps_all_present_streaming(
  stats_file: str,
  itimes_set: Any,
  *,
  timestamp_present: Any | None = None,
  max_lines: Any | None = None,
) -> Any:
  """
  True when every timestamp in the tail window is already present in DB/cache.
  
  Args:
    stats_file (str): String for stats file.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.
    max_lines (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> tail_window_timestamps_all_present_streaming("x", None, None, None)
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _raise_if_ingest_deadline_exceeded,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  if max_lines is None:
    max_lines = cfg.get_sync_ingest_db_complete_tail_window_lines()
  update_worker_substage("parse:tail_window")
  lines = _collect_tail_timestamp_lines(stats_file, max_lines=max_lines)
  if not lines:
    return False
  for line in lines:
    _raise_if_ingest_deadline_exceeded()
    s = line.lstrip()
    if not s or not s[0].isdigit():
      continue
    unix_sec = _digit_line_unix_second(s)
    if unix_sec is None:
      continue
    if not _timestamp_present_for_duplicate(
        itimes_set, timestamp_present, unix_sec):
      return False
  return True


class IncrementalStatsParser:
  """
  Stateful parser for chunked/streaming stats-file ingest.
  
  Attributes:
    _line_index: Attribute.
    exclude_types_list: Attribute.
    insert: Attribute.
    line_ctx: Attribute.
    proc_stats: Attribute.
    schema: Attribute.
    schema_fast: Attribute.
    start_idx: Attribute.
    stats: Attribute.
  """

  def __init__(
    self,
    start_idx: int = 0,
    exclude_types_list: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      start_idx (int): Integer value for start idx.
      exclude_types_list (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> IncrementalStatsParser(0, None)  # doctest: +SKIP
    """
    self.start_idx = int(start_idx)
    self.exclude_types_list = (
        exclude_types_list if exclude_types_list is not None else exclude_types
    )
    self._line_index = 0
    self.schema = {}
    self.schema_fast = {}
    self.stats = []
    self.proc_stats = []
    self.insert = False
    self.line_ctx = {"tags": None, "tags2": None}

  def feed_line(self, line: Any) -> None:
    """
    Feed line.
    
    Args:
      line (Any): Line passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> IncrementalStatsParser().feed_line(None)  # doctest: +SKIP
    """
    i = self._line_index
    self._line_index += 1
    if not line:
      return
    s = line.lstrip()
    if not s:
      return

    if s[0].isalpha() and self.insert:
      typ, dev, vals = s.split(maxsplit=2)
      vals = vals.split()
      if typ in self.exclude_types_list:
        return

      if typ in ("proc", "host_proc"):
        # device = full monitor token (name/pid/cmask/mmask); proc = name only.
        proc_name = dev.split("/")[0]
        full_schema_keys = (
            self.schema.get(typ)
            or self.schema.get("host_proc")
            or self.schema.get("proc")
            or list(HOST_PROC_KEYS)
        )
        tier_marker = None
        if vals and vals[0] in _TIER_MARKERS:
          tier_marker = vals[0]
          vals = vals[1:]
        if tier_marker == "@fast":
          schema_keys = self.schema_fast.get(typ) or _fast_schema_keys(
              full_schema_keys
          )
        else:
          # ``@full`` or legacy lines without a tier marker use the full KEYS.
          schema_keys = full_schema_keys
        vals_by_key = {}
        for i, key in enumerate(schema_keys):
          if i >= len(vals):
            break
          raw = vals[i]
          bare = schema_key_basename(key)
          try:
            vals_by_key[bare] = int(raw)
          except (TypeError, ValueError):
            try:
              vals_by_key[bare] = int(float(raw))
            except (TypeError, ValueError):
              vals_by_key[bare] = None
        row = {
            **self.line_ctx["tags2"],
            "proc": proc_name,
            "device": dev,
        }
        for key in HOST_PROC_KEYS:
          # Omitted slow-tier keys on ``@fast`` stay None (never invent 0).
          row[key] = vals_by_key.get(key)
        self.proc_stats.append(row)
        return

      if typ not in self.schema:
        return

      tier_marker = None
      if vals and vals[0] in _TIER_MARKERS:
        tier_marker = vals[0]
        vals = vals[1:]

      if tier_marker == "@fast":
        if schema_needs_legacy_hardware_decode(typ, self.schema[typ]):
          return
        schema_keys = self.schema_fast.get(typ, [])
        use_legacy = False
      else:
        schema_keys = self.schema[typ]
        use_legacy = schema_needs_legacy_hardware_decode(typ, self.schema[typ])

      vals_dict = _vals_dict_from_line(
          typ, self.schema, schema_keys, vals, use_legacy, dev=dev)
      if vals_dict is None:
        return

      out_typ = legacy_parsing.legacy_output_type(typ) if use_legacy else typ
      rec = {**self.line_ctx["tags"], "type": out_typ, "dev": dev}
      _append_stats_rows(self.stats, rec, vals_dict)

    elif i >= self.start_idx and s[0].isdigit():
      parsed = _digit_line_identity(s)
      if parsed is None:
        return
      t, jid, host = parsed
      self.insert = True
      # Same sample-header jid as tags2; idle monitors emit "-".
      self.line_ctx["tags"] = {"time": float(t), "host": host, "jid": jid}
      self.line_ctx["tags2"] = {"time": float(t), "host": host, "jid": jid}
    elif s[0] == "!":
      label, events = s.split(maxsplit=1)
      typ, events = label[1:], events.split()
      self.schema[typ] = events
      self.schema_fast[typ] = _fast_schema_keys(events)

  def feed_lines(self, lines: Any) -> None:
    """
    Feed lines.
    
    Args:
      lines (Any): Lines passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> IncrementalStatsParser().feed_lines(None)  # doctest: +SKIP
    """
    for line in lines:
      self.feed_line(line)

  def finish(self) -> Any:
    """
    Finish processing and finalize state.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> IncrementalStatsParser().finish()  # doctest: +SKIP
    """
    return self.stats, self.proc_stats


def parse_stats_lines(
  lines: Any,
  start_idx: Any,
  eventmaps_by_type: Any | None = None,
  exclude_types_list: Any | None = None,
) -> Any:
  """
  Parse stats and proc_stats from lines starting at start_idx.
  
  Legacy archives (CTL/CTR or legacy st_name) use sync_timedb_parsing_legacy.
  eventmaps_by_type is ignored (kept for API compat); detection is automatic.
  
  Args:
    lines (Any): Lines passed to this helper.
    start_idx (Any): Start idx passed to this helper.
    eventmaps_by_type (Any | None): One of ``Any``, ``None``.
    exclude_types_list (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_stats_lines(None, None, None, None)  # doctest: +SKIP
  """
  del eventmaps_by_type  # noqa: F841 — auto-detect legacy vs canonical
  parser = IncrementalStatsParser(start_idx, exclude_types_list)
  parser.feed_lines(lines)
  return parser.finish()


def parse_stats_file_streaming(
  stats_file: str,
  *,
  start_line_idx: int = 0,
  parse_start_idx: int = 0,
  batch_size: int = STREAM_PARSE_LINE_BATCH,
  exclude_types_list: Any | None = None,
) -> Any:
  """
  Parse a large stats file in bounded batches without ``readlines()``.
  
  Resume offsets must feed the file prefix through the parser so ``!`` schema
  lines register; emission is gated by ``start_idx`` (same as
    ``parse_stats_lines``).
  Do not fast-forward with bare ``fd.readline()`` — that drops schema and
    silently
  discards every hardware stats line (RC-0).
  
  Args:
    stats_file (str): String for stats file.
    start_line_idx (int): Integer value for start line idx.
    parse_start_idx (int): Integer value for parse start idx.
    batch_size (int): Integer value for batch size.
    exclude_types_list (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_stats_file_streaming("x", 0, 0, 0, None)  # doctest: +SKIP
  """
  emission_start = max(int(start_line_idx or 0), int(parse_start_idx or 0))
  parser = IncrementalStatsParser(emission_start, exclude_types_list)
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        while True:
          batch = []
          for _ in range(int(batch_size)):
            line = fd.readline()
            if not line:
              break
            batch.append(line)
          if not batch:
            break
          parser.feed_lines(batch)
          del batch
  except FileNotFoundError:
    return [], []
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return parser.finish()


def build_stats_dataframes(stats_list: Any, proc_stats_list: Any) -> Any:
  """
  Build the stats dataframes.
  
  Args:
    stats_list (Any): Stats list passed to this helper.
    proc_stats_list (Any): Proc stats list passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_stats_dataframes(None, None)  # doctest: +SKIP
  """
  proc_stats_df = DataFrame.from_records(proc_stats_list)
  if not proc_stats_df.empty:
    # Peak-merge duplicate (jid, host, proc): GREATEST for vm_stk/exe/lib.
    merged = dedupe_proc_stats_peak_merge(
        proc_stats_df.to_dict(orient="records")
    )
    proc_stats_df = DataFrame.from_records(merged)
  stats_df = DataFrame.from_records(stats_list)
  return stats_df, proc_stats_df


_EMPTY_DELTA_ARC_COLUMNS = [
    "time", "host", "jid", "type", "dev", "event", "unit", "value", "delta", "arc"
]


class DeltaCarryState:
  """
  Cross-chunk state for counter deltas and arc rates during incremental ingest.
  
  Attributes:
    arc: Attribute.
    raw: Attribute.
  """

  __slots__ = ("raw", "arc")

  def __init__(self) -> None:
    """
    Initialize a new instance.
    
    Returns:
      None
    
    Examples:
      >>> DeltaCarryState()  # doctest: +SKIP
    """
    self.raw = {}
    self.arc = {}


def _empty_delta_arc_frame() -> Any:
  """
  Internal helper to handle empty delta arc DataFrame.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _empty_delta_arc_frame()  # doctest: +SKIP
  """
  return DataFrame(columns=_EMPTY_DELTA_ARC_COLUMNS)


def _stats_df_has_required_delta_cols(stats_df: Any) -> Any:
  """
  Internal helper to handle stats DataFrame has required delta cols.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _stats_df_has_required_delta_cols(None)  # doctest: +SKIP
  """
  required_cols = {
      "host", "type", "dev", "event", "unit", "time", "value", "wid", "mult"
  }
  return (
      not stats_df.empty
      and required_cols.issubset(stats_df.columns)
  )


def _apply_counter_deltas(stats_df: Any, carry: Any | None = None) -> Any:
  """
  Apply counter diffs; optional cross-flush ``carry.raw`` continuity.
  
  Carry paths must stay vectorized (groupby head/tail + array extract).
  
  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _apply_counter_deltas(None, None)  # doctest: +SKIP
  """
  stats_df = stats_df.sort_values(by=_COUNTER_GROUP_COLS + ["time"]).copy()
  stats_df["delta"] = stats_df.groupby(
      _COUNTER_GROUP_COLS, observed=True)["value"].diff()

  if carry is not None and carry.raw:
    first = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      hosts = first["host"].to_numpy()
      types = first["type"].to_numpy()
      devs = first["dev"].to_numpy()
      events = first["event"].to_numpy()
      values = first["value"].to_numpy(dtype=np.float64, copy=False)
      wids = first["wid"].to_numpy(copy=False)
      mults = first["mult"].to_numpy(dtype=np.float64, copy=False)
      idxs = first.index.to_numpy()
      carry_deltas = np.full(len(first), np.nan, dtype=np.float64)
      apply_mask = np.zeros(len(first), dtype=bool)
      for i in range(len(first)):
        prev = carry.raw.get((hosts[i], types[i], devs[i], events[i]))
        if prev is None:
          continue
        delta = float(values[i]) - float(prev["value"])
        if delta < 0:
          delta = (2 ** int(wids[i])) + delta
        carry_deltas[i] = delta * float(mults[i])
        apply_mask[i] = True
      if apply_mask.any():
        stats_df.loc[idxs[apply_mask], "delta"] = carry_deltas[apply_mask]

  stats_df["delta"] = stats_df["delta"].mask(
      stats_df["delta"] < 0, 2 ** stats_df["wid"] + stats_df["delta"])
  stats_df["delta"] = stats_df["delta"] * stats_df["mult"]

  if carry is not None:
    last = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      hosts = last["host"].to_numpy()
      types = last["type"].to_numpy()
      devs = last["dev"].to_numpy()
      events = last["event"].to_numpy()
      values = last["value"].to_numpy(dtype=np.float64, copy=False)
      wids = last["wid"].to_numpy(copy=False)
      mults = last["mult"].to_numpy(dtype=np.float64, copy=False)
      times = last["time"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(last)):
        carry.raw[(hosts[i], types[i], devs[i], events[i])] = {
            "value": float(values[i]),
            "wid": int(wids[i]),
            "mult": float(mults[i]),
            "time": float(times[i]),
        }

  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _normalize_collapse_dev_column(stats_df: Any) -> Any:
  """
  Fill missing ``dev`` with ``''`` so group keys and UNIQUE semantics match.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_collapse_dev_column(None)  # doctest: +SKIP
  """
  if "dev" not in stats_df.columns:
    out = stats_df.copy()
    out["dev"] = ""
    return out
  out = stats_df.copy()
  dev = out["dev"]
  out["dev"] = dev.where(dev.notna(), "").astype(str).replace(
      {"nan": "", "None": "", "<NA>": ""}
  )
  return out


def _collapse_stats_with_deltas(stats_df: Any) -> Any:
  """
  Collapse multi-row samples; GPU types keep ``dev``, others sum across devices.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _collapse_stats_with_deltas(None)  # doctest: +SKIP
  """
  stats_df = _normalize_collapse_dev_column(stats_df)
  gcols = _COLLAPSE_GROUP_COLS
  gcols_gpu = _COLLAPSE_GROUP_COLS_WITH_DEV
  nv_df = stats_df[stats_df["type"] == "nvidia_gpu"]
  other_gpu_df = stats_df[stats_df["type"].isin({"amd_gpu", "intel_gpu"})]
  rest_df = stats_df[~stats_df["type"].isin(_GPU_STATS_TYPES)]
  parts = []
  if not rest_df.empty:
    ccm_power_mask = (
        rest_df["type"].isin(_HOST_CPU_HW_TYPES)
        & rest_df["event"].isin(_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS))
    ccm_power_df = rest_df[ccm_power_mask]
    rest_other = rest_df[~ccm_power_mask]
    if not rest_other.empty:
      collapsed_rest = _groupby_sum_min_count(rest_other, gcols)
      collapsed_rest["dev"] = ""
      parts.append(collapsed_rest)
    if not ccm_power_df.empty:
      collapsed_ccm = _collapse_dcg_cpu_power_vectorized(ccm_power_df, gcols)
      collapsed_ccm["dev"] = ""
      parts.append(collapsed_ccm)
  if not other_gpu_df.empty:
    # Identity groups when each (host,dev,event,time) is unique.
    parts.append(_groupby_sum_min_count(other_gpu_df, gcols_gpu))
  if not nv_df.empty:
    parts.append(_collapse_nvidia_gpu_vectorized(nv_df, gcols_gpu))

  if not parts:
    return _empty_delta_arc_frame()
  collapsed = concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
  del parts
  if "dev" not in collapsed.columns:
    collapsed["dev"] = ""
  else:
    collapsed["dev"] = collapsed["dev"].fillna("").astype(str)
  return collapsed.sort_values(by=_ARC_GROUP_COLS + ["time"])


def _apply_arc_and_finalize(stats_df: Any, carry: Any | None = None) -> Any:
  """
  Compute arc rates; optional cross-flush ``carry.arc`` continuity.
  
  Carry paths must stay vectorized (groupby head/tail + array extract /
  Index.get_indexer once).
  
  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _apply_arc_and_finalize(None, None)  # doctest: +SKIP
  """
  deltat = stats_df.groupby(_ARC_GROUP_COLS, observed=True)["time"].diff()
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)

  if carry is not None and carry.arc:
    first = stats_df.groupby(_ARC_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      positions = stats_df.index.get_indexer(first.index)
      hosts = first["host"].to_numpy()
      types = first["type"].to_numpy()
      devs = first["dev"].to_numpy() if "dev" in first.columns else [""] * len(first)
      events = first["event"].to_numpy()
      times = first["time"].to_numpy(dtype=np.float64, copy=False)
      deltas = first["delta"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(first)):
        pos = int(positions[i])
        if pos < 0:
          continue
        prev = carry.arc.get((hosts[i], types[i], str(devs[i] or ""), events[i]))
        if prev is None:
          continue
        dt = float(times[i]) - float(prev["time"])
        if dt > 0 and np.isfinite(deltas[i]):
          _arc[pos] = float(deltas[i]) / dt

  stats_df = stats_df.copy()
  stats_df["arc"] = _arc

  if carry is not None:
    last = stats_df.groupby(_ARC_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      hosts = last["host"].to_numpy()
      types = last["type"].to_numpy()
      devs = last["dev"].to_numpy() if "dev" in last.columns else [""] * len(last)
      events = last["event"].to_numpy()
      times = last["time"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(last)):
        carry.arc[(hosts[i], types[i], str(devs[i] or ""), events[i])] = {
            "time": float(times[i]),
        }

  stats_df["time"] = to_datetime(stats_df["time"], unit="s").dt.tz_localize("UTC")
  return stats_df.dropna(subset=["host", "type", "event", "time", "value"])


def _warn_nonempty_stats_collapsed_to_empty(stats_df: Any) -> None:
  """
  Loud warning when a non-empty stats frame yields zero delta/arc rows.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _warn_nonempty_stats_collapsed_to_empty(None)  # doctest: +SKIP
  """
  if stats_df is None or getattr(stats_df, "empty", True):
    return
  cols = [str(c) for c in list(stats_df.columns)]
  warnings.warn(
      "non-empty stats frame collapsed to empty delta/arc rows=%d cols=%s"
      % (int(len(stats_df)), cols),
      stacklevel=3,
  )


def compute_deltas_and_arc(stats_df: Any) -> Any:
  """
  Compute the deltas and arc.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> compute_deltas_and_arc(None)  # doctest: +SKIP
  """
  if not _stats_df_has_required_delta_cols(stats_df):
    _warn_nonempty_stats_collapsed_to_empty(stats_df)
    return _empty_delta_arc_frame()
  stats_df = _apply_counter_deltas(stats_df.copy())
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  return _apply_arc_and_finalize(stats_df)


def compute_deltas_and_arc_chunk(stats_df: Any, *, carry: Any) -> Any:
  """
  Compute deltas/arc for one incremental flush; update ``carry`` in place.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any): Carry passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ValueError: Raised when ``compute_deltas_and_arc_chunk`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> compute_deltas_and_arc_chunk(None, None)  # doctest: +SKIP
  """
  if carry is None:
    raise ValueError("carry is required for incremental delta computation")
  if not _stats_df_has_required_delta_cols(stats_df):
    _warn_nonempty_stats_collapsed_to_empty(stats_df)
    return _empty_delta_arc_frame()
  stats_df = _apply_counter_deltas(stats_df, carry=carry)
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  return _apply_arc_and_finalize(stats_df, carry=carry)


def _line_starts_time_sample(line: Any, line_index: Any, start_idx: Any) -> Any:
  """
  Internal helper to handle line starts time sample.
  
  Args:
    line (Any): Line passed to this helper.
    line_index (Any): Line index passed to this helper.
    start_idx (Any): Start idx passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _line_starts_time_sample(None, None, None)  # doctest: +SKIP
  """
  if not line:
    return False
  stripped = line.lstrip()
  if not stripped:
    return False
  return stripped[0].isdigit() and line_index >= start_idx


def parse_stats_file_streaming_incremental(
  stats_file: str,
  *,
  start_line_idx: int = 0,
  parse_start_idx: int = 0,
  flush_rows: Any,
  on_chunk: Any,
  line_batch_size: int = STREAM_PARSE_LINE_BATCH,
  exclude_types_list: Any | None = None,
) -> None:
  """
  Parse a large stats file, flushing complete time samples via ``on_chunk``.
  
  Resume offsets must feed the file prefix through the parser so ``!`` schema
  lines register; emission is gated by ``start_idx`` (same as
    ``parse_stats_lines``).
  Do not fast-forward with bare ``fd.readline()`` — that drops schema and
    silently
  discards every hardware stats line (RC-0).
  
  Args:
    stats_file (str): String for stats file.
    start_line_idx (int): Integer value for start line idx.
    parse_start_idx (int): Integer value for parse start idx.
    flush_rows (Any): Flush rows passed to this helper.
    on_chunk (Any): On chunk passed to this helper.
    line_batch_size (int): Integer value for line batch size.
    exclude_types_list (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> parse_stats_file_streaming_incremental("x", 0, 0, None, None, 0, None)
  """
  emission_start = max(int(start_line_idx or 0), int(parse_start_idx or 0))
  parser = IncrementalStatsParser(emission_start, exclude_types_list)
  pending_flush = False
  flush_rows = max(1, int(flush_rows))

  def _emit_flush() -> None:
    """
    Internal helper to handle emit flush.
    
    Returns:
      None
    
    Examples:
      >>> _emit_flush()  # doctest: +SKIP
    """
    nonlocal pending_flush
    if not parser.stats and not parser.proc_stats:
      pending_flush = False
      return
    on_chunk(parser.stats, parser.proc_stats)
    parser.stats = []
    parser.proc_stats = []
    pending_flush = False

  def _on_time_sample_boundary() -> None:
    """
    Internal helper to handle on time sample boundary.
    
    Returns:
      None
    
    Examples:
      >>> _on_time_sample_boundary()  # doctest: +SKIP
    """
    if pending_flush or len(parser.stats) >= flush_rows:
      _emit_flush()

  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        while True:
          got_line = False
          for _ in range(int(line_batch_size)):
            line = fd.readline()
            if not line:
              break
            got_line = True
            if _line_starts_time_sample(
                line, parser._line_index, parser.start_idx):
              _on_time_sample_boundary()
            parser.feed_line(line)
            if len(parser.stats) >= flush_rows:
              pending_flush = True
          if not got_line:
            break
    _emit_flush()
  except FileNotFoundError:
    return
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
