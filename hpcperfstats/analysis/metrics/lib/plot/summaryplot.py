"""Summary plot: multi-metric step plots for a job (FLOPS, BW, CPU, etc.) using jid_table aggregate data and Bokeh.

"""
import hpcperfstats.dbload.lib.conf_parser as cfg

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

from django.db import close_old_connections

from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    begin_summary_aggregate_counting,
    end_summary_aggregate_counting,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
    LLITE_METADATA_IOPS_EVENTS,
)

import numpy as np
from pandas import isna as pd_isna
from pandas import to_datetime

from hpcperfstats.analysis.metrics.lib.gen.utils import (
    CHA_TYPENAME_PRIORITY,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
    add_hover_plain_columns,
    format_plain_decimal,
    format_cluster_hover_datetime,
    non_degenerate_y_range_for_series,
    set_linear_axes_plain_numeric,
    tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    aperf_event_names,
    core_pmc_types_probe_order,
    dram_cas_read_write_pairs,
    events_probe_names,
    imc_types_probe_order,
    instr_retired_event_names,
    mperf_event_names,
    pkg_energy_event_names,
    type_probe_names,
)

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.palettes import d3
from bokeh.plotting import figure
from bokeh.transform import factor_cmap

from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.plot.hover_html import hover_tooltip_html_host_time_value
from hpcperfstats.analysis.metrics.lib.plot.job_window import job_window_timestamps_utc
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
    description_for_summary_metric,
    researcher_use_for_summary_metric,
)
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
    add_job_detail_bokeh_help_marker,
)

# Hard cap for nested aggregate prefetch threads (see job_plots + api ThreadPoolExecutor).
# Keeps DB connection and work_mem spikes bounded when summaryplot runs inside a worker thread.
_SUMMARY_AGGREGATE_PREFETCH_MAX_THREADS = 2


def _cycled_d3_category20_palette(n):
  """Return ``n`` colors from d3 Category20, cycling every 20 hosts.

  ``factor_cmap`` requires ``len(palette) == len(factors)``; jobs often exceed
  20 nodes (Bokeh W-1008 otherwise maps extras to ``nan_color``).
  """
  base = d3["Category20"][20]
  if n <= 0:
    return []
  return [base[i % len(base)] for i in range(n)]


_CAS_BW_CONV = 64 / (1024 * 1024 * 1024)
_BYTES_TO_MB = 1 / (1024 * 1024)
_BYTES_TO_GB = 1 / (1024 * 1024 * 1024)
# Intel CHA counter names after dbload event map (must match host_data.event strings).
_CHA_ARC_EVENTS = (
    "SF_EVICTIONS_MES,E",
    "LLC_LOOKUP_DATA_READ_LOCAL,E",
    "BYPASS_CHA_IMC_ALL,E",
    "LLC_LOOKUP_WRITE",
)
def _summary_type_events_feasible(schema, typ, events):
  """Return False when schema is known and no requested event exists for typ (skip ORM work)."""
  if not isinstance(schema, dict) or not schema:
    return True
  if not events:
    return any(t in schema for t in type_probe_names(typ))
  probed_events = set(events_probe_names(list(events)))
  for t in type_probe_names(typ):
    if t not in schema:
      continue
    if probed_events.intersection(schema[t]):
      return True
  return False


def _empty_agg_df():
  import pandas as pd

  return pd.DataFrame(columns=["host", "time", "sum_val"])


def _get_agg_if_feasible(jt, typ, val_col, events, conv):
  """Like jt.get_aggregate_df with canonical + legacy type/event dual-read."""
  raw_schema = getattr(jt, "schema", None)
  schema = raw_schema if isinstance(raw_schema, dict) else {}
  if not _summary_type_events_feasible(schema, typ, events):
    return _empty_agg_df()
  ev = events_probe_names(list(events))
  for t in type_probe_names(typ):
    if not _summary_type_events_feasible(schema, t, events):
      continue
    agg = jt.get_aggregate_df(t, val_col, ev, conv)
    if agg is not None and not agg.empty:
      return agg
  return _empty_agg_df()


def _step_polyline_xy(times, values):
  """Build x/y lists for a step-before polyline through (time, value) samples."""
  if times is None or len(times) == 0:
    return [], []
  t = times.tolist() if hasattr(times, "tolist") else list(times)
  v = values.tolist() if hasattr(values, "tolist") else list(values)
  if len(t) != len(v):
    return [], []
  xs = [t[0]]
  ys = [v[0]]
  for i in range(1, len(t)):
    xs.append(t[i])
    ys.append(v[i - 1])
    xs.append(t[i])
    ys.append(v[i])
  return xs, ys


def compute_summary_aggregate_prefetch_pool_size(num_specs):
  """Return ``ThreadPoolExecutor`` ``max_workers`` for summary aggregate prefetch.

  Capped by ``_SUMMARY_AGGREGATE_PREFETCH_MAX_THREADS`` so nested parallelism does not
  multiply against ``site.machine.api``'s shared executor under ``job_plots``.
  """
  return max(
    1,
    min(
      int(num_specs),
      cfg.get_parallel_db_prefetch_max_workers(),
      _SUMMARY_AGGREGATE_PREFETCH_MAX_THREADS,
    ),
  )


def _prefetch_single_spec_aggregates(jt, spec_rows):
  """Parallel fetch for (typ, val, events, conv, name) rows; returns name -> DataFrame.

  Thread count is capped by ``_SUMMARY_AGGREGATE_PREFETCH_MAX_THREADS`` so this path
  does not multiply against ``api.py``'s shared executor when building summary plots.
  """
  import pandas as pd

  raw_schema = getattr(jt, "schema", None)
  schema = raw_schema if isinstance(raw_schema, dict) else {}

  def _one(row):
    typ, val, events, conv, name = row
    close_old_connections()
    try:
      if not _summary_type_events_feasible(schema, typ, events):
        return name, pd.DataFrame(columns=["host", "time", "sum_val"])
      return name, jt.get_aggregate_df(typ, val, list(events), conv)
    finally:
      close_old_connections()

  out = {}
  if not spec_rows:
    return out
  max_workers = compute_summary_aggregate_prefetch_pool_size(len(spec_rows))
  with ThreadPoolExecutor(max_workers=max_workers) as pool:
    futures = [pool.submit(_one, row) for row in spec_rows]
    for fut in as_completed(futures):
      name, df = fut.result()
      out[name] = df
  return out


def _intel_core_tries(events, conv):
  """(typename, events, conv) rows for Intel PMC and host_cpu_hw."""
  ev = list(events)
  return [(t, ev, conv) for t in core_pmc_types_probe_order()]


# One aggregate per row (fixed typename); used for AMD, fabric, etc.
_SUMMARY_SINGLE_SPECS = [
    ("amd_x86_pmc", "arc", ["fp_ops_retired"], "amd_flops", 1e-9, "CPU FLOPS32b+64b[GF]"),
    (
        "amd_x86_uncore_df",
        "arc",
        [
            "MBW_CHANNEL_0",
            "MBW_CHANNEL_1",
            "MBW_CHANNEL_2",
            "MBW_CHANNEL_3",
        ],
        "amd_mbw",
        2 / (1024 * 1024 * 1024),
        "CPU DRAMBW[GB/s]",
    ),
    (
        "amd_x86_pmc",
        "value",
        list(instr_retired_event_names()),
        "amd_instr",
        1,
        "CPU Instructions [#/s]",
    ),
    ("amd_x86_pmc", "arc", list(mperf_event_names()), "amd_mcycles", 1, "CPU Reference Cycles [#/s]"),
    ("amd_x86_pmc", "arc", list(aperf_event_names()), "amd_acycles", 1, "CPU Actual Cycles [#/s]"),
    (
        "intel_x86_rapl",
        "arc",
        list(pkg_energy_event_names()),
        "watts",
        0.00001526,
        "CPU package power [W]",
    ),
    (
        "amd_x86_rapl",
        "arc",
        list(pkg_energy_event_names()),
        "amd_pkg_w",
        0.00001526,
        "CPU package power (AMD) [W]",
    ),
    (
        "host_ib",
        "arc",
        ["port_rcv_data", "port_xmit_data"],
        "ibbw",
        1 / (1024 * 1024),
        "FabricBW[MB/s]",
    ),
    (
        "host_opa",
        "arc",
        ["PortXmitWait", "SwPortCongestion"],
        "opa_wait_cong",
        1.0,
        "OPA wait+switch congestion [#/s]",
    ),
    (
        "host_opa",
        "arc",
        ["PortRcvFECN", "PortRcvBECN"],
        "opa_ecn",
        1.0,
        "OPA FECN+BECN [#/s]",
    ),
    (
        "host_numa",
        "arc",
        ["numa_miss", "numa_foreign", "other_node"],
        "numa_remote_refs",
        1.0,
        "CPU NUMA remote refs [#/s]",
    ),
    (
        "lustre_llite",
        "arc",
        list(LLITE_METADATA_IOPS_EVENTS),
        "liops",
        1,
        "Lustre IOPS [#/s]",
    ),
    ("lustre_llite", "arc", ["read_bytes"], "lustre_read_mb_s", _BYTES_TO_MB, "Lustre read [MB/s]"),
    ("lustre_llite", "arc", ["write_bytes"], "lustre_write_mb_s", _BYTES_TO_MB, "Lustre write [MB/s]"),
    (
        "host_nfs",
        "arc",
        ["normal_read", "direct_read", "server_read"],
        "nfs_read_mb_s",
        _BYTES_TO_MB,
        "NFS read [MB/s]",
    ),
    (
        "host_nfs",
        "arc",
        ["normal_write", "direct_write", "server_write"],
        "nfs_write_mb_s",
        _BYTES_TO_MB,
        "NFS write [MB/s]",
    ),
    ("host_nfs", "arc", ["read_ops", "write_ops"], "nfs_iops", 1.0, "NFS IOPS [#/s]"),
    ("host_cpu", "arc", ["user", "system", "nice"], "cpu", 0.01,
     "CPU Usage [#cores]"),
    ("nvidia_gpu", "value", ["gpu_util"], "nv_gpu_util", 1, "GPU util [%]"),
    ("nvidia_gpu", "value", ["mem_used_mb"], "nv_mem_used_mb", 1 / 1024, "GPU MemUsed [GB]"),
    (
        "nvidia_gpu",
        "value",
        ["mem_total_mb"],
        "nv_mem_total_mb",
        1 / 1024,
        "GPU mem total [GB]",
    ),
    ("nvidia_gpu", "value", ["gpu_count"], "nv_gpu_count", 1, "GPU count"),
    (
        "nvidia_gpu",
        "value",
        ["tensor_active"],
        "nv_tensor_active",
        1.0,
        "GPU tensor pipe [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["sm_occupancy"],
        "nv_sm_occupancy",
        1.0,
        "GPU SM occupancy [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["fp16_active"],
        "nv_fp16_active",
        1.0,
        "GPU FP16 pipe [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["fp32_active"],
        "nv_fp32_active",
        1.0,
        "GPU FP32 pipe [%]",
    ),
    ("nvidia_gpu", "value", ["mem_util"], "nv_mem_util_pct", 1.0, "GPU mem util [%]"),
    ("nvidia_gpu", "value", ["power_usage"], "nv_power_w", 1.0, "GPU power [W]"),
    (
        "nvidia_gpu",
        "value",
        ["module_power_usage"],
        "nv_module_power_w",
        1.0,
        "GPU module power [W]",
    ),
    (
        "host_cpu_hw",
        "value",
        ["dcgm_cpu_power_util_w"],
        "dcg_cpu_power_w",
        1.0,
        "Grace CPU power [W]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["gpu_mem_bw_bytes_rate"],
        "nv_gpu_mem_bw_gbs",
        _BYTES_TO_GB,
        "GPU HBM BW est. [GB/s]",
    ),
    (
        "nvidia_gpu",
        "arc",
        ["gpu_io_link_total_bytes"],
        "nv_gpu_link_gbs",
        _BYTES_TO_GB,
        "GPU PCIe+NVLink [GB/s]",
    ),
    ("host_mem", "value", ["mem_used"], "mem", 1 / (1024 * 1024), "CPU MemUsed[GB]"),
]

# Metrics that may be sampled on a sparse (host, time) grid vs the union grid from
# get_host_time_df(); do not drop the column when a left-merge leaves NaN gaps.
_SUMMARY_ALLOW_PARTIAL_NULL = frozenset({
    "nv_gpu_util",
    "nv_mem_used_mb",
    "nv_mem_total_mb",
    "nv_gpu_count",
    "nv_tensor_active",
    "nv_sm_occupancy",
    "nv_fp16_active",
    "nv_fp32_active",
    "nv_mem_util_pct",
    "nv_power_w",
    "nv_module_power_w",
    "dcg_cpu_power_w",
    "amd_pkg_w",
    "node_power_est_w",
    "nv_gpu_mem_bw_gbs",
    "nv_gpu_link_gbs",
    "cha_counter_arc_sum",
    "opa_wait_cong",
    "opa_ecn",
    "numa_remote_refs",
    "lustre_read_mb_s",
    "lustre_write_mb_s",
    "nfs_read_mb_s",
    "nfs_write_mb_s",
    "nfs_iops",
})

# Merged for scaling/context only; not rendered as its own subplot.
_SUMMARY_SKIP_PLOT_METRICS = frozenset({
    "nv_mem_total_mb",
    "nv_gpu_count",
    "nv_module_power_w",
    "dcg_cpu_power_w",
    "amd_pkg_w",
})

# First typename with full host/time coverage wins (same column name).
_SUMMARY_FIRST_WIN_SPECS = (
    {
        "name": "flops64b",
        "val_col": "arc",
        "label": "CPU FLOPS64b[GF]",
        "tries": _intel_core_tries(INTEL_FP_ARITH_DOUBLE_EVENTS, 1e-9),
    },
    {
        "name": "flops32b",
        "val_col": "arc",
        "label": "CPU FLOPS32b[GF]",
        "tries": _intel_core_tries(INTEL_FP_ARITH_SINGLE_EVENTS, 1e-9),
    },
    {
        "name": "instr",
        "val_col": "arc",
        "label": "CPU Instructions [#/s]",
        "tries": _intel_core_tries(list(instr_retired_event_names()), 1),
    },
    {
        "name": "mcycles",
        "val_col": "arc",
        "label": "CPU Reference Cycles [#/s]",
        "tries": _intel_core_tries(list(mperf_event_names()), 1),
    },
    {
        "name": "acycles",
        "val_col": "arc",
        "label": "CPU Actual Cycles [#/s]",
        "tries": _intel_core_tries(list(aperf_event_names()), 1),
    },
)


def _summary_nv_mem_used_y_range_end(df):
  """Upper y bound for GPU mem used plot: max(used, total) when total column exists."""
  if "nv_mem_used_mb" not in df.columns or "nv_mem_total_mb" not in df.columns:
    return None
  candidates = []
  for col in ("nv_mem_used_mb", "nv_mem_total_mb"):
    mx = df[col].max()
    if mx is not None and not pd_isna(mx):
      try:
        candidates.append(float(mx))
      except (TypeError, ValueError):
        pass
  if not candidates:
    return None
  return 1.1 * max(candidates)


def _summary_nv_gpu_util_y_range_end(df):
  """Upper y bound for GPU util: GPU_count * 100 when GPU count exists."""
  if "nv_gpu_util" not in df.columns or "nv_gpu_count" not in df.columns:
    return None
  gpu_count_max = df["nv_gpu_count"].max()
  if gpu_count_max is None or pd_isna(gpu_count_max):
    return None
  try:
    return float(gpu_count_max) * 100.0
  except (TypeError, ValueError):
    return None


def _summary_intel_imc_bw_tries():
  """Intel DRAM BW: first IMC type with dram CAS read/write rows."""
  tries = []
  for imc_typ in imc_types_probe_order():
    for read_ev, write_ev in dram_cas_read_write_pairs():
      tries.append((imc_typ, [read_ev, write_ev], _CAS_BW_CONV))
  return tries


def _merge_first_full_coverage(df, jt, column_name, val_col, tries):
  """Left-merge first (typ, events, conv) whose aggregate has no nulls on base (host, time)."""
  for typ, events, conv in tries:
    agg = _get_agg_if_feasible(jt, typ, val_col, events, conv)
    if agg.empty or "sum_val" not in agg.columns:
      continue
    merged = df.merge(
        agg[["host", "time", "sum_val"]],
        on=["host", "time"],
        how="left",
    )
    merged[column_name] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    if column_name in merged.columns and merged[column_name].isnull().values.any():
      continue
    return merged
  return df


def _merge_nvidia_gpu_util_column(df, jt):
  """Left-merge ``nv_gpu_util`` from ``nvidia_gpu`` ``value``: prefer ``gpu_util``, else ``utilization``.

  Matches ``avg_gpuutil`` / job_detail GPU stats: newer monitor emits ``gpu_util``;
  older archives may only have ``utilization``.
  """
  name = "nv_gpu_util"
  for events in (["gpu_util"], ["utilization"]):
    agg = _get_agg_if_feasible(jt, "nvidia_gpu", "value", events, 1.0)
    if agg.empty or "sum_val" not in agg.columns:
      continue
    merged = df.merge(
        agg[["host", "time", "sum_val"]],
        on=["host", "time"],
        how="left",
    )
    merged[name] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    if name in merged.columns and merged[name].isnull().values.any():
      keep_sparse = (
          name in _SUMMARY_ALLOW_PARTIAL_NULL and merged[name].notna().any()
      )
      if not keep_sparse:
        del merged[name]
        continue
    return merged
  return df


def _merge_opa_fabric_if_no_ib_ext(df, jt):
  """When ``host_ib`` bytes are absent, fill ``ibbw`` from Omni-Path (same scaling as ``avg_ibbw`` OPA path)."""
  if "ibbw" in df.columns and df["ibbw"].notna().any():
    return df
  agg = _get_agg_if_feasible(
      jt,
      "opa",
      "arc",
      ["PortXmitData", "PortRcvData"],
      1.0 / 125000.0,
  )
  if agg.empty or "sum_val" not in agg.columns:
    return df
  merged = df.merge(
      agg[["host", "time", "sum_val"]],
      on=["host", "time"],
      how="left",
  )
  merged["ibbw"] = merged["sum_val"]
  merged.drop(columns=["sum_val"], inplace=True)
  return merged


def _merge_cha_counter_arc_sum(df, jt):
  """Sum selected Intel CHA ``arc`` counters (all boxes); first matching CHA typename in schema."""
  raw_schema = getattr(jt, "schema", None)
  schema = raw_schema if isinstance(raw_schema, dict) else {}
  for cha_typ in CHA_TYPENAME_PRIORITY:
    if cha_typ not in schema:
      continue
    have = set(schema[cha_typ])
    evs = [e for e in _CHA_ARC_EVENTS if e in have]
    if not evs:
      continue
    agg = _get_agg_if_feasible(jt, cha_typ, "arc", evs, 1.0)
    if agg.empty or "sum_val" not in agg.columns:
      continue
    merged = df.merge(
        agg[["host", "time", "sum_val"]],
        on=["host", "time"],
        how="left",
    )
    merged["cha_counter_arc_sum"] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    return merged
  return df


def _add_node_power_est_column(df):
  """Estimated on-node power (W): module-only when ``nv_module_power_w`` > 0, else CPU + GPU.

  CPU side: ``dcg_cpu_power_w`` (Grace DCGM) if present, else Intel ``watts``, else ``amd_pkg_w``.
  GPU side: ``nv_power_w`` (summed per-GPU draw). Does **not** add module + DCGM + per-GPU together.
  """
  n = len(df.index)
  out = np.full(n, np.nan, dtype=np.float64)
  has_mod = "nv_module_power_w" in df.columns
  has_gpu = "nv_power_w" in df.columns
  dcg = df["dcg_cpu_power_w"] if "dcg_cpu_power_w" in df.columns else None
  intel = df["watts"] if "watts" in df.columns else None
  amd = df["amd_pkg_w"] if "amd_pkg_w" in df.columns else None
  for i in range(n):
    if has_mod:
      modv = df["nv_module_power_w"].iloc[i]
      if not pd_isna(modv) and float(modv) > 0.0:
        out[i] = float(modv)
        continue
    cpu = float("nan")
    if dcg is not None:
      v = dcg.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    if math.isnan(cpu) and intel is not None:
      v = intel.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    if math.isnan(cpu) and amd is not None:
      v = amd.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    gpu = float("nan")
    if has_gpu:
      gv = df["nv_power_w"].iloc[i]
      if not pd_isna(gv):
        gpu = float(gv)
    if math.isnan(cpu) and math.isnan(gpu):
      continue
    total = 0.0
    if math.isfinite(cpu):
      total += cpu
    if math.isfinite(gpu):
      total += gpu
    if math.isfinite(cpu) or math.isfinite(gpu):
      out[i] = total
  df["node_power_est_w"] = out
  return df


def iter_summary_aggregate_attempts():
  """Flat (typ, val_col, events, name, conv, label) for diagnostics."""
  for typ, val, events, name, conv, label in _SUMMARY_SINGLE_SPECS:
    if name == "nv_gpu_util":
      yield "nvidia_gpu", "value", ["gpu_util"], name, conv, label
      yield "nvidia_gpu", "value", ["utilization"], name, conv, label
      continue
    yield typ, val, events, name, conv, label
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    for typ, events, conv in fw["tries"]:
      yield typ, fw["val_col"], events, fw["name"], conv, fw["label"]
  for imc_typ, events, conv in _summary_intel_imc_bw_tries():
    yield imc_typ, "arc", events, "mbw", conv, "CPU DRAMBW[GB/s]"


def _summary_metric_specs():
  """Ordered (typ, val, events, name, conv, label) for plot() second pass (plot columns only)."""
  out = list(_SUMMARY_SINGLE_SPECS)
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    out.append(("", fw["val_col"], [], fw["name"], 0, fw["label"]))
  out.append(("intel_imc", "arc", [], "mbw", _CAS_BW_CONV, "CPU DRAMBW[GB/s]"))
  out.append(("", "", [], "cha_counter_arc_sum", 0, "CPU CHA uncore [#/s]"))
  out.append(("", "", [], "node_power_est_w", 0, "Est. node power (CPU+GPU) [W]"))
  return out


def _summary_plot_order_key(metric_name):
  """Priority order for summary subplots (ascending).

  Product ordering (each block is contiguous when those metrics exist):
  1) CPU usage
  2) CPU memory (NUMA + DRAM bandwidth)
  3) CPU flops / cycles / package power / uncore CHA
  4) GPU usage
  5) GPU memory
  6) GPU FLOPS / tensors / SM / HBM BW estimate
  7) GPU other (power, PCIe/NVLink)
  8) Lustre client (llite): read / write / metadata IOPS
  9) NFS client: read / write / IOPS
  10) Network: InfiniBand/OPA bytes, fabric/compute ratios, OPA quality counters
  """
  priority = {
      # --- 1) CPU usage ---
      "cpu": 100,
      # --- 2) CPU memory ---
      "mem": 200,
      "numa_remote_refs": 210,
      "mbw": 220,
      "amd_mbw": 230,
      # --- 3) CPU flops / cycles / power / uncore ---
      "amd_flops": 300,
      "flops64b": 310,
      "flops32b": 320,
      "instr": 330,
      "amd_instr": 335,
      "mcycles": 340,
      "acycles": 350,
      "amd_mcycles": 355,
      "amd_acycles": 360,
      "freq": 370,
      "watts": 380,
      "cha_counter_arc_sum": 390,
      # --- 4) GPU usage ---
      "nv_gpu_util": 500,
      # --- 5) GPU memory ---
      "nv_mem_used_mb": 510,
      "nv_mem_util_pct": 520,
      # --- 6) GPU FLOPS / tensors ---
      "nv_tensor_active": 600,
      "nv_sm_occupancy": 610,
      "nv_fp16_active": 620,
      "nv_fp32_active": 630,
      "nv_gpu_mem_bw_gbs": 640,
      # --- 7) GPU other ---
      "nv_power_w": 700,
      "node_power_est_w": 710,
      "nv_gpu_link_gbs": 720,
      # --- 8) Lustre (llite) ---
      "lustre_read_mb_s": 800,
      "lustre_write_mb_s": 803,
      "liops": 806,
      # --- 9) NFS ---
      "nfs_read_mb_s": 810,
      "nfs_write_mb_s": 813,
      "nfs_iops": 816,
      # --- 10) Network ---
      "ibbw": 900,
      "opa_wait_cong": 930,
      "opa_ecn": 940,
  }
  # Unknown metrics: after CPU/CHA block, before GPU (avoids splitting CPU story).
  return priority.get(metric_name, 395)


# InfiniBand ``ib`` counters: error-like arc rates only (exclude byte/packet throughput and wait).
_IB_SUMMARY_ERROR_EVENTS = (
    "excessive_buffer_overrun_errors",
    "link_downed",
    "link_error_recovery",
    "local_link_integrity_errors",
    "port_rcv_constraint_errors",
    "port_rcv_errors",
    "port_rcv_remote_physical_errors",
    "port_rcv_switch_relay_errors",
    "port_xmit_constraint_errors",
    "port_xmit_discards",
    "symbol_error",
)

_NET_SUMMARY_ERROR_EVENTS = (
    "rx_crc_errors",
    "rx_errors",
    "rx_fifo_errors",
    "rx_frame_errors",
    "rx_length_errors",
    "rx_missed_errors",
    "rx_over_errors",
    "tx_aborted_errors",
    "tx_carrier_errors",
    "tx_errors",
    "tx_fifo_errors",
    "tx_heartbeat_errors",
    "tx_window_errors",
)


def _one_error_job_series(jt, typ, event):
  """Return DataFrame columns ``time``, ``rate`` (job-wide sum of arc per time) or None."""
  agg = _get_agg_if_feasible(jt, typ, "arc", [event], 1.0)
  if agg.empty or "sum_val" not in agg.columns:
    return None
  out = (
      agg.groupby("time", as_index=False)["sum_val"]
      .sum()
      .rename(columns={"sum_val": "rate"})
  )
  if out.empty or not out["rate"].notna().any():
    return None
  return out


def _collect_hardware_error_job_series(jt):
  """List of (legend, time/rate frame) for overlay; may be empty."""
  import pandas as pd

  parts = []
  for ev in _IB_SUMMARY_ERROR_EVENTS:
    leg = f"ib:{ev}"
    s = _one_error_job_series(jt, "host_ib", ev)
    if s is not None:
      parts.append((leg, s))
  for ev in _NET_SUMMARY_ERROR_EVENTS:
    leg = f"net:{ev}"
    s = _one_error_job_series(jt, "net", ev)
    if s is not None:
      parts.append((leg, s))
  s = _one_error_job_series(jt, "opa", "PortErrorCounterSummary")
  if s is not None:
    parts.append(("opa:PortErrorCounterSummary", s))
  return parts


def plot_hardware_error_rates_figure(jt, x_range):
  """One figure: job-wide hardware error counter rates [#/s]; None if no data."""
  import pandas as pd

  series = _collect_hardware_error_job_series(jt)
  if not series:
    return None
  merged = None
  for leg, sdf in series:
    sdf = sdf.rename(columns={"rate": leg})
    if merged is None:
      merged = sdf
    else:
      merged = pd.merge(merged, sdf, on="time", how="outer")
  merged = merged.sort_values("time")
  value_cols = [c for c in merged.columns if c != "time"]
  if not value_cols:
    return None
  merged[value_cols] = merged[value_cols].fillna(0.0)
  if not merged[value_cols].to_numpy().any():
    return None

  merged["time"] = to_datetime(merged["time"], utc=True)

  plot_kwargs = figure_embed_kw(
      150,
      x_axis_type="datetime",
      x_axis_label="Time",
      y_axis_label="Hardware error rates [#/s]",
      title="Hardware error rates (job-wide sum)",
  )
  if x_range is not None:
    plot_kwargs["x_range"] = x_range
  plot = figure(**plot_kwargs)
  plot.xaxis.ticker.desired_num_ticks = 5
  set_linear_axes_plain_numeric(plot)
  plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

  palette = d3["Category10"][10]
  line_renderers = []
  for i, col in enumerate(value_cols):
    sub = merged[["time", col]].dropna()
    if sub.empty:
      continue
    xi, yi = _step_polyline_xy(sub["time"], sub[col])
    color = palette[i % len(palette)]
    src = ColumnDataSource(
        data={
            "x": xi,
            "y": yi,
            "x_plain": [format_cluster_hover_datetime(v) for v in xi],
            "y_plain": [format_plain_decimal(v) for v in yi],
        },
    )
    ln = plot.line("x", "y", source=src, line_width=1.5, color=color, legend_label=col)
    line_renderers.append(ln)
    plot.add_tools(
        HoverTool(
            renderers=[ln],
            tooltips=[
                ("Series", col),
                ("Time", "@x_plain"),
                ("Rate [#/s]", "@y_plain"),
            ],
        )
    )
  if line_renderers:
    plot.legend.location = "top_left"
    plot.legend.click_policy = "hide"
  add_job_detail_bokeh_help_marker(
      plot,
      description_for_summary_metric("summary_hardware_error_rates"),
      researcher_use_for_summary_metric("summary_hardware_error_rates"),
  )
  return plot


class SummaryPlot():
  """Builds a grid of Bokeh step plots (one per metric) from jid_table aggregate DataFrames.

    """

  def __init__(self, jt):
    """Store jid, jt, and host_list from the given jid_table (or HostDataProvider).

        """
    self.jid = jt.jid
    self.jt = jt
    self.host_list = jt.host_list

  def plot_metric(
      self,
      df,
      metric,
      label,
      y_range_end=None,
      x_range=None,
      variable_description=None,
  ):
    """Create one Bokeh figure with step-shaped lines and scatter hits per host.

        Uses one multi_line glyph plus one scatter (HoverTool on data) and a separate
        HoverTool on a small “?” marker for metric documentation.
        """
    s = time.time()

    df = df[["time", "host", metric]].copy()
    df["host"] = df["host"].astype(str)

    y_range_start, y_range_end = non_degenerate_y_range_for_series(
        df[metric], y_range_end=y_range_end
    )

    label_text = (label or "").strip() or metric

    plot_kwargs = figure_embed_kw(
        150,
        x_axis_type="datetime",
        y_range=Range1d(y_range_start, y_range_end),
        x_axis_label="Time",
        y_axis_label=label_text,
        title=label_text,
    )
    if x_range is not None:
      plot_kwargs["x_range"] = x_range
    plot = figure(
        **plot_kwargs,
    )
    plot.xaxis.ticker.desired_num_ticks = 5
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    xs_list = []
    ys_list = []
    colors_list = []
    for h in self.host_list:
      host_key = str(h)
      sub = df[(df["host"] == host_key) & df[metric].notna()].sort_values("time")
      if sub.empty:
        xs_list.append([])
        ys_list.append([])
      else:
        xi, yi = _step_polyline_xy(sub["time"], sub[metric])
        xs_list.append(xi)
        ys_list.append(yi)
      colors_list.append(self.hc[h])

    line_source = ColumnDataSource(
        data={"xs": xs_list, "ys": ys_list, "line_color": colors_list}
    )
    plot.multi_line(
        xs="xs",
        ys="ys",
        line_color="line_color",
        source=line_source,
        line_width=1.5,
    )

    scatter_df = add_hover_plain_columns(
        df.dropna(subset=[metric]).sort_values(["host", "time"]),
        [metric],
        time_col="time",
    )
    scatter_source = ColumnDataSource(scatter_df)
    factors = [str(h) for h in self.host_list]
    palette = _cycled_d3_category20_palette(len(factors))
    scatter = plot.scatter(
        x="time",
        y=metric,
        source=scatter_source,
        size=4,
        marker="circle",
        color=factor_cmap("host", palette=palette, factors=factors),
        alpha=0.9,
    )

    plot.add_tools(
        HoverTool(
            tooltips=hover_tooltip_html_host_time_value(label_text, metric),
            renderers=[scatter],
        )
    )
    if variable_description is not None:
      doc_text = variable_description
      researcher_use = None
    else:
      doc_text = description_for_summary_metric(metric)
      researcher_use = researcher_use_for_summary_metric(metric)
    add_job_detail_bokeh_help_marker(plot, doc_text, researcher_use)
    log.debug("time to plot %s: %s", metric, time.time() - s)
    return plot

  def plot(self):
    """Build host_time_df, merge all configured metrics (amd64_pmc, intel_8pmc3, llite, cpu, mem, etc.), and return a gridplot of step plots.

        """
    palette = _cycled_d3_category20_palette(len(self.host_list))
    self.hc = {
        hostname: palette[i] for i, hostname in enumerate(self.host_list)
    }

    log.debug("Host Count: %s", len(self.host_list))

    df = self.jt.get_host_time_df()
    if df.empty or not self.host_list:
      raise ValueError(MSG_NO_METRIC_DATA)

    spec_rows = [
        (typ, val, tuple(events), conv, name)
        for typ, val, events, name, conv, label in _SUMMARY_SINGLE_SPECS
        if name != "nv_gpu_util"
    ]
    prefetched = _prefetch_single_spec_aggregates(self.jt, spec_rows)

    for typ, val, events, name, conv, label in _SUMMARY_SINGLE_SPECS:
      if name == "nv_gpu_util":
        continue
      s = time.time()
      agg = prefetched.get(name)
      if agg is None:
        agg = _empty_agg_df()
      if agg.empty or "sum_val" not in agg.columns:
        df[name] = float("nan")
      else:
        df = df.merge(agg[["host", "time", "sum_val"]],
                      on=["host", "time"],
                      how="left")
        df[name] = df["sum_val"]
        df.drop(columns=["sum_val"], inplace=True)

      if name == "amd_watts":
        log.debug("amd_watts: %s", df[name].tolist())
      if name in df.columns and df[name].isnull().values.any():
        keep_sparse = (
            name in _SUMMARY_ALLOW_PARTIAL_NULL and df[name].notna().any()
        )
        if not keep_sparse:
          del df[name]
      log.debug("time to compute %s: %s", name, time.time() - s)

    df = _merge_nvidia_gpu_util_column(df, self.jt)

    for fw in _SUMMARY_FIRST_WIN_SPECS:
      s = time.time()
      df = _merge_first_full_coverage(
          df, self.jt, fw["name"], fw["val_col"], fw["tries"])
      log.debug("time to compute %s: %s", fw["name"], time.time() - s)

    df = _merge_first_full_coverage(
        df, self.jt, "mbw", "arc", _summary_intel_imc_bw_tries())

    df = _merge_cha_counter_arc_sum(df, self.jt)
    df = _merge_opa_fabric_if_no_ib_ext(df, self.jt)
    df = _add_node_power_est_column(df)

    metrics = _summary_metric_specs()

    if 'acycles' in df.columns and 'mcycles' in df.columns:
      df["freq"] = 2.7 * df["acycles"] / df["mcycles"]
      metrics += [("freq", "arc", [], "freq", 1, "CPU freq [GHz]")]
      del df["mcycles"], df["acycles"], df["instr"]

    if 'amd_acycles' in df.columns and 'amd_mcycles' in df.columns:
      del df["amd_mcycles"], df["amd_acycles"], df["amd_instr"]
    df = df.reset_index()

    df["time"] = to_datetime(df["time"], utc=True)

    render_specs = []
    for typ, val, events, name, conv, label in metrics:
      if name not in df.columns:
        continue
      if name == "node_power_est_w" and not df[name].notna().any():
        continue
      if name in _SUMMARY_SKIP_PLOT_METRICS:
        continue
      if name == "freq":
        freq_max = df[name].max()
        if freq_max is None or math.isnan(freq_max) or freq_max <= 500:
          continue
      y_top = None
      if name == "nv_mem_used_mb":
        y_top = _summary_nv_mem_used_y_range_end(df)
      elif name == "nv_gpu_util":
        y_top = _summary_nv_gpu_util_y_range_end(df)
      render_specs.append((name, label, y_top))

    render_specs.sort(key=lambda item: _summary_plot_order_key(item[0]))

    x_start, x_end = job_window_timestamps_utc(self.jt)
    x_range = Range1d(x_start, x_end) if x_start is not None and x_end is not None else None

    plots = []
    for name, label, y_top in render_specs:
      plots += [self.plot_metric(df, name, label, y_range_end=y_top, x_range=x_range)]

    err_fig = plot_hardware_error_rates_figure(self.jt, x_range)
    if err_fig is not None:
      plots.append(err_fig)

    if not plots:
      raise ValueError(MSG_NO_METRIC_DATA)
    # Root GridPlot must stretch horizontally so the grid uses the full embed width
    # (child figures already use stretch_width via figure_embed_kw).
    return gridplot(plots, ncols=min(2, len(plots)), sizing_mode="stretch_width")


def plot_and_reason_summary_from_jid_table(jt):
  """Build summary plot and return (figure_or_none, unavailable_reason_or_none)."""
  t0 = time.monotonic()
  begin_summary_aggregate_counting()
  try:
    fig = SummaryPlot(jt).plot()
  except Exception as plot_exc:
    end_summary_aggregate_counting()
    host_time_df = jt.get_host_time_df()
    if host_time_df.empty or not jt.host_list:
      return (None, "No hosts/timestamps found in host_data for this job/time range")

    schema = getattr(jt, "schema", None)
    if isinstance(schema, dict) and schema:
      type_keys = ", ".join(sorted(schema.keys())[:24])
      if len(schema) > 24:
        type_keys += ", ..."
      return (
          None,
          "Missing summary counters in host_data (no renderable series). "
          f"host_data metric types present: {type_keys}. "
          f"Detail: {plot_exc!s}",
      )
    return (
        None,
        "Missing summary counters in host_data (no renderable series). "
        f"Detail: {plot_exc!s}",
    )
  else:
    n_aggs = end_summary_aggregate_counting()
    log.debug(
        "summary plot success jid=%s elapsed_s=%.3f get_aggregate_df_calls=%s",
        getattr(jt, "jid", None),
        time.monotonic() - t0,
        n_aggs,
    )
    return (fig, None)

