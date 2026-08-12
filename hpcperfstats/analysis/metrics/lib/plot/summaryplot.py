"""
Summary plot: multi-metric step plots for a job (FLOPS, BW, CPU, etc.) using
jid_table aggregate data and Bokeh.

Attributes:
  _BYTES_TO_GB: Attribute.
  _BYTES_TO_GBPS: Attribute.
  _BYTES_TO_MB: Attribute.
  _CAS_BW_CONV: Attribute.
  _CHA_ARC_EVENTS: Attribute.
  _HARDWARE_ERROR_METRIC_COL: Attribute.
  _IB_SUMMARY_ERROR_EVENTS: Attribute.
  _MAX_SANE_GPU_LINK_GBPS: Attribute.
  _NET_SUMMARY_ERROR_EVENTS: Attribute.
  _SERIAL_SUMMARY_AGGREGATE_PREFETCH: Attribute.
  get_summary_aggregate_prefetch_max_threads(): Attribute.
  _SUMMARY_ALLOW_PARTIAL_NULL: Attribute.
  _SUMMARY_FIRST_WIN_SPECS: Attribute.
  _SUMMARY_SINGLE_SPECS: Attribute.
  _SUMMARY_SKIP_PLOT_METRICS: Attribute.
  _SUMMARY_TENSOR_SPLIT_METRICS: Attribute.
  log: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import contextlib
import contextvars
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
    add_hover_plain_columns,
    non_degenerate_y_range_for_series,
    set_linear_axes_plain_numeric,
    timestamps_as_cluster_naive,
    tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    CHA_TYPENAME_PRIORITY,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    aperf_event_names,
    core_pmc_types_probe_order,
    dram_cas_read_write_pairs,
    events_probe_names,
    grace_fp_scalar_double_event_names,
    grace_fp_scalar_single_event_names,
    hbm_cas_read_write_pairs,
    host_cpu_hw_type_names,
    imc_types_probe_order,
    instr_retired_event_names,
    mperf_event_names,
    pkg_energy_event_names,
    type_probe_names,
)
from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import (
    agg_sum_val_to_bw_frame,
    combine_cas_bw_frames,
)

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.palettes import d3
from bokeh.plotting import figure
from bokeh.transform import factor_cmap

from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.plot.hover_html import hover_tooltip_html_host_time_value
from hpcperfstats.analysis.metrics.lib.plot.job_window import job_window_bounds_local
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
    description_for_summary_metric,
    researcher_use_for_summary_metric,
)
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
    add_job_detail_bokeh_help_marker,
)

# Hard cap for nested aggregate prefetch threads (see job_plots + api ThreadPoolExecutor).
# Keeps DB connection and work_mem spikes bounded when summaryplot runs inside a worker thread.
# Absolute INI [PORTAL] summary_aggregate_prefetch_max_threads (default 2).

# When True (plot/detail prewarm threads), fetch aggregates serially so peak RSS
# stays ~one host×time grid (design capacity 5000×48×60).
_SERIAL_SUMMARY_AGGREGATE_PREFETCH = contextvars.ContextVar(
    "hps_serial_summary_aggregate_prefetch",
    default=False,
)


def set_serial_summary_aggregate_prefetch(enabled: bool) -> Any:
  """
  Enable or disable serial summary aggregate prefetch for this context.

  Used by plot-artifact prewarm so nested ThreadPools do not multiply memory.

  Args:
    enabled (bool): True to force serial ``get_aggregate_df`` prefetch.

  Returns:
    Any: ContextVar reset token for ``ContextVar.reset``.

  Examples:
    >>> tok = set_serial_summary_aggregate_prefetch(True)
    >>> _SERIAL_SUMMARY_AGGREGATE_PREFETCH.get()
    True
    >>> _SERIAL_SUMMARY_AGGREGATE_PREFETCH.reset(tok)
  """
  return _SERIAL_SUMMARY_AGGREGATE_PREFETCH.set(bool(enabled))


@contextlib.contextmanager
def serial_summary_aggregate_prefetch_context() -> Iterator[None]:
  """
  Force serial summary aggregate prefetch for the duration of the block.

  Yields:
    None

  Examples:
    >>> with serial_summary_aggregate_prefetch_context():
    ...     assert _SERIAL_SUMMARY_AGGREGATE_PREFETCH.get() is True
  """
  token = _SERIAL_SUMMARY_AGGREGATE_PREFETCH.set(True)
  try:
    yield
  finally:
    _SERIAL_SUMMARY_AGGREGATE_PREFETCH.reset(token)


def _cycled_d3_category20_palette(n: Any) -> Any:
  """
  Return ``n`` colors from d3 Category20, cycling every 20 hosts.
  
  ``factor_cmap`` requires ``len(palette) == len(factors)``; jobs often exceed
  20 nodes (Bokeh W-1008 otherwise maps extras to ``nan_color``).
  
  Args:
    n (Any): N passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cycled_d3_category20_palette(None)  # doctest: +SKIP
  """
  base = d3["Category20"][20]
  if n <= 0:
    return []
  return [base[i % len(base)] for i in range(n)]


_CAS_BW_CONV = 64 / (1024 * 1024 * 1024)
_BYTES_TO_MB = 1 / (1024 * 1024)
_BYTES_TO_GBPS = 1e-9  # decimal GB/s — match metrics avg_gpu_* / link conversions
_BYTES_TO_GB = _BYTES_TO_GBPS  # GPU rate plots; prefer _BYTES_TO_GBPS for new call sites
# Intel CHA counter names after dbload event map (must match host_data.event strings).
_CHA_ARC_EVENTS = (
    "SF_EVICTIONS_MES,E",
    "LLC_LOOKUP_DATA_READ_LOCAL,E",
    "BYPASS_CHA_IMC_ALL,E",
    "LLC_LOOKUP_WRITE",
)
def _summary_type_events_feasible(schema: Any, typ: Any, events: Any) -> Any:
  """
  Return False when schema is known and no requested event exists for typ (skip.
  
    ORM work).
  
  Args:
    schema (Any): Schema passed to this helper.
    typ (Any): Typ passed to this helper.
    events (Any): Events passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _summary_type_events_feasible(None, None, None)  # doctest: +SKIP
  """
  if not isinstance(schema, dict) or not schema:
    return True
  if not events:
    return any(t in schema for t in type_probe_names(typ))
  probed_events = set(events_probe_names(list(events), typ=typ))
  for t in type_probe_names(typ):
    if t not in schema:
      continue
    if probed_events.intersection(schema[t]):
      return True
  return False


def _empty_agg_df() -> Any:
  """
  Internal helper to handle empty agg DataFrame.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _empty_agg_df()  # doctest: +SKIP
  """
  import pandas as pd

  return pd.DataFrame(columns=["host", "time", "sum_val"])


def _get_agg_if_feasible(
  jt: Any,
  typ: Any,
  val_col: Any,
  events: Any,
  conv: Any,
) -> Any:
  """
  Like jt.get_aggregate_df with canonical + legacy type/event dual-read.
  
  Args:
    jt (Any): Jt passed to this helper.
    typ (Any): Typ passed to this helper.
    val_col (Any): Val col passed to this helper.
    events (Any): Events passed to this helper.
    conv (Any): Conv passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _get_agg_if_feasible(None, None, None, None, None)  # doctest: +SKIP
  """
  raw_schema = getattr(jt, "schema", None)
  schema = raw_schema if isinstance(raw_schema, dict) else {}
  if not _summary_type_events_feasible(schema, typ, events):
    return _empty_agg_df()
  ev = events_probe_names(list(events), typ=typ)
  for t in type_probe_names(typ):
    if not _summary_type_events_feasible(schema, t, events):
      continue
    agg = jt.get_aggregate_df(t, val_col, ev, conv)
    if agg is not None and not agg.empty:
      return agg
  return _empty_agg_df()


def _continuous_polyline_xy(times: Any, values: Any) -> Any:
  """
  Build x/y lists for a continuous polyline through (time, value) samples.
  
  NaN values in ``values`` break Bokeh line segments (gaps). Callers that want
  continuous segments across missing samples should drop NaNs before calling.
  
  Args:
    times (Any): Times passed to this helper.
    values (Any): Values passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _continuous_polyline_xy(None, None)  # doctest: +SKIP
  """
  if times is None or len(times) == 0:
    return [], []
  t = times.tolist() if hasattr(times, "tolist") else list(times)
  v = values.tolist() if hasattr(values, "tolist") else list(values)
  if len(t) != len(v):
    return [], []
  return t, v


def compute_summary_aggregate_prefetch_pool_size(num_specs: Any) -> Any:
  """
  Return ``ThreadPoolExecutor`` ``max_workers`` for summary aggregate prefetch.
  
  Capped by ``get_summary_aggregate_prefetch_max_threads`` so nested parallelism
    does not
  multiply against ``site.machine.api``'s shared executor under ``job_plots``.
  
  Args:
    num_specs (Any): Num specs passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> compute_summary_aggregate_prefetch_pool_size(None)  # doctest: +SKIP
  """
  return max(
    1,
    min(
      int(num_specs),
      cfg.get_parallel_db_prefetch_max(),
      cfg.get_summary_aggregate_prefetch_max_threads(),
    ),
  )


def _prefetch_single_spec_aggregates(jt: Any, spec_rows: Any) -> Any:
  """
  Parallel fetch for (typ, val, events, conv, name) rows; returns name ->.
  
    DataFrame.
  
  Thread count is capped by ``get_summary_aggregate_prefetch_max_threads()`` so this
    path
  does not multiply against ``api.py``'s shared executor when building summary
    plots.
  
  Args:
    jt (Any): Jt passed to this helper.
    spec_rows (Any): Spec rows passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    RuntimeError: Re-raised when pool submit fails for a reason other than
      interpreter/ThreadPool shutdown (those fall back to serial prefetch).
    Exception: Propagated from worker futures (``fut.result()``) when aggregate
      fetch fails for a subplot row.
  
  Examples:
    >>> _prefetch_single_spec_aggregates(None, None)  # doctest: +SKIP
  """
  import pandas as pd

  raw_schema = getattr(jt, "schema", None)
  schema = raw_schema if isinstance(raw_schema, dict) else {}

  def _one(row: Any) -> Any:
    """
    Internal helper to handle one.
    
    Args:
      row (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _one(None)  # doctest: +SKIP
    """
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

  def _run_serial() -> dict:
    """
    Fetch each aggregate row sequentially when the thread pool cannot start.

    Returns:
      dict: Mapping of subplot name to aggregate DataFrame.

    Examples:
      >>> _run_serial()  # doctest: +SKIP
    """
    serial = {}
    for row in spec_rows:
      name, df = _one(row)
      serial[name] = df
    return serial

  if _SERIAL_SUMMARY_AGGREGATE_PREFETCH.get():
    return _run_serial()
  max_workers = compute_summary_aggregate_prefetch_pool_size(len(spec_rows))

  try:
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
      futures = [pool.submit(_one, row) for row in spec_rows]
      for fut in as_completed(futures):
        name, df = fut.result()
        out[name] = df
  except RuntimeError as exc:
    # Nested under a dying metrics/prewarm pool: do not poison artifacts.
    if "interpreter shutdown" in str(exc).lower() or "cannot schedule new futures" in str(
        exc
    ).lower():
      return _run_serial()
    raise
  return out


def _intel_core_tries(events: Any, conv: Any) -> Any:
  """
  (typename, events, conv) rows for Intel PMC and host_cpu_hw.
  
  Args:
    events (Any): Events passed to this helper.
    conv (Any): Conv passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _intel_core_tries(None, None)  # doctest: +SKIP
  """
  ev = list(events)
  return [(t, ev, conv) for t in core_pmc_types_probe_order()]


def _grace_fp_scalar_tries(events: Any, conv: Any) -> Any:
  """
  (typename, events, conv) rows for Grace host_cpu_hw scalar FP only.
  
  Args:
    events (Any): Events passed to this helper.
    conv (Any): Conv passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _grace_fp_scalar_tries(None, None)  # doctest: +SKIP
  """
  ev = list(events)
  return [(t, ev, conv) for t in host_cpu_hw_type_names()]


# One aggregate per row (fixed typename); used for AMD, fabric, etc.
# amd_mbw is filled via _merge_amd_df_mbw (family DF + dual-read events).
_SUMMARY_SINGLE_SPECS = [
    ("amd_x86_pmc", "arc", ["fp_ops_retired"], "amd_flops", 1e-9, "CPU FLOPS32b+64b[GF]"),
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
        "OPA wait+cong [#/s]",
    ),
    (
        "host_opa",
        "arc",
        ["PortRcvFECN", "PortRcvBECN"],
        "opa_ecn",
        1.0,
        "OPA ECN [#/s]",
    ),
    (
        "host_numa",
        "arc",
        ["numa_miss", "numa_foreign", "other_node"],
        "numa_remote_refs",
        1.0,
        "NUMA remote [#/s]",
    ),
    (
        "lustre_llite",
        "arc",
        list(LLITE_METADATA_IOPS_EVENTS),
        "liops",
        1,
        "Lustre IOPS [#/s]",
    ),
    ("lustre_llite", "arc", ["vfs_read_bytes"], "lustre_read_mb_s", _BYTES_TO_MB, "Lustre read [MB/s]"),
    ("lustre_llite", "arc", ["vfs_write_bytes"], "lustre_write_mb_s", _BYTES_TO_MB, "Lustre write [MB/s]"),
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
     "CPU [#cores]"),
    ("nvidia_gpu", "value", ["gpu_util"], "nv_gpu_util", 1, "GPU util [%]"),
    ("nvidia_gpu", "value", ["mem_used_mb"], "nv_mem_used_mb", 1 / 1024, "GPU mem [GB]"),
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
        ["tensor_imma_active"],
        "nv_tensor_imma_active",
        1.0,
        "GPU IMMA [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["tensor_hmma_active"],
        "nv_tensor_hmma_active",
        1.0,
        "GPU HMMA [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["tensor_dfma_active"],
        "nv_tensor_dfma_active",
        1.0,
        "GPU DFMA [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["tensor_active"],
        "nv_tensor_active",
        1.0,
        "GPU tensor [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["sm_occupancy"],
        "nv_sm_occupancy",
        1.0,
        "GPU SM occ. [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["fp16_active"],
        "nv_fp16_active",
        1.0,
        "GPU FP16 [%]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["fp32_active"],
        "nv_fp32_active",
        1.0,
        "GPU FP32 [%]",
    ),
    ("nvidia_gpu", "value", ["mem_util"], "nv_mem_util_pct", 1.0, "GPU mem util [%]"),
    ("nvidia_gpu", "value", ["power_usage"], "nv_power_w", 1.0, "GPU power [W]"),
    (
        "nvidia_gpu",
        "value",
        ["module_power_usage"],
        "nv_module_power_w",
        1.0,
        "GPU module [W]",
    ),
    (
        "host_cpu_hw",
        "value",
        ["dcgm_cpu_power_util_w"],
        "dcg_cpu_power_w",
        1.0,
        "CPU power [W]",
    ),
    (
        "nvidia_gpu",
        "value",
        ["gpu_mem_bw_bytes_rate"],
        "nv_gpu_mem_bw_gbs",
        _BYTES_TO_GB,
        "GPU HBM BW [GB/s]",
    ),
    (
        "nvidia_gpu",
        "arc",
        ["gpu_io_link_total_bytes"],
        "nv_gpu_link_gbs",
        _BYTES_TO_GB,
        "GPU link [GB/s]",
    ),
    ("host_mem", "value", ["mem_used"], "mem", 1 / (1024 * 1024), "CPU mem [GB]"),
]

# Metrics that may be sampled on a sparse (host, time) grid vs the union grid from
# get_host_time_df(); do not drop the column when a left-merge leaves NaN gaps.
_SUMMARY_ALLOW_PARTIAL_NULL = frozenset({
    "nv_gpu_util",
    "nv_mem_used_mb",
    "nv_mem_total_mb",
    "nv_gpu_count",
    "nv_tensor_imma_active",
    "nv_tensor_hmma_active",
    "nv_tensor_dfma_active",
    "nv_tensor_active",
    "nv_sm_occupancy",
    "nv_fp16_active",
    "nv_fp32_active",
    "nv_mem_util_pct",
    "nv_power_w",
    "nv_module_power_w",
    "dcg_cpu_power_w",
    "watts",
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

# Prefer IMMA/HMMA/DFMA Summary subplots over lumped tensor_active (any-pipe).
_SUMMARY_TENSOR_SPLIT_METRICS = (
    "nv_tensor_imma_active",
    "nv_tensor_hmma_active",
    "nv_tensor_dfma_active",
)


def _summary_has_tensor_split_series(df: Any) -> Any:
  """
  True when any tensor-pipe split column has at least one non-null sample.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _summary_has_tensor_split_series(None)  # doctest: +SKIP
  """
  for name in _SUMMARY_TENSOR_SPLIT_METRICS:
    if name in df.columns and df[name].notna().any():
      return True
  return False


# First typename with full host/time coverage wins (same column name).
_SUMMARY_FIRST_WIN_SPECS = (
    {
        "name": "flops64b",
        "val_col": "arc",
        "label": "CPU FLOPS64b[GF]",
        "tries": (
            _intel_core_tries(INTEL_FP_ARITH_DOUBLE_EVENTS, 1e-9)
            + _grace_fp_scalar_tries(grace_fp_scalar_double_event_names(), 1e-9)
        ),
    },
    {
        "name": "flops32b",
        "val_col": "arc",
        "label": "CPU FLOPS32b[GF]",
        "tries": (
            _intel_core_tries(INTEL_FP_ARITH_SINGLE_EVENTS, 1e-9)
            + _grace_fp_scalar_tries(grace_fp_scalar_single_event_names(), 1e-9)
        ),
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


def _summary_nv_mem_used_y_range_end(df: Any) -> Any:
  """
  Upper y bound for GPU mem used plot: max(used, total) when total column.
  
    exists.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _summary_nv_mem_used_y_range_end(None)  # doctest: +SKIP
  """
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


def _summary_nv_gpu_util_y_range_end(df: Any) -> Any:
  """
  Upper y bound for GPU util: GPU_count * 100 when GPU count exists.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _summary_nv_gpu_util_y_range_end(None)  # doctest: +SKIP
  """
  if "nv_gpu_util" not in df.columns or "nv_gpu_count" not in df.columns:
    return None
  gpu_count_max = df["nv_gpu_count"].max()
  if gpu_count_max is None or pd_isna(gpu_count_max):
    return None
  try:
    return float(gpu_count_max) * 100.0
  except (TypeError, ValueError):
    return None


def _summary_intel_imc_bw_tries() -> Any:
  """
  Diagnostic tries: dram CAS pairs then hbm CAS pairs per IMC type.
  
  Returns:
    Any: Open return polymorphism from ``_summary_intel_imc_bw_tries``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _summary_intel_imc_bw_tries()  # doctest: +SKIP
  """
  tries = []
  for imc_typ in imc_types_probe_order():
    for read_ev, write_ev in dram_cas_read_write_pairs():
      tries.append((imc_typ, [read_ev, write_ev], _CAS_BW_CONV))
    for read_ev, write_ev in hbm_cas_read_write_pairs():
      tries.append((imc_typ, [read_ev, write_ev], _CAS_BW_CONV))
  return tries


def _merge_intel_imc_cas_mbw(df: Any, jt: Any) -> Any:
  """
  Fill ``mbw`` from first IMC type with usable dram and/or hbm CAS BW (sum when.
  
    both).
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_intel_imc_cas_mbw(None, None)  # doctest: +SKIP
  """
  column_name = "mbw"
  for imc_typ in imc_types_probe_order():
    dram_bw = None
    for read_ev, write_ev in dram_cas_read_write_pairs():
      agg = _get_agg_if_feasible(
          jt, imc_typ, "arc", [read_ev, write_ev], _CAS_BW_CONV
      )
      dram_bw = agg_sum_val_to_bw_frame(agg)
      if dram_bw is not None:
        break
    hbm_bw = None
    for read_ev, write_ev in hbm_cas_read_write_pairs():
      agg = _get_agg_if_feasible(
          jt, imc_typ, "arc", [read_ev, write_ev], _CAS_BW_CONV
      )
      hbm_bw = agg_sum_val_to_bw_frame(agg)
      if hbm_bw is not None:
        break
    combined = combine_cas_bw_frames(dram_bw, hbm_bw)
    if combined is None:
      continue
    merged = df.merge(
        combined.rename(columns={"bw_gb": "sum_val"}),
        on=["host", "time"],
        how="left",
    )
    merged[column_name] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    if column_name in merged.columns and merged[column_name].isnull().values.any():
      continue
    return merged
  return df


def _merge_amd_df_mbw(df: Any, jt: Any) -> Any:
  """
  Fill ``amd_mbw`` from family DF ``dram_chan*_bytes`` or historical.
  
    ``MBW_CHANNEL_*``.
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_amd_df_mbw(None, None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.monitor_naming.canonical import AMD_DF_STATS_TYPES
  from hpcperfstats.dbload.lib.monitor_naming.resolve import (
      amd_df_bw_event_conv_tries,
      amd_df_types_probe_order,
  )

  column_name = "amd_mbw"
  for df_typ in amd_df_types_probe_order():
    tries = (
        amd_df_bw_event_conv_tries()[:1]
        if df_typ in AMD_DF_STATS_TYPES
        else amd_df_bw_event_conv_tries()[::-1]
    )
    for events, conv in tries:
      agg = _get_agg_if_feasible(jt, df_typ, "arc", list(events), conv)
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


def _merge_first_full_coverage(
  df: Any,
  jt: Any,
  column_name: Any,
  val_col: Any,
  tries: Any,
) -> Any:
  """
  Left-merge first (typ, events, conv) whose aggregate has no nulls on base.
  
    (host, time).
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
    column_name (Any): Column name passed to this helper.
    val_col (Any): Val col passed to this helper.
    tries (Any): Tries passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_first_full_coverage(None, None, None, None, None)
  """
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


def _merge_nvidia_gpu_util_column(df: Any, jt: Any) -> Any:
  """
  Left-merge ``nv_gpu_util`` from ``nvidia_gpu`` ``value``: prefer.
  
    ``gpu_util``,.
  
    else ``utilization``.
  
  Matches ``avg_gpuutil`` / job_detail GPU stats: newer monitor emits
    ``gpu_util``;
  older archives may only have ``utilization``.
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_nvidia_gpu_util_column(None, None)  # doctest: +SKIP
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


def _merge_opa_fabric_if_no_ib_ext(df: Any, jt: Any) -> Any:
  """
  When ``host_ib`` bytes are absent, fill ``ibbw`` from Omni-Path (same scaling.
  
    as ``avg_ibbw`` OPA path).
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_opa_fabric_if_no_ib_ext(None, None)  # doctest: +SKIP
  """
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


def _merge_cha_counter_arc_sum(df: Any, jt: Any) -> Any:
  """
  Sum selected Intel CHA ``arc`` counters (all boxes); first matching CHA.
  
    typename in schema.
  
  Args:
    df (Any): Df passed to this helper.
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_cha_counter_arc_sum(None, None)  # doctest: +SKIP
  """
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


def _add_node_power_est_column(df: Any) -> Any:
  """
  Estimated on-node power (W): module-only when ``nv_module_power_w`` > 0, else.
  
    CPU + GPU.
  
  CPU side: ``dcg_cpu_power_w`` (Grace DCGM) if present, else Intel ``watts``,
    else ``amd_pkg_w``.
  GPU side: ``nv_power_w`` (summed per-GPU draw). Does **not** add module + DCGM
    + per-GPU together.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _add_node_power_est_column(None)  # doctest: +SKIP
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


# Keep in sync with ``metrics._MAX_SANE_GPU_LINK_GBPS`` (avoid circular import).
_MAX_SANE_GPU_LINK_GBPS = 1.0e5


def _clamp_summary_gpu_link_rates(df: Any) -> Any:
  """
  NaN-out wrap-class poison on ``nv_gpu_link_gbs`` so Y-range stays sane.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _clamp_summary_gpu_link_rates(None)  # doctest: +SKIP
  """
  if df is None or df.empty or "nv_gpu_link_gbs" not in df.columns:
    return df
  s = df["nv_gpu_link_gbs"]
  poison = s.notna() & (s.abs() > _MAX_SANE_GPU_LINK_GBPS)
  if poison.any():
    df = df.copy()
    df.loc[poison, "nv_gpu_link_gbs"] = np.nan
  return df


def iter_summary_aggregate_attempts() -> Iterator[Any]:
  """
  Flat (typ, val_col, events, name, conv, label) for diagnostics.
  
  Yields:
    Iterator[Any]: Open return polymorphism from
    ``iter_summary_aggregate_attempts``: concrete type depends on inputs and
    branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> iter_summary_aggregate_attempts()  # doctest: +SKIP
  """
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


def _summary_metric_specs() -> Any:
  """
  Ordered (typ, val, events, name, conv, label) for plot() second pass (plot.
  
    columns only).
  
  Returns:
    Any: Open return polymorphism from ``_summary_metric_specs``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _summary_metric_specs()  # doctest: +SKIP
  """
  out = list(_SUMMARY_SINGLE_SPECS)
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    out.append(("", fw["val_col"], [], fw["name"], 0, fw["label"]))
  out.append(("intel_imc", "arc", [], "mbw", _CAS_BW_CONV, "CPU DRAMBW[GB/s]"))
  out.append(("", "arc", [], "amd_mbw", 1 / (1024 ** 3), "CPU DRAMBW[GB/s]"))
  out.append(("", "", [], "cha_counter_arc_sum", 0, "CPU CHA uncore [#/s]"))
  out.append(("", "", [], "node_power_est_w", 0, "Node power [W]"))
  return out


def _summary_plot_order_key(metric_name: Any) -> Any:
  """
  Priority order for summary subplots (ascending).
  
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
  
  Args:
    metric_name (Any): Metric name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _summary_plot_order_key(None)  # doctest: +SKIP
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
      "nv_tensor_imma_active": 600,
      "nv_tensor_hmma_active": 605,
      "nv_tensor_dfma_active": 610,
      "nv_tensor_active": 600,
      "nv_sm_occupancy": 615,
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


# Shared column name so plot_metric looks up summary_hardware_error_rates help.
_HARDWARE_ERROR_METRIC_COL = "summary_hardware_error_rates"


def _iter_hardware_error_event_specs() -> Iterator[tuple[str, str, str]]:
  """
  Yield ``(type, event, y_axis_label)`` for Summary hardware-error subplots.

  Yields:
    tuple: Canonical monitor type, event name, and y-axis label string.

  Examples:
    >>> next(_iter_hardware_error_event_specs())[1]
    'excessive_buffer_overrun_errors'
  """
  for ev in _IB_SUMMARY_ERROR_EVENTS:
    yield ("host_ib", ev, f"IB {ev} [#/s]")
  for ev in _NET_SUMMARY_ERROR_EVENTS:
    yield ("net", ev, f"Eth {ev} [#/s]")
  yield (
      "opa",
      "PortErrorCounterSummary",
      "OPA PortErrorCounterSummary [#/s]",
  )


def _one_error_host_series(jt: Any, typ: Any, event: Any) -> Any:
  """
  Return host×time rates for one error counter, or None when unusable.

  Uses arc rates from ``_get_agg_if_feasible``. Returns None when the aggregate
  is empty, all-NaN, or all zeros after fillna (no subplot for that counter).

  Args:
    jt (Any): Job table / HostDataProvider with schema and get_aggregate_df.
    typ (Any): Canonical monitor type (host_ib, net, opa).
    event (Any): Counter event name within that type.

  Returns:
    Any: DataFrame with columns host, time, summary_hardware_error_rates, or
    None when the counter should not be plotted.

  Examples:
    >>> _one_error_host_series(None, "host_ib", "port_rcv_errors")  # doctest: +SKIP
  """
  agg = _get_agg_if_feasible(jt, typ, "arc", [event], 1.0)
  if agg.empty or "sum_val" not in agg.columns:
    return None
  out = agg[["host", "time", "sum_val"]].rename(
      columns={"sum_val": _HARDWARE_ERROR_METRIC_COL}
  )
  if out.empty or not out[_HARDWARE_ERROR_METRIC_COL].notna().any():
    return None
  rates = out[_HARDWARE_ERROR_METRIC_COL].fillna(0.0)
  if not rates.to_numpy().any():
    return None
  return out


def plot_hardware_error_rate_figures(
  summary: Any,
  x_range: Any | None = None,
) -> list[Any]:
  """
  Build one per-host Summary subplot per non-zero hardware-error counter.

  Each figure uses ``SummaryPlot.plot_metric`` (host multi_line + scatter hover +
  screen-space ``?`` help). Counters absent from schema, empty, or all-zero are
  skipped. Returns an empty list when no error panels qualify.

  Args:
    summary (Any): SummaryPlot instance with jt, host_list, and hc colors.
    x_range (Any | None): Shared job-window Range1d, or None.

  Returns:
    list[Any]: List of Bokeh figures (may be empty).

  Examples:
    >>> plot_hardware_error_rate_figures(SummaryPlot(jt), None)  # doctest: +SKIP
  """
  figures: list[Any] = []
  for typ, event, label in _iter_hardware_error_event_specs():
    df = _one_error_host_series(summary.jt, typ, event)
    if df is None:
      continue
    figures.append(
        summary.plot_metric(
            df,
            _HARDWARE_ERROR_METRIC_COL,
            label,
            x_range=x_range,
        )
    )
  return figures


class SummaryPlot():
  """
  Builds a grid of Bokeh continuous-line plots (one per metric) from jid_table.
  
  Attributes:
    host_list: Attribute.
    jid: Attribute.
    jt: Attribute.
  """

  def __init__(self, jt: Any) -> None:
    """
    Store jid, jt, and host_list from the given jid_table (or HostDataProvider).
    
    Args:
      jt (Any): Jt passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> SummaryPlot(None)  # doctest: +SKIP
    """
    self.jid = jt.jid
    self.jt = jt
    self.host_list = jt.host_list

  def plot_metric(
    self,
    df: Any,
    metric: Any,
    label: Any,
    y_range_end: Any | None = None,
    x_range: Any | None = None,
    variable_description: Any | None = None,
  ) -> Any:
    """
    Create one Bokeh figure with continuous per-host lines and scatter hits.
    
    Uses one multi_line glyph plus one scatter (HoverTool on data) and a
      separate
    HoverTool on a small “?” marker for metric documentation.
    
    Args:
      df (Any): Df passed to this helper.
      metric (Any): Metric passed to this helper.
      label (Any): Label passed to this helper.
      y_range_end (Any | None): One of ``Any``, ``None``.
      x_range (Any | None): One of ``Any``, ``None``.
      variable_description (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> SummaryPlot().plot_metric(None, None, None, None, None, None)
    """
    s = time.time()

    df = df[["time", "host", metric]].copy()
    df["host"] = df["host"].astype(str)
    df["time"] = timestamps_as_cluster_naive(to_datetime(df["time"], utc=True))

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
        title="",
        min_border_left=72,
    )
    if x_range is not None:
      plot_kwargs["x_range"] = x_range
    plot = figure(
        **plot_kwargs,
    )
    plot.xaxis.ticker.desired_num_ticks = 5
    plot.yaxis.axis_label_text_font_size = "9pt"
    plot.xaxis.axis_label_text_font_size = "9pt"
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    xs_list = []
    ys_list = []
    colors_list = []
    for h in self.host_list:
      host_key = str(h)
      # Keep NaN samples so multi_line breaks segments across gaps.
      sub = df[df["host"] == host_key].sort_values("time")
      if sub.empty:
        xs_list.append([])
        ys_list.append([])
      else:
        xi, yi = _continuous_polyline_xy(sub["time"], sub[metric])
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
    help_body = (
        f"Time: sample timestamp (cluster timezone) on the X axis. "
        f"Y ({label_text}): {doc_text}"
    )
    add_job_detail_bokeh_help_marker(plot, help_body, researcher_use)
    log.debug("time to plot %s: %s", metric, time.time() - s)
    return plot

  def plot(self) -> Any:
    """
    Build host_time_df, merge all configured metrics (amd64_pmc, intel_8pmc3,.
    
      llite, cpu, mem, etc.), and return a gridplot of continuous-line plots.
    
    Returns:
      Any: Open return polymorphism from ``plot``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Raises:
      ValueError: Raised when ``plot`` hits a ``ValueError`` failure path.
    
    Examples:
      >>> SummaryPlot().plot()  # doctest: +SKIP
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

    df = _merge_intel_imc_cas_mbw(df, self.jt)
    df = _merge_amd_df_mbw(df, self.jt)

    df = _merge_cha_counter_arc_sum(df, self.jt)
    df = _merge_opa_fabric_if_no_ib_ext(df, self.jt)
    df = _add_node_power_est_column(df)
    df = _clamp_summary_gpu_link_rates(df)

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
    skip_lumped_tensor = _summary_has_tensor_split_series(df)
    for typ, val, events, name, conv, label in metrics:
      if name not in df.columns:
        continue
      if name == "node_power_est_w" and not df[name].notna().any():
        continue
      if name in _SUMMARY_SKIP_PLOT_METRICS:
        continue
      if name == "nv_tensor_active" and skip_lumped_tensor:
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

    # Match plot series (cluster-naive wall clock) so Range1d aligns with glyphs.
    x_start_l, x_end_l = job_window_bounds_local(self.jt)
    if x_start_l is not None and x_end_l is not None:
      x_range = Range1d(
          x_start_l.tz_localize(None),
          x_end_l.tz_localize(None),
      )
    else:
      x_range = None

    plots = []
    for name, label, y_top in render_specs:
      plots += [self.plot_metric(df, name, label, y_range_end=y_top, x_range=x_range)]

    plots.extend(plot_hardware_error_rate_figures(self, x_range))

    if not plots:
      raise ValueError(MSG_NO_METRIC_DATA)
    # Root GridPlot must stretch horizontally so the grid uses the full embed width
    # (child figures already use stretch_width via figure_embed_kw).
    return gridplot(plots, ncols=min(2, len(plots)), sizing_mode="stretch_width")


def plot_and_reason_summary_from_jid_table(jt: Any) -> Any:
  """
  Build summary plot and return (figure_or_none, unavailable_reason_or_none).
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> plot_and_reason_summary_from_jid_table(None)  # doctest: +SKIP
  """
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

