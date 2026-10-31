"""
K3 roofline points: CPU and GPU ``(host[, dev], time, flops_gf, bw_gb)``
samples from a ColumnarJobTable.

Ports ``roofline._get_flops_bw_df_and_reason`` /
``_get_gpu_flops_bw_df_and_reason`` and ``roofline_peaks._max_converted_sum_val``
to aggregate tables; probe orders, attempt logs and unavailable reasons are
kept text-identical so the persisted ``unavailable_reason`` does not change.

Attributes:
  GPU_ROOFLINE_BW_AXIS_MEMORY: Measured-axis tag for HBM bandwidth.
  GPU_ROOFLINE_BW_AXIS_LINK: Measured-axis tag for PCIe / NVLink bytes.
  _GPU_BYTES_TO_GIB: GiB/s conversion shared with ``host_roofline_peak``.
  _NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS: NVIDIA directional link byte arcs.
  _INTEL_GPU_LINK_EVENTS: Intel PCIe + Xe Link byte arcs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable
from hpcperfstats.analysis.metrics.lib.columnar.series_frame import (
  SeriesFrame,
  inner_join,
  outer_join_sum,
)
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  bw_table,
  combine_bw,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  AMD_DF_STATS_TYPES,
  INTEL_FP_ARITH_ALL_EVENTS,
  INTEL_LEGACY_SSE_FLOP_EVENTS,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  amd_df_bw_event_conv_tries,
  amd_df_type_names,
  amd_pmc_type_names,
  arm_dram_bw_event_names,
  arm_est_flops_event_names,
  arm_imc_types_probe_order,
  core_pmc_types_probe_order,
  dram_cas_read_write_pairs,
  fp_ops_retired_event_names,
  hbm_cas_read_write_pairs,
  host_cpu_hw_type_names,
  host_roofline_peak_type_names,
  imc_types_probe_order,
)

GPU_ROOFLINE_BW_AXIS_MEMORY = "memory_bw"
GPU_ROOFLINE_BW_AXIS_LINK = "pcie_nvlink"
_GPU_BYTES_TO_GIB = 1 / (1024**3)
_NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS = (
  "gpu_pcie_tx_bytes",
  "gpu_pcie_rx_bytes",
  "gpu_nvlink_tx_bytes",
  "gpu_nvlink_rx_bytes",
)
_INTEL_GPU_LINK_EVENTS = (
  "gpu_pcie_tx_bytes",
  "gpu_pcie_rx_bytes",
  "gpu_xe_link_tx_bytes",
  "gpu_xe_link_rx_bytes",
)


@dataclass
class RooflinePoints:
  """
  Roofline samples (one row per ``(host[, dev], time)``).

  Attributes:
    host_id: Host id per row.
    time_us: Sample time per row.
    flops_gf: GFLOP/s per row.
    bw_gb: GB/s (or GiB/s on the GPU path) per row.
    hosts: Host names by id.
    dev_id: Signed dev id per row (GPU path) or None.
    devs: Device names by ``abs(dev_id)``.
  """

  host_id: np.ndarray
  time_us: np.ndarray
  flops_gf: np.ndarray
  bw_gb: np.ndarray
  hosts: list[str]
  dev_id: np.ndarray | None = None
  devs: list[str] | None = None

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Rows.

    Examples:
      >>> import numpy as np
      >>> len(
      ...   RooflinePoints(
      ...     np.array([0]),
      ...     np.array([1]),
      ...     np.array([1.0]),
      ...     np.array([2.0]),
      ...     ["h"],
      ...   )
      ... )
      1
    """
    return int(self.host_id.shape[0])

  @property
  def empty(self) -> bool:
    """
    True without rows.

    Returns:
      bool: Emptiness.

    Examples:
      >>> import numpy as np
      >>> RooflinePoints(
      ...   np.array([], dtype=np.int64),
      ...   np.array([], dtype=np.int64),
      ...   np.array([]),
      ...   np.array([]),
      ...   [],
      ... ).empty
      True
    """
    return self.host_id.shape[0] == 0

  def host_names(self) -> list[str]:
    """
    Host name per row.

    Returns:
      list[str]: Names.

    Examples:
      >>> import numpy as np
      >>> RooflinePoints(
      ...   np.array([0]),
      ...   np.array([1]),
      ...   np.array([1.0]),
      ...   np.array([2.0]),
      ...   ["h"],
      ... ).host_names()
      ['h']
    """
    return [self.hosts[int(i)] for i in self.host_id]

  def dev_names(self) -> list[str]:
    """
    Device name per row (``""`` without devices; ``j:`` copies keep the
    prefix).

    Returns:
      list[str]: Names.

    Examples:
      >>> import numpy as np
      >>> RooflinePoints(
      ...   np.array([0]),
      ...   np.array([1]),
      ...   np.array([1.0]),
      ...   np.array([2.0]),
      ...   ["h"],
      ... ).dev_names()
      ['']
    """
    if self.dev_id is None or self.devs is None:
      return [""] * len(self)
    out = []
    for d in self.dev_id.tolist():
      name = self.devs[abs(int(d))]
      out.append(f"j:{name}" if int(d) < 0 else name)
    return out


def _agg_arc(
  jt: Any, typ: str, events: Sequence[str], conv: float
) -> tuple[AggTable, str]:
  """
  ``roofline._aggregate_arc``.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    events (Sequence[str]): Events.
    conv (float): Multiplier.

  Returns:
    tuple[AggTable, str]: Table and source tag.

  Examples:
    >>> _agg_arc(jt, "amd64_pmc", ["FLOPS"], 1e-9)  # doctest: +SKIP
  """
  return jt.get_aggregate(typ, "arc", list(events), conv), "arc"


def _agg_value(
  jt: Any, typ: str, events: Sequence[str], conv: float
) -> tuple[AggTable, str]:
  """
  ``roofline._aggregate_value``.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    events (Sequence[str]): Events.
    conv (float): Multiplier.

  Returns:
    tuple[AggTable, str]: Table and source tag.

  Examples:
    >>> _agg_value(jt, "nvidia_gpu", ["gpu_flops_rate"], 1e-9)  # doctest: +SKIP
  """
  return jt.get_aggregate(typ, "value", list(events), conv), "value"


def _agg_arc_by_dev(
  jt: Any, typ: str, events: Sequence[str], conv: float
) -> tuple[AggTable, str]:
  """
  ``roofline._aggregate_arc_by_dev``.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    events (Sequence[str]): Events.
    conv (float): Multiplier.

  Returns:
    tuple[AggTable, str]: Per-device table and source tag.

  Examples:
    >>> _agg_arc_by_dev(jt, "nvidia_gpu", ["gpu_flops"], 1e-9)  # doctest: +SKIP
  """
  return jt.get_aggregate(
    typ, "arc", list(events), conv, group_by_dev=True
  ), "arc"


def _agg_value_by_dev(
  jt: Any, typ: str, events: Sequence[str], conv: float
) -> tuple[AggTable, str]:
  """
  ``roofline._aggregate_value_by_dev``.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    events (Sequence[str]): Events.
    conv (float): Multiplier.

  Returns:
    tuple[AggTable, str]: Per-device table and source tag.

  Examples:
    >>> _agg_value_by_dev(
    ...   jt, "nvidia_gpu", ["gpu_mem_bw_bytes_rate"], 1e-9
    ... )  # doctest: +SKIP
  """
  return jt.get_aggregate(
    typ, "value", list(events), conv, group_by_dev=True
  ), "value"


def _merge_weighted_event_arcs(
  jt: Any,
  intel_typ: str,
  event_weights: Sequence[tuple[str, float]],
  attempts: list[str],
  label: str,
) -> AggTable | None:
  """
  ``roofline._merge_weighted_event_arcs``: outer-join sum of weighted
  per-event GFLOP/s; None when empty or all zero.

  Args:
    jt (Any): ColumnarJobTable.
    intel_typ (str): Core PMC type.
    event_weights (Sequence[tuple[str, float]]): ``(event, flop weight)``.
    attempts (list[str]): Attempt log (appended).
    label (str): Log label.

  Returns:
    AggTable | None: Summed table.

  Examples:
    >>> _merge_weighted_event_arcs(
    ...   jt, "intel_8pmc3", [], [], "x"
    ... ) is None  # doctest: +SKIP
    True
  """
  merged: AggTable | None = None
  for ev, weight in event_weights:
    agg, src = _agg_arc(jt, intel_typ, [ev], 1e-9 * weight)
    attempts.append(f"{label}:{intel_typ} event={ev} rows={len(agg)} src={src}")
    if agg.empty:
      continue
    merged = agg if merged is None else outer_join_sum(merged, agg)
  if merged is None or merged.empty:
    return None
  if not np.nan_to_num(merged.sum_val, nan=0.0).any():
    return None
  return merged


def _intel_fp_arith_flops(jt: Any, attempts: list[str]) -> AggTable | None:
  """
  ``roofline._intel_fp_arith_flops_gf``.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.

  Returns:
    AggTable | None: FLOPS table.

  Examples:
    >>> _intel_fp_arith_flops(jt, [])  # doctest: +SKIP
  """
  fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)
  for core_typ in core_pmc_types_probe_order():
    cand, src = _agg_arc(jt, core_typ, fp_events, 1e-9)
    attempts.append(
      f"intel_fp_arith:{core_typ} rows(flops={len(cand)}) src={src}"
    )
    if not cand.empty:
      return cand
  return None


def _intel_legacy_sse_flops(jt: Any, attempts: list[str]) -> AggTable | None:
  """
  ``roofline._intel_legacy_sse_flops_gf``.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.

  Returns:
    AggTable | None: FLOPS table.

  Examples:
    >>> _intel_legacy_sse_flops(jt, [])  # doctest: +SKIP
  """
  for core_typ in core_pmc_types_probe_order():
    merged = _merge_weighted_event_arcs(
      jt, core_typ, INTEL_LEGACY_SSE_FLOP_EVENTS, attempts, "intel_legacy_sse"
    )
    if merged is not None:
      return merged
  return None


def _intel_imc_bw(jt: Any, attempts: list[str]) -> AggTable | None:
  """
  ``roofline._intel_imc_bw_gb``: DDR + HBM CAS bandwidth per IMC type.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.

  Returns:
    AggTable | None: Bandwidth table.

  Examples:
    >>> _intel_imc_bw(jt, [])  # doctest: +SKIP
  """
  conv = 64 / (1024**3)
  for imc_typ in imc_types_probe_order():
    dram = None
    for read_ev, write_ev in dram_cas_read_write_pairs():
      agg, src = _agg_arc(jt, imc_typ, [read_ev, write_ev], conv)
      attempts.append(
        f"{imc_typ} rows(bw={len(agg)}) events=({read_ev},{write_ev}) src(bw={src})"
      )
      dram = bw_table(agg)
      if dram is not None:
        break
    hbm = None
    for read_ev, write_ev in hbm_cas_read_write_pairs():
      agg, src = _agg_arc(jt, imc_typ, [read_ev, write_ev], conv)
      attempts.append(
        f"{imc_typ} rows(hbm_bw={len(agg)}) events=({read_ev},{write_ev}) src(bw={src})"
      )
      hbm = bw_table(agg)
      if hbm is not None:
        break
    combined = combine_bw(dram, hbm)
    if combined is not None:
      return combined
  return None


def _arm_dcgm_flops_bw(
  jt: Any, attempts: list[str]
) -> tuple[AggTable | None, AggTable | None]:
  """
  ``roofline._arm_dcgm_flops_bw``.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.

  Returns:
    tuple[AggTable | None, AggTable | None]: FLOPS and bandwidth tables.

  Examples:
    >>> _arm_dcgm_flops_bw(jt, [])  # doctest: +SKIP
  """
  for hw_typ in host_cpu_hw_type_names():
    for flop_ev in arm_est_flops_event_names():
      flops, fsrc = _agg_arc(jt, hw_typ, [flop_ev], 1e-9)
      bw, bsrc = _agg_arc(
        jt, hw_typ, list(arm_dram_bw_event_names()), 1 / (1024**3)
      )
      attempts.append(
        f"arm_dcgm:{hw_typ} rows(flops={len(flops)}, bw={len(bw)}) src(flops={fsrc}, bw={bsrc})"
      )
      if not flops.empty and not bw.empty:
        return flops, bw
  return None, None


def _arm_imc_bw(jt: Any, attempts: list[str]) -> AggTable | None:
  """
  ``roofline._arm_imc_bw_gb``.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.

  Returns:
    AggTable | None: Bandwidth table.

  Examples:
    >>> _arm_imc_bw(jt, [])  # doctest: +SKIP
  """
  conv = 64 / (1024**3)
  for imc_typ in arm_imc_types_probe_order():
    for read_ev, write_ev in dram_cas_read_write_pairs():
      agg, src = _agg_arc(jt, imc_typ, [read_ev, write_ev], conv)
      attempts.append(f"arm_imc:{imc_typ} rows(bw={len(agg)}) src(bw={src})")
      if not agg.empty:
        return agg
  return None


def cpu_roofline_points(jt: Any) -> tuple[RooflinePoints | None, str | None]:
  """
  ``roofline._get_flops_bw_df_and_reason``: inner join of the base
  ``(host, time)`` rows with FLOPS and bandwidth tables.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    tuple[RooflinePoints | None, str | None]: Points or an unavailable
    reason.

  Examples:
    >>> cpu_roofline_points(jt)  # doctest: +SKIP
  """
  base = jt.get_host_time()
  if base.empty or not getattr(jt, "host_list", None):
    return (
      None,
      "No hosts/timestamps found in host_data for this job/time range",
    )
  flops: AggTable | None = None
  bw: AggTable | None = None
  attempts: list[str] = []
  for pmc_typ in amd_pmc_type_names():
    for flop_ev in fp_ops_retired_event_names():
      agg_flops, fsrc = _agg_arc(jt, pmc_typ, [flop_ev], 1e-9)
      for df_typ in amd_df_type_names():
        tries = (
          amd_df_bw_event_conv_tries()[:1]
          if df_typ in AMD_DF_STATS_TYPES
          else amd_df_bw_event_conv_tries()[::-1]
        )
        for bw_events, bw_conv in tries:
          agg_bw, bsrc = _agg_arc(jt, df_typ, list(bw_events), bw_conv)
          attempts.append(
            f"amd rows(flops={len(agg_flops)}, bw={len(agg_bw)}) src(flops={fsrc}, bw={bsrc})"
          )
          if not agg_flops.empty and not agg_bw.empty:
            flops, bw = agg_flops, agg_bw
            break
        if flops is not None and bw is not None:
          break
      if flops is not None and bw is not None:
        break
    if flops is not None and bw is not None:
      break
  if flops is None or bw is None:
    if flops is None:
      flops = _intel_fp_arith_flops(jt, attempts)
    if flops is None:
      flops = _intel_legacy_sse_flops(jt, attempts)
    if bw is None:
      bw = _intel_imc_bw(jt, attempts)
    if bw is None:
      bw = _arm_imc_bw(jt, attempts)
  if flops is None or bw is None:
    arm_flops, arm_bw = _arm_dcgm_flops_bw(jt, attempts)
    if flops is None:
      flops = arm_flops
    if bw is None:
      bw = arm_bw
  if flops is None or bw is None:
    attempted = "; ".join(attempts)
    reason = (
      "Missing roofline counters in host_data (need FLOPS + memory-bandwidth counters). "
      f"Attempted: {attempted}"
    )
    has_cpu_counter_metrics = any(
      t in a for t in host_cpu_hw_type_names() for a in attempts
    )
    has_any_imc_or_df = any(
      t.startswith("amd") or "imc" in t
      for t in [att.split(":")[0] for att in attempts if att]
    )
    if has_cpu_counter_metrics and not has_any_imc_or_df:
      reason = (
        "Roofline not available on this job: cpu_counter_metrics FLOPS "
        "are present (e.g. via DCGM backend), but no DRAM bandwidth "
        "source (AMD DF or Intel/ARM DRAM CAS counters) was found "
        "in host_data for these hosts."
      )
    return None, reason
  frame = SeriesFrame.from_base(base.host_id, base.time_us, base.hosts)
  base_tbl = AggTable(frame.host_id, frame.time_us, np.zeros(len(frame)))
  li, fi = inner_join(base_tbl, flops)
  step = AggTable(frame.host_id[li], frame.time_us[li], flops.sum_val[fi])
  li2, bi = inner_join(step, bw)
  if li2.size == 0:
    return (
      None,
      "Roofline counters found but no overlapping host/time samples after merge",
    )
  return (
    RooflinePoints(
      step.host_id[li2],
      step.time_us[li2],
      step.sum_val[li2],
      bw.sum_val[bi],
      base.hosts,
    ),
    None,
  )


def _merge_gpu_flops_bw(
  flops: AggTable, bw: AggTable, hosts: list[str], devs: list[str]
) -> RooflinePoints | None:
  """
  ``roofline._merge_gpu_flops_bw_on_base``: inner join on ``(host, dev,
  time)`` when both tables carry devices, else ``(host, time)``.

  Args:
    flops (AggTable): FLOPS rows.
    bw (AggTable): Bandwidth rows.
    hosts (list[str]): Host names.
    devs (list[str]): Device names.

  Returns:
    RooflinePoints | None: Joined points or None when empty.

  Examples:
    >>> _merge_gpu_flops_bw(AggTable.zero(), AggTable.zero(), [], []) is None
    True
  """
  if flops.empty or bw.empty:
    return None
  by_dev = flops.dev_id is not None and bw.dev_id is not None
  li, ri = inner_join(flops, bw, by_dev=by_dev)
  if li.size == 0:
    return None
  return RooflinePoints(
    flops.host_id[li],
    flops.time_us[li],
    flops.sum_val[li],
    bw.sum_val[ri],
    hosts,
    flops.dev_id[li] if by_dev else None,
    devs if by_dev else None,
  )


def _has_usable_bw(points: RooflinePoints | None) -> bool:
  """
  ``roofline._gpu_roofline_df_has_usable_bw``: a finite positive BW sample.

  Args:
    points (RooflinePoints | None): Points.

  Returns:
    bool: Usable.

  Examples:
    >>> _has_usable_bw(None)
    False
  """
  if points is None or points.empty:
    return False
  bw = points.bw_gb
  return bool(((bw > 0) & np.isfinite(bw)).any())


def _try_gpu_merge(
  jt: Any,
  gpu_typ: str,
  source_tag: str,
  flops_fn: Callable[..., tuple[AggTable, str]],
  flops_events: Sequence[str],
  flops_conv: float,
  bw_fn: Callable[..., tuple[AggTable, str]],
  bw_events: Sequence[str],
  bw_conv: float,
) -> tuple[RooflinePoints | None, str, str | None]:
  """
  ``roofline._try_gpu_roofline_flops_bw_merge``.

  Args:
    jt (Any): ColumnarJobTable.
    gpu_typ (str): GPU type.
    source_tag (str): Attempt label.
    flops_fn (Callable): FLOPS aggregator.
    flops_events (Sequence[str]): FLOPS events.
    flops_conv (float): FLOPS multiplier.
    bw_fn (Callable): Bandwidth aggregator.
    bw_events (Sequence[str]): Bandwidth events.
    bw_conv (float): Bandwidth multiplier.

  Returns:
    tuple[RooflinePoints | None, str, str | None]: Points, log line, miss
    reason.

  Examples:
    >>> _try_gpu_merge(
    ...   jt,
    ...   "nvidia_gpu",
    ...   "mem_value",
    ...   _agg_arc_by_dev,
    ...   ["gpu_flops"],
    ...   1e-9,
    ...   _agg_value_by_dev,
    ...   ["gpu_mem_bw_bytes_rate"],
    ...   1.0,
    ... )  # doctest: +SKIP
  """
  flops, fsrc = flops_fn(jt, gpu_typ, flops_events, flops_conv)
  bw, bsrc = bw_fn(jt, gpu_typ, bw_events, bw_conv)
  line = (
    f"{gpu_typ}:{source_tag} rows(flops={len(flops)}, bw={len(bw)}) "
    f"src(flops={fsrc}, bw={bsrc})"
  )
  points = _merge_gpu_flops_bw(flops, bw, jt.frame.hosts, jt.frame.devs)
  if points is not None and _has_usable_bw(points):
    return points, line, None
  miss = None
  if not flops.empty and not bw.empty:
    if points is not None and not _has_usable_bw(points):
      miss = (
        f"{gpu_typ}: {source_tag} overlapped but BW samples were non-positive"
      )
    else:
      miss = f"{gpu_typ}: counters found in {source_tag} but no overlapping host/device/time samples"
  return None, line, miss


def _try_gpu_axis(
  jt: Any,
  attempts: list[str],
  missing: list[str],
  gpu_typ: str,
  source_tag: str,
  flops_fn: Callable[..., tuple[AggTable, str]],
  bw_fn: Callable[..., tuple[AggTable, str]],
  bw_events: Sequence[str],
  bw_axis: str,
) -> tuple[RooflinePoints, None, str] | None:
  """
  ``roofline._try_gpu_roofline_axis_attempt``: cumulative ``gpu_flops`` arc,
  then the ``gpu_flops_rate`` estimate.

  Args:
    jt (Any): ColumnarJobTable.
    attempts (list[str]): Attempt log.
    missing (list[str]): Miss reasons.
    gpu_typ (str): GPU type.
    source_tag (str): Attempt label.
    flops_fn (Callable): FLOPS aggregator for the arc attempt.
    bw_fn (Callable): Bandwidth aggregator.
    bw_events (Sequence[str]): Bandwidth events.
    bw_axis (str): Axis tag on success.

  Returns:
    tuple[RooflinePoints, None, str] | None: Success triple or None.

  Examples:
    >>> _try_gpu_axis(
    ...   jt,
    ...   [],
    ...   [],
    ...   "nvidia_gpu",
    ...   "mem_value",
    ...   _agg_arc_by_dev,
    ...   _agg_value_by_dev,
    ...   ["gpu_mem_bw_bytes_rate"],
    ...   "memory_bw",
    ... )  # doctest: +SKIP
  """
  points, line, miss = _try_gpu_merge(
    jt,
    gpu_typ,
    source_tag,
    flops_fn,
    ["gpu_flops"],
    1e-9,
    bw_fn,
    list(bw_events),
    _GPU_BYTES_TO_GIB,
  )
  attempts.append(line)
  if points is not None:
    return points, None, bw_axis
  if miss:
    missing.append(miss)
  points, line, miss = _try_gpu_merge(
    jt,
    gpu_typ,
    f"{source_tag}_flops_rate",
    _agg_value_by_dev,
    ["gpu_flops_rate"],
    1e-9,
    bw_fn,
    list(bw_events),
    _GPU_BYTES_TO_GIB,
  )
  attempts.append(line)
  if points is not None:
    return points, None, bw_axis
  if miss:
    missing.append(miss)
  return None


def gpu_roofline_points(
  jt: Any,
) -> tuple[RooflinePoints | None, str | None, str | None]:
  """
  ``roofline._get_gpu_flops_bw_df_and_reason``: memory BW first (NVIDIA
  then AMD), then NVIDIA aggregate / directional link bytes, then Intel.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    tuple[RooflinePoints | None, str | None, str | None]: Points, reason,
    bandwidth axis.

  Examples:
    >>> gpu_roofline_points(jt)  # doctest: +SKIP
  """
  base = jt.get_host_time()
  if base.empty or not getattr(jt, "host_list", None):
    return (
      None,
      "No hosts/timestamps found in host_data for this job/time range",
      None,
    )
  attempts: list[str] = []
  missing: list[str] = []
  for gpu_typ in ("nvidia_gpu", "amd_gpu"):
    hit = _try_gpu_axis(
      jt,
      attempts,
      missing,
      gpu_typ,
      "mem_value",
      _agg_arc_by_dev,
      _agg_value_by_dev,
      ["gpu_mem_bw_bytes_rate"],
      GPU_ROOFLINE_BW_AXIS_MEMORY,
    )
    if hit is not None:
      return hit
  for gpu_typ, tag, events in (
    ("nvidia_gpu", "link_arc_total", ["gpu_io_link_total_bytes"]),
    (
      "nvidia_gpu",
      "link_arc_directional",
      list(_NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS),
    ),
    ("intel_gpu", "link_arc_intel", list(_INTEL_GPU_LINK_EVENTS)),
  ):
    hit = _try_gpu_axis(
      jt,
      attempts,
      missing,
      gpu_typ,
      tag,
      _agg_arc_by_dev,
      _agg_arc_by_dev,
      events,
      GPU_ROOFLINE_BW_AXIS_LINK,
    )
    if hit is not None:
      return hit
  detail = "; ".join(missing) if missing else "; ".join(attempts)
  return (
    None,
    "Missing strict GPU roofline counters in host_data "
    "(need gpu_flops or gpu_flops_rate plus gpu_mem_bw_bytes_rate or "
    "PCIe/NVLink/Xe Link bytes on nvidia_gpu, amd_gpu, or intel_gpu). "
    f"Attempted: {detail}",
    None,
  )


def max_converted_sum_val(jt: Any, event: str, conv: float) -> float | None:
  """
  ``roofline_peaks._max_converted_sum_val``: max finite per-sample sum of a
  ``host_roofline_peak`` event over ``value`` then ``arc``.

  Args:
    jt (Any): ColumnarJobTable.
    event (str): Peak event name.
    conv (float): Multiplier.

  Returns:
    float | None: Maximum or None.

  Examples:
    >>> max_converted_sum_val(
    ...   jt, "cpu_peak_fp64_flops_per_s", 1e-9
    ... )  # doctest: +SKIP
  """
  for value_column in ("value", "arc"):
    for peak_typ in host_roofline_peak_type_names():
      try:
        agg = jt.get_aggregate(peak_typ, value_column, [event], conv)
      except Exception:
        continue
      if agg.empty:
        continue
      finite = agg.sum_val[np.isfinite(agg.sum_val)]
      if finite.size == 0:
        continue
      return float(np.max(finite))
  return None
