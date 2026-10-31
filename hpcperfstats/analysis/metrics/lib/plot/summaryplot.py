"""
Summary plot: multi-metric line plots for a job (FLOPS, BW, CPU, etc.) from
the columnar job table and Bokeh.

The series come from ``columnar.summary_series.build_summary_series`` (one
``(host, time)`` base plus one column per metric); this module owns the
spec tables, the subplot order and the Bokeh figure construction from
arrays. No pandas.

Attributes:
  log: Module logger.
  _CAS_BW_CONV: 64 B per CAS to GB/s.
  _BYTES_TO_MB: Bytes to MiB.
  _BYTES_TO_GBPS: Bytes to decimal GB/s.
  _BYTES_TO_GB: Alias of ``_BYTES_TO_GBPS`` for GPU rate plots.
  _CHA_ARC_EVENTS: Intel CHA counter events summed for the uncore subplot.
  _SUMMARY_SINGLE_SPECS: One-aggregate subplot specs.
  _SUMMARY_ALLOW_PARTIAL_NULL: Columns kept with NaN gaps.
  _SUMMARY_SKIP_PLOT_METRICS: Columns merged for scaling only.
  _SUMMARY_TENSOR_SPLIT_METRICS: Tensor-pipe split columns.
  _SUMMARY_FIRST_WIN_SPECS: First-full-coverage subplot specs.
  _MAX_SANE_GPU_LINK_GBPS: Ceiling for GPU link rates.
  _IB_SUMMARY_ERROR_EVENTS: InfiniBand error counters.
  _NET_SUMMARY_ERROR_EVENTS: Ethernet error counters.
  _HARDWARE_ERROR_METRIC_COL: Shared hardware-error column name.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from typing import Any

import numpy as np
from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.palettes import d3
from bokeh.plotting import figure
from bokeh.transform import factor_cmap

from hpcperfstats.analysis.metrics.lib.beegfs_metadata_iops_events import (
  BEEGFS_METADATA_IOPS_EVENTS,
  BEEGFS_READ_BYTES_EVENTS,
  BEEGFS_WRITE_BYTES_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.columnar.series_frame import SeriesFrame
from hpcperfstats.analysis.metrics.lib.gen.utils import (
  hover_plain_columns,
  non_degenerate_y_range_for_series,
  set_linear_axes_plain_numeric,
  timestamps_as_cluster_naive,
  tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
  LLITE_METADATA_IOPS_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.phase_profile import (
  phase as _profile_phase,
)
from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
  add_job_detail_bokeh_help_marker,
)
from hpcperfstats.analysis.metrics.lib.plot.hover_html import (
  hover_tooltip_html_host_time_value,
)
from hpcperfstats.analysis.metrics.lib.plot.job_window import (
  job_window_bounds_local,
)
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
  description_for_summary_metric,
  researcher_use_for_summary_metric,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
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

log = logging.getLogger(__name__)


@contextlib.contextmanager
def serial_summary_aggregate_prefetch_context() -> Iterator[None]:
  """
  Compatibility no-op: the columnar table serves aggregates from memory, so
  there is no aggregate prefetch pool left to serialise.

  Yields:
    None

  Examples:
    >>> with serial_summary_aggregate_prefetch_context():
    ...   pass
  """
  yield


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


_BYTES_TO_GBPS = (
  1e-9  # decimal GB/s - match metrics avg_gpu_* / link conversions
)


_BYTES_TO_GB = (
  _BYTES_TO_GBPS  # GPU rate plots; prefer _BYTES_TO_GBPS for new call sites
)


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
  (
    "amd_x86_pmc",
    "arc",
    ["fp_ops_retired"],
    "amd_flops",
    1e-9,
    "CPU FLOPS32b+64b[GF]",
  ),
  (
    "amd_x86_pmc",
    "value",
    list(instr_retired_event_names()),
    "amd_instr",
    1,
    "CPU Instructions [#/s]",
  ),
  (
    "amd_x86_pmc",
    "arc",
    list(mperf_event_names()),
    "amd_mcycles",
    1,
    "CPU Reference Cycles [#/s]",
  ),
  (
    "amd_x86_pmc",
    "arc",
    list(aperf_event_names()),
    "amd_acycles",
    1,
    "CPU Actual Cycles [#/s]",
  ),
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
  (
    "lustre_llite",
    "arc",
    ["vfs_read_bytes"],
    "lustre_read_mb_s",
    _BYTES_TO_MB,
    "Lustre read [MB/s]",
  ),
  (
    "lustre_llite",
    "arc",
    ["vfs_write_bytes"],
    "lustre_write_mb_s",
    _BYTES_TO_MB,
    "Lustre write [MB/s]",
  ),
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
  (
    "host_nfs",
    "arc",
    ["read_ops", "write_ops"],
    "nfs_iops",
    1.0,
    "NFS IOPS [#/s]",
  ),
  (
    "beegfs_client",
    "arc",
    list(BEEGFS_READ_BYTES_EVENTS),
    "beegfs_read_mb_s",
    _BYTES_TO_MB,
    "BeeGFS read [MB/s]",
  ),
  (
    "beegfs_client",
    "arc",
    list(BEEGFS_WRITE_BYTES_EVENTS),
    "beegfs_write_mb_s",
    _BYTES_TO_MB,
    "BeeGFS write [MB/s]",
  ),
  (
    "beegfs_client",
    "arc",
    list(BEEGFS_METADATA_IOPS_EVENTS),
    "beegfs_iops",
    1.0,
    "BeeGFS IOPS [#/s]",
  ),
  (
    "host_cpu",
    "arc",
    ["user", "system", "nice"],
    "cpu",
    0.01,
    "CPU [#cores]",
  ),
  ("nvidia_gpu", "value", ["gpu_util"], "nv_gpu_util", 1, "GPU util [%]"),
  (
    "nvidia_gpu",
    "value",
    ["mem_used_mb"],
    "nv_mem_used_mb",
    1 / 1024,
    "GPU mem [GB]",
  ),
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
  (
    "nvidia_gpu",
    "value",
    ["mem_util"],
    "nv_mem_util_pct",
    1.0,
    "GPU mem util [%]",
  ),
  (
    "nvidia_gpu",
    "value",
    ["power_usage"],
    "nv_power_w",
    1.0,
    "GPU power [W]",
  ),
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
]


# Metrics that may be sampled on a sparse (host, time) grid vs the union grid from
# get_host_time_df(); do not drop the column when a left-merge leaves NaN gaps.
_SUMMARY_ALLOW_PARTIAL_NULL = frozenset(
  {
    "mem",
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
    "beegfs_read_mb_s",
    "beegfs_write_mb_s",
    "beegfs_iops",
  }
)


# Merged for scaling/context only; not rendered as its own subplot.
_SUMMARY_SKIP_PLOT_METRICS = frozenset(
  {
    "nv_mem_total_mb",
    "nv_gpu_count",
    "nv_module_power_w",
    "dcg_cpu_power_w",
    "amd_pkg_w",
  }
)


# Prefer IMMA/HMMA/DFMA Summary subplots over lumped tensor_active (any-pipe).
_SUMMARY_TENSOR_SPLIT_METRICS = (
  "nv_tensor_imma_active",
  "nv_tensor_hmma_active",
  "nv_tensor_dfma_active",
)


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


# Keep in sync with ``metrics._MAX_SANE_GPU_LINK_GBPS`` (avoid circular import).
_MAX_SANE_GPU_LINK_GBPS = 1.0e5


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
  yield (
    "host_mem_job",
    "value",
    ["mem_used"],
    "mem",
    1 / (1024 * 1024),
    "CPU mem [GB]",
  )
  yield (
    "host_mem",
    "value",
    ["mem_used"],
    "mem",
    1 / (1024 * 1024),
    "CPU mem [GB]",
  )
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
  out.append(
    (
      "host_mem_job",
      "value",
      ["mem_used"],
      "mem",
      1 / (1024 * 1024),
      "CPU mem [GB]",
    )
  )
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    out.append(("", fw["val_col"], [], fw["name"], 0, fw["label"]))
  out.append(("intel_imc", "arc", [], "mbw", _CAS_BW_CONV, "CPU DRAMBW[GB/s]"))
  out.append(("", "arc", [], "amd_mbw", 1 / (1024**3), "CPU DRAMBW[GB/s]"))
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
    # --- 10) BeeGFS ---
    "beegfs_read_mb_s": 820,
    "beegfs_write_mb_s": 823,
    "beegfs_iops": 826,
    # --- 11) Network ---
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


def _continuous_polyline_xy(times: Any, values: Any) -> Any:
  """
  Build x/y lists for a continuous polyline through (time, value) samples.

  NaN values in ``values`` break Bokeh line segments (gaps). Callers that want
  continuous segments across missing samples should drop NaNs before calling.

  Args:
    times (Any): Naive cluster-local ``datetime64`` samples.
    values (Any): Values aligned with ``times``.

  Returns:
    Any: ``(xs, ys)`` lists, or two empty lists on a length mismatch.

  Examples:
    >>> _continuous_polyline_xy([], [])
    ([], [])
  """
  if times is None or len(times) == 0:
    return [], []
  t = times.tolist() if hasattr(times, "tolist") else list(times)
  v = values.tolist() if hasattr(values, "tolist") else list(values)
  if len(t) != len(v):
    return [], []
  return t, v


def _error_series_frame(
  hosts: list[str], host_id: Any, time_us: Any, rates: Any
) -> SeriesFrame:
  """
  Frame with one ``summary_hardware_error_rates`` column (``fillna(0)``).

  Args:
    hosts (list[str]): Host names by id.
    host_id (Any): Host id per row.
    time_us (Any): Sample time per row.
    rates (Any): Error rate per row (NaN -> 0).

  Returns:
    SeriesFrame: Frame for :meth:`SummaryPlot.plot_metric`.

  Examples:
    >>> import numpy as np
    >>> f = _error_series_frame(
    ...   ["h"], np.array([0]), np.array([1]), np.array([np.nan])
    ... )
    >>> f.columns[_HARDWARE_ERROR_METRIC_COL].tolist()
    [0.0]
  """
  frame = SeriesFrame.from_base(host_id, time_us, hosts)
  order = np.lexsort((frame.time_us, frame.host_id))  # from_base reorders rows
  del order
  # Re-align the rates with the sorted base.
  key_in = list(
    zip(np.asarray(host_id).tolist(), np.asarray(time_us).tolist(), strict=True)
  )
  lookup = dict(
    zip(key_in, np.asarray(rates, dtype=np.float64).tolist(), strict=True)
  )
  aligned = np.array(
    [
      lookup[(int(h), int(t))]
      for h, t in zip(frame.host_id, frame.time_us, strict=True)
    ],
    dtype=np.float64,
  )
  frame.set(_HARDWARE_ERROR_METRIC_COL, np.nan_to_num(aligned, nan=0.0))
  return frame


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
    >>> plot_hardware_error_rate_figures(
    ...   SummaryPlot(jt), None
    ... )  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
    hardware_error_series,
  )

  figures: list[Any] = []
  hosts = list(summary.jt.frame.hosts)
  for _event, label, host_id, time_us, rates in hardware_error_series(
    summary.jt
  ):
    frame = _error_series_frame(hosts, host_id, time_us, rates)
    with _profile_phase("bokeh"):
      figures.append(
        summary.plot_metric(
          frame, _HARDWARE_ERROR_METRIC_COL, label, x_range=x_range
        )
      )
  return figures


class SummaryPlot:
  """
  Builds a grid of Bokeh continuous-line plots (one per metric) from the
  columnar job table.

  Attributes:
    host_list: Hosts with host_data in the job window (plot line order).
    jid: Job id.
    jt: ``ColumnarJobTable`` supplying the series.
    hc: Host -> line color map, set by ``plot``.
    last_series: ``SummarySeries`` of the last ``plot`` call (golden-corpus
      capture and tests), or None.
  """

  def __init__(self, jt: Any) -> None:
    """
    Store the job table and its host list.

    Args:
      jt (Any): ``ColumnarJobTable`` (``host_list``, ``schema``,
        aggregates).

    Returns:
      None

    Examples:
      >>> SummaryPlot(jt)  # doctest: +SKIP
    """
    self.jt = jt
    self.jid = getattr(jt, "jid", None)
    self.host_list = list(getattr(jt, "host_list", []) or [])
    self.hc: dict[str, str] = {}
    self.last_series = None

  def plot_metric(
    self,
    frame: SeriesFrame,
    metric: Any,
    label: Any,
    y_range_end: Any | None = None,
    x_range: Any | None = None,
    variable_description: Any | None = None,
  ) -> Any:
    """
    Create one Bokeh figure with continuous per-host lines and scatter hits.

    Uses one multi_line glyph plus one scatter (HoverTool on data) and a
    separate HoverTool on a small "?" marker for metric documentation.

    Args:
      frame (SeriesFrame): Series frame holding ``metric``.
      metric (Any): Column name.
      label (Any): Y-axis label.
      y_range_end (Any | None): Explicit upper bound, or None.
      x_range (Any | None): Shared ``Range1d``, or None.
      variable_description (Any | None): Help text override.

    Returns:
      Any: Bokeh figure.

    Examples:
      >>> SummaryPlot(jt).plot_metric(frame, "cpu", "CPU")  # doctest: +SKIP
    """
    s = time.time()
    with _profile_phase("plot_series"):
      values = np.asarray(frame.columns[metric], dtype=np.float64)
      host_names = np.array(frame.host_names(), dtype=object)
      times_local = timestamps_as_cluster_naive(frame.time_us)
      y_range_start, y_range_end = non_degenerate_y_range_for_series(
        values, y_range_end=y_range_end
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
    plot = figure(**plot_kwargs)
    plot.xaxis.ticker.desired_num_ticks = 5
    plot.yaxis.axis_label_text_font_size = "9pt"
    plot.xaxis.axis_label_text_font_size = "9pt"
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    xs_list = []
    ys_list = []
    colors_list = []
    with _profile_phase("plot_series"):
      for h in self.host_list:
        host_key = str(h)
        sel = np.nonzero(host_names == host_key)[0]
        if sel.size == 0:
          xs_list.append([])
          ys_list.append([])
        else:
          order = sel[np.argsort(frame.time_us[sel], kind="stable")]
          # Keep NaN samples so multi_line breaks segments across gaps.
          xi, yi = _continuous_polyline_xy(times_local[order], values[order])
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

    with _profile_phase("plot_series"):
      keep = ~np.isnan(values)
      order = np.lexsort((frame.time_us[keep], host_names[keep].astype(str)))
      idx = np.nonzero(keep)[0][order]
      scatter_data: dict[str, Any] = {
        "host": host_names[idx].tolist(),
        "time": times_local[idx],
        metric: values[idx],
      }
      scatter_data.update(
        hover_plain_columns(times_local[idx], {metric: values[idx]})
      )
    scatter_source = ColumnDataSource(data=scatter_data)
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
    from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
      is_job_scoped_host_data_type,
      show_shared_host_wide_help_footer,
    )

    # Map summary column -> host_data.type for residual footer gating.
    _metric_typ = None
    for spec in _SUMMARY_SINGLE_SPECS:
      if len(spec) >= 4 and spec[3] == metric:
        _metric_typ = spec[0]
        break
    _surface_job_scoped = None
    if _metric_typ is not None:
      _surface_job_scoped = is_job_scoped_host_data_type(_metric_typ)
    elif metric in ("mem", "cpu") or str(metric).startswith(
      ("nv_", "amd_", "intel_", "gpu")
    ):
      _surface_job_scoped = True

    add_job_detail_bokeh_help_marker(
      plot,
      help_body,
      researcher_use,
      is_shared_node=show_shared_host_wide_help_footer(
        is_shared_node=bool(getattr(self.jt, "_shared_node", False)),
        typ=_metric_typ,
        surface_job_scoped=_surface_job_scoped,
      ),
    )
    log.debug("time to plot %s: %s", metric, time.time() - s)
    return plot

  def plot(self) -> Any:
    """
    Build the merged series, one figure per rendered metric, and return a
    two-column ``gridplot``.

    Returns:
      Any: Bokeh ``GridPlot``.

    Raises:
      ValueError: ``MSG_NO_METRIC_DATA`` when the job has no series or no
        renderable subplot.

    Examples:
      >>> SummaryPlot(jt).plot()  # doctest: +SKIP
    """
    from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
      build_summary_series,
    )

    palette = _cycled_d3_category20_palette(len(self.host_list))
    self.hc = {
      hostname: palette[i] for i, hostname in enumerate(self.host_list)
    }
    log.debug("Host Count: %s", len(self.host_list))

    with _profile_phase("plot_series"):
      series = build_summary_series(self.jt)
    self.last_series = series

    # Match plot series (cluster-naive wall clock) so Range1d aligns with glyphs.
    x_start_l, x_end_l = job_window_bounds_local(self.jt)
    if x_start_l is not None and x_end_l is not None:
      x_range = Range1d(
        x_start_l.replace(tzinfo=None), x_end_l.replace(tzinfo=None)
      )
    else:
      x_range = None

    plots = []
    for name, label, y_top in series.render_specs:
      with _profile_phase("bokeh"):
        plots += [
          self.plot_metric(
            series.frame, name, label, y_range_end=y_top, x_range=x_range
          )
        ]

    plots.extend(plot_hardware_error_rate_figures(self, x_range))

    if not plots:
      raise ValueError(MSG_NO_METRIC_DATA)
    # Root GridPlot must stretch horizontally so the grid uses the full embed width
    # (child figures already use stretch_width via figure_embed_kw).
    with _profile_phase("bokeh"):
      return gridplot(
        plots, ncols=min(2, len(plots)), sizing_mode="stretch_width"
      )


def plot_and_reason_summary_from_jid_table(jt: Any) -> Any:
  """
  Build summary plot and return (figure_or_none, unavailable_reason_or_none).

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: ``(figure, None)`` or ``(None, reason)``.

  Examples:
    >>> plot_and_reason_summary_from_jid_table(jt)  # doctest: +SKIP
  """
  t0 = time.monotonic()
  try:
    fig = SummaryPlot(jt).plot()
  except Exception as plot_exc:
    host_time = jt.get_host_time()
    if host_time.empty or not jt.host_list:
      return (
        None,
        "No hosts/timestamps found in host_data for this job/time range",
      )

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
    log.debug(
      "summary plot success jid=%s elapsed_s=%.3f",
      getattr(jt, "jid", None),
      time.monotonic() - t0,
    )
    return (fig, None)
