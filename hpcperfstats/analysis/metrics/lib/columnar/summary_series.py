"""
K3 summary series: the data half of ``SummaryPlot.plot`` over a
ColumnarJobTable.

``build_summary_series`` reproduces, column by column, the merged
``(host, time, metric…)`` frame and the ``(name, label, y_top)`` render list
that ``SummaryPlot.plot`` assembled with pandas merges, so the Bokeh layer
only turns arrays into glyphs. The spec tables (``_SUMMARY_SINGLE_SPECS``,
first-win tries, order keys, hardware-error counters) stay in
``summaryplot`` and are imported lazily to avoid an import cycle.

Attributes:
  WATT_UNIQUE_ROUND_DECIMALS: Rounding before the unique-watt collapse
    (``dcgm_cpu_power_agg``).
  FREQ_GHZ_SCALE: ``freq = FREQ_GHZ_SCALE * acycles / mcycles``.
  FREQ_PLOT_MIN_MAX: ``freq`` is plotted only when its maximum exceeds this.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import segments as sg
from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable
from hpcperfstats.analysis.metrics.lib.columnar.series_frame import (
  SeriesFrame,
  outer_join_sum,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  AMD_DF_STATS_TYPES,
  CHA_TYPENAME_PRIORITY,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  amd_df_bw_event_conv_tries,
  amd_df_types_probe_order,
  dram_cas_read_write_pairs,
  events_probe_names,
  hbm_cas_read_write_pairs,
  imc_types_probe_order,
  type_probe_names,
)

WATT_UNIQUE_ROUND_DECIMALS = 3
FREQ_GHZ_SCALE = 2.7
FREQ_PLOT_MIN_MAX = 500.0


@dataclass
class SummarySeries:
  """
  Output of :func:`build_summary_series`.

  Attributes:
    frame: Merged per-host series (``host``, ``time`` base plus metric
      columns in merge order).
    render_specs: ``(name, label, y_top)`` rows in subplot order.
    metrics: ``(typ, val, events, name, conv, label)`` rows considered
      (``_summary_metric_specs`` plus ``freq`` when derived).
  """

  frame: SeriesFrame
  render_specs: list[tuple[str, str, float | None]] = field(
    default_factory=list
  )
  metrics: list[tuple[Any, ...]] = field(default_factory=list)


def _sp() -> Any:
  """
  Lazy import of ``summaryplot`` (spec tables) to avoid an import cycle.

  Returns:
    Any: The ``summaryplot`` module.

  Examples:
    >>> hasattr(_sp(), "_SUMMARY_SINGLE_SPECS")
    True
  """
  from hpcperfstats.analysis.metrics.lib.plot import summaryplot

  return summaryplot


def feasible(schema: Any, typ: str, events: Sequence[str] | None) -> bool:
  """
  ``_summary_type_events_feasible``: schema knows an event of ``typ``.

  Args:
    schema (Any): ``jt.schema`` (dict) or anything else (= feasible).
    typ (str): Type alias.
    events (Sequence[str] | None): Event names.

  Returns:
    bool: Whether a read is worth trying.

  Examples:
    >>> feasible({"host_cpu": ["user"]}, "host_cpu", ["user"])
    True
    >>> feasible({"host_cpu": ["user"]}, "host_cpu", ["nice"])
    False
  """
  if not isinstance(schema, dict) or not schema:
    return True
  if not events:
    return any(t in schema for t in type_probe_names(typ))
  probed = set(events_probe_names(list(events), typ=typ))
  return any(
    t in schema and probed.intersection(schema[t])
    for t in type_probe_names(typ)
  )


def agg_if_feasible(
  jt: Any,
  typ: str,
  val_col: str,
  events: Sequence[str],
  conv: float,
  *,
  group_by_dev: bool = False,
) -> AggTable:
  """
  ``_get_agg_if_feasible``: first probe type with rows, or an empty table.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type alias.
    val_col (str): Column.
    events (Sequence[str]): Event names.
    conv (float): Multiplier.
    group_by_dev (bool): Per-device rows.

  Returns:
    AggTable: Aggregate rows.

  Examples:
    >>> agg_if_feasible(jt, "host_cpu", "arc", ["user"], 0.01)  # doctest: +SKIP
  """
  schema = jt.schema if isinstance(getattr(jt, "schema", None), dict) else {}
  if not feasible(schema, typ, events):
    return AggTable.zero(by_dev=group_by_dev)
  ev = events_probe_names(list(events), typ=typ)
  for t in type_probe_names(typ):
    if not feasible(schema, t, events):
      continue
    agg = jt.get_aggregate(t, val_col, ev, conv, group_by_dev=group_by_dev)
    if not agg.empty:
      return agg
  return AggTable.zero(by_dev=group_by_dev)


def unique_watt_sum(agg: AggTable) -> AggTable:
  """
  ``sum_unique_watt_values_per_host_time``: per ``(host, time)`` sum of
  distinct rounded finite watt paints across device rows.

  Args:
    agg (AggTable): Per-device rows (``dev_id`` set).

  Returns:
    AggTable: Host-level rows (groups without a finite paint vanish).

  Examples:
    >>> import numpy as np
    >>> t = AggTable(
    ...   np.array([0, 0, 0]),
    ...   np.array([1, 1, 1]),
    ...   np.array([45.0, 45.0, 30.0]),
    ...   np.array([1, 2, 3]),
    ... )
    >>> unique_watt_sum(t).sum_val.tolist()
    [75.0]
  """
  if agg.empty:
    return AggTable.zero()
  fin = np.isfinite(agg.sum_val)
  if not fin.any():
    return AggTable.zero()
  host = agg.host_id[fin].astype(np.int64)
  time = agg.time_us[fin].astype(np.int64)
  vals = np.round(agg.sum_val[fin], WATT_UNIQUE_ROUND_DECIMALS)
  axis = np.unique(time)
  key = sg.composite_key(host, np.searchsorted(axis, time).astype(np.int64))
  # Unique (key, value) pairs, then sum values per key.
  pair = np.unique(np.stack([key, vals.view(np.int64)], axis=1), axis=0)
  keys_u, sums, _c = sg.group_sum(pair[:, 0], pair[:, 1].view(np.float64))
  hosts = (keys_u >> sg.TIME_BITS).astype(np.int64)
  times = axis[keys_u & ((1 << sg.TIME_BITS) - 1)].astype(np.int64)
  return AggTable(hosts, times, sums)


def dcgm_cpu_power_util_agg(jt: Any, conv: float = 1.0) -> AggTable:
  """
  ``_get_dcgm_cpu_power_util_agg``: Grace socket watts without per-CPU
  replica overcount.

  Args:
    jt (Any): ColumnarJobTable.
    conv (float): Multiplier.

  Returns:
    AggTable: Host-level unique-watt sums.

  Examples:
    >>> dcgm_cpu_power_util_agg(jt)  # doctest: +SKIP
  """
  return unique_watt_sum(
    agg_if_feasible(
      jt,
      "host_cpu_hw",
      "value",
      ["dcgm_cpu_power_util_w"],
      conv,
      group_by_dev=True,
    )
  )


def bw_table(agg: AggTable) -> AggTable | None:
  """
  ``agg_sum_val_to_bw_frame``: the table itself, or None when empty or all
  non-finite.

  Args:
    agg (AggTable): Aggregate rows.

  Returns:
    AggTable | None: Usable table.

  Examples:
    >>> import numpy as np
    >>> bw_table(
    ...   AggTable(np.array([0]), np.array([1]), np.array([np.nan]))
    ... ) is None
    True
  """
  if agg.empty or not np.isfinite(agg.sum_val).any():
    return None
  return agg


def combine_bw(dram: AggTable | None, hbm: AggTable | None) -> AggTable | None:
  """
  ``combine_cas_bw_frames``: outer-join DDR and HBM and sum, or the usable
  side alone, or None.

  Args:
    dram (AggTable | None): DDR CAS bandwidth.
    hbm (AggTable | None): HBM CAS bandwidth.

  Returns:
    AggTable | None: Combined table.

  Examples:
    >>> combine_bw(None, None) is None
    True
  """
  d = bw_table(dram) if dram is not None else None
  h = bw_table(hbm) if hbm is not None else None
  if d is not None and h is not None:
    return bw_table(outer_join_sum(d, h))
  return d if d is not None else h


def _merge_column(frame: SeriesFrame, name: str, agg: AggTable) -> None:
  """
  ``df.merge(agg, how="left")`` then ``df[name] = sum_val``.

  Args:
    frame (SeriesFrame): Target frame.
    name (str): Column name.
    agg (AggTable): Aggregate rows.

  Returns:
    None

  Examples:
    >>> _merge_column(frame, "cpu", agg)  # doctest: +SKIP
  """
  frame.set(name, frame.join_values(agg))


def _merge_first_full_coverage(
  frame: SeriesFrame, jt: Any, name: str, val_col: str, tries: Sequence[Any]
) -> None:
  """
  ``_merge_first_full_coverage``: first try whose column has no NaN sticks.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.
    name (str): Column name.
    val_col (str): Column.
    tries (Sequence[Any]): ``(typ, events, conv)`` rows.

  Returns:
    None

  Examples:
    >>> _merge_first_full_coverage(
    ...   frame, jt, "flops64b", "arc", []
    ... )  # doctest: +SKIP
  """
  for typ, events, conv in tries:
    agg = agg_if_feasible(jt, typ, val_col, list(events), conv)
    if agg.empty:
      continue
    vals = frame.join_values(agg)
    if np.isnan(vals).any():
      continue
    frame.set(name, vals)
    return


def _merge_intel_imc_cas_mbw(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_intel_imc_cas_mbw``: ``mbw`` from the first IMC type whose DDR
  and/or HBM CAS bandwidth covers every base row.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_intel_imc_cas_mbw(frame, jt)  # doctest: +SKIP
  """
  conv = _sp()._CAS_BW_CONV
  for imc_typ in imc_types_probe_order():
    dram = None
    for read_ev, write_ev in dram_cas_read_write_pairs():
      dram = bw_table(
        agg_if_feasible(jt, imc_typ, "arc", [read_ev, write_ev], conv)
      )
      if dram is not None:
        break
    hbm = None
    for read_ev, write_ev in hbm_cas_read_write_pairs():
      hbm = bw_table(
        agg_if_feasible(jt, imc_typ, "arc", [read_ev, write_ev], conv)
      )
      if hbm is not None:
        break
    combined = combine_bw(dram, hbm)
    if combined is None:
      continue
    vals = frame.join_values(combined)
    if np.isnan(vals).any():
      continue
    frame.set("mbw", vals)
    return


def _merge_amd_df_mbw(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_amd_df_mbw``: ``amd_mbw`` from the first AMD DF type/event try
  with full coverage.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_amd_df_mbw(frame, jt)  # doctest: +SKIP
  """
  for df_typ in amd_df_types_probe_order():
    tries = (
      amd_df_bw_event_conv_tries()[:1]
      if df_typ in AMD_DF_STATS_TYPES
      else amd_df_bw_event_conv_tries()[::-1]
    )
    for events, conv in tries:
      agg = agg_if_feasible(jt, df_typ, "arc", list(events), conv)
      if agg.empty:
        continue
      vals = frame.join_values(agg)
      if np.isnan(vals).any():
        continue
      frame.set("amd_mbw", vals)
      return


def _merge_mem_prefer_job(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_summary_mem_prefer_job``: ``mem`` from ``host_mem_job`` else
  ``host_mem`` (partial coverage accepted).

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_mem_prefer_job(frame, jt)  # doctest: +SKIP
  """
  conv = 1 / (1024 * 1024)
  for typ in ("host_mem_job", "host_mem"):
    agg = agg_if_feasible(jt, typ, "value", ["mem_used"], conv)
    if agg.empty:
      continue
    vals = frame.join_values(agg)
    if (~np.isnan(vals)).any():
      frame.set("mem", vals)
      return


def _merge_nvidia_gpu_util(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_nvidia_gpu_util_column``: ``gpu_util`` else ``utilization``,
  sparse coverage kept when any sample exists.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_nvidia_gpu_util(frame, jt)  # doctest: +SKIP
  """
  sp = _sp()
  name = "nv_gpu_util"
  for events in (["gpu_util"], ["utilization"]):
    agg = agg_if_feasible(jt, "nvidia_gpu", "value", events, 1.0)
    if agg.empty:
      continue
    vals = frame.join_values(agg)
    if np.isnan(vals).any():
      keep_sparse = (
        name in sp._SUMMARY_ALLOW_PARTIAL_NULL and (~np.isnan(vals)).any()
      )
      if not keep_sparse:
        continue
    frame.set(name, vals)
    return


def _merge_cha_counter_arc_sum(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_cha_counter_arc_sum``: first CHA type in schema with known events.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_cha_counter_arc_sum(frame, jt)  # doctest: +SKIP
  """
  schema = jt.schema if isinstance(getattr(jt, "schema", None), dict) else {}
  cha_events = _sp()._CHA_ARC_EVENTS
  for cha_typ in CHA_TYPENAME_PRIORITY:
    if cha_typ not in schema:
      continue
    have = set(schema[cha_typ])
    evs = [e for e in cha_events if e in have]
    if not evs:
      continue
    agg = agg_if_feasible(jt, cha_typ, "arc", evs, 1.0)
    if agg.empty:
      continue
    frame.set("cha_counter_arc_sum", frame.join_values(agg))
    return


def _merge_opa_fabric_if_no_ib(frame: SeriesFrame, jt: Any) -> None:
  """
  ``_merge_opa_fabric_if_no_ib_ext``: Omni-Path bytes fill ``ibbw`` when
  InfiniBand bytes are absent.

  Args:
    frame (SeriesFrame): Target frame.
    jt (Any): ColumnarJobTable.

  Returns:
    None

  Examples:
    >>> _merge_opa_fabric_if_no_ib(frame, jt)  # doctest: +SKIP
  """
  if "ibbw" in frame.columns and frame.any_finite("ibbw"):
    return
  agg = agg_if_feasible(
    jt, "opa", "arc", ["PortXmitData", "PortRcvData"], 1.0 / 125000.0
  )
  if agg.empty:
    return
  frame.set("ibbw", frame.join_values(agg))


def add_node_power_est_column(frame: SeriesFrame) -> None:
  """
  ``_add_node_power_est_column``: module-only when ``nv_module_power_w`` >
  0, else CPU (DCGM, Intel RAPL, AMD RAPL first-present) + GPU power.

  Args:
    frame (SeriesFrame): Target frame (adds ``node_power_est_w``).

  Returns:
    None

  Examples:
    >>> import numpy as np
    >>> f = SeriesFrame.from_base(np.array([0, 0]), np.array([1, 2]), ["h"])
    >>> f.set("watts", np.array([100.0, np.nan]))
    >>> f.set("nv_power_w", np.array([50.0, 20.0]))
    >>> add_node_power_est_column(f)
    >>> f.columns["node_power_est_w"].tolist()
    [150.0, 20.0]
  """
  n = len(frame)
  out = np.full(n, np.nan, dtype=np.float64)
  use_mod = np.zeros(n, dtype=bool)
  mod = frame.columns.get("nv_module_power_w")
  if mod is not None:
    use_mod = (~np.isnan(mod)) & (mod > 0.0)
    out = np.where(use_mod, mod, out)
  cpu = np.full(n, np.nan, dtype=np.float64)
  filled = np.zeros(n, dtype=bool)
  for col_name in ("dcg_cpu_power_w", "watts", "amd_pkg_w"):
    col = frame.columns.get(col_name)
    if col is None:
      continue
    take = (~np.isnan(col)) & (~filled)
    cpu = np.where(take, col, cpu)
    filled |= take
  gpu = np.full(n, np.nan, dtype=np.float64)
  gv = frame.columns.get("nv_power_w")
  if gv is not None:
    gpu = np.where(~np.isnan(gv), gv, gpu)
  cpu_ok = np.isfinite(cpu)
  gpu_ok = np.isfinite(gpu)
  total = np.where(cpu_ok, cpu, 0.0) + np.where(gpu_ok, gpu, 0.0)
  use_sum = (~use_mod) & (cpu_ok | gpu_ok)
  frame.set("node_power_est_w", np.where(use_sum, total, out))


def _clamp_gpu_link_rates(frame: SeriesFrame) -> None:
  """
  ``_clamp_summary_gpu_link_rates``: NaN-out wrap poison on ``nv_gpu_link_gbs``.

  Args:
    frame (SeriesFrame): Target frame.

  Returns:
    None

  Examples:
    >>> import numpy as np
    >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
    >>> f.set("nv_gpu_link_gbs", np.array([1e9]))
    >>> _clamp_gpu_link_rates(f)
    >>> f.columns["nv_gpu_link_gbs"].tolist()
    [nan]
  """
  col = frame.columns.get("nv_gpu_link_gbs")
  if col is None:
    return
  limit = _sp()._MAX_SANE_GPU_LINK_GBPS
  poison = (~np.isnan(col)) & (np.abs(col) > limit)
  if poison.any():
    frame.set("nv_gpu_link_gbs", np.where(poison, np.nan, col))


def _nanmax(col: np.ndarray) -> float | None:
  """
  ``Series.max()`` → None when every value is NaN.

  Args:
    col (np.ndarray): Column.

  Returns:
    float | None: Maximum.

  Examples:
    >>> import numpy as np
    >>> _nanmax(np.array([np.nan, 2.0])), _nanmax(np.array([np.nan]))
    (2.0, None)
  """
  fin = col[~np.isnan(col)]
  return float(fin.max()) if fin.size else None


def _nv_mem_used_y_top(frame: SeriesFrame) -> float | None:
  """
  ``_summary_nv_mem_used_y_range_end``.

  Args:
    frame (SeriesFrame): Summary frame.

  Returns:
    float | None: ``1.1 * max(used, total)`` or None.

  Examples:
    >>> import numpy as np
    >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
    >>> _nv_mem_used_y_top(f) is None
    True
  """
  if (
    "nv_mem_used_mb" not in frame.columns
    or "nv_mem_total_mb" not in frame.columns
  ):
    return None
  cands = [
    m
    for m in (
      _nanmax(frame.columns["nv_mem_used_mb"]),
      _nanmax(frame.columns["nv_mem_total_mb"]),
    )
    if m is not None
  ]
  return 1.1 * max(cands) if cands else None


def _nv_gpu_util_y_top(
  frame: SeriesFrame, shared_gpu_count: int | None
) -> float | None:
  """
  ``_summary_nv_gpu_util_y_range_end``.

  Args:
    frame (SeriesFrame): Summary frame.
    shared_gpu_count (int | None): Affinity GPU count on shared jobs.

  Returns:
    float | None: Upper bound or None.

  Examples:
    >>> import numpy as np
    >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
    >>> _nv_gpu_util_y_top(f, 2)
    200.0
  """
  if shared_gpu_count is not None:
    return float(shared_gpu_count) * 100.0
  if "nv_gpu_util" not in frame.columns or "nv_gpu_count" not in frame.columns:
    return None
  mx = _nanmax(frame.columns["nv_gpu_count"])
  return None if mx is None else mx * 100.0


def build_summary_series(jt: Any) -> SummarySeries:
  """
  Assemble the Summary frame and render list for one job.

  Args:
    jt (Any): ColumnarJobTable (``host_list``, ``schema``, aggregates).

  Returns:
    SummarySeries: Frame, render specs and the metric spec list.

  Raises:
    ValueError: ``MSG_NO_METRIC_DATA`` when the job has no ``(host, time)``
      base rows or no hosts.
    KeyError: A derived-frequency cleanup names an absent column
      (same failure as the pandas ``del df[...]``).

  Examples:
    >>> build_summary_series(jt)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA

  sp = _sp()
  ht = jt.get_host_time()
  host_list = list(getattr(jt, "host_list", []) or [])
  if ht.empty or not host_list:
    raise ValueError(MSG_NO_METRIC_DATA)
  frame = SeriesFrame.from_base(ht.host_id, ht.time_us, ht.hosts)
  schema = jt.schema if isinstance(getattr(jt, "schema", None), dict) else {}

  for typ, val, events, name, conv, _label in sp._SUMMARY_SINGLE_SPECS:
    if name == "nv_gpu_util":
      continue
    if not feasible(schema, typ, events):
      agg = AggTable.zero()
    elif name == "dcg_cpu_power_w":
      agg = dcgm_cpu_power_util_agg(jt, conv)
    else:
      agg = jt.get_aggregate(typ, val, list(events), conv)
    vals = (
      frame.join_values(agg) if not agg.empty else np.full(len(frame), np.nan)
    )
    if np.isnan(vals).any():
      keep_sparse = (
        name in sp._SUMMARY_ALLOW_PARTIAL_NULL and (~np.isnan(vals)).any()
      )
      if not keep_sparse:
        continue
    frame.set(name, vals)

  _merge_nvidia_gpu_util(frame, jt)
  _merge_mem_prefer_job(frame, jt)
  for fw in sp._SUMMARY_FIRST_WIN_SPECS:
    _merge_first_full_coverage(
      frame, jt, fw["name"], fw["val_col"], fw["tries"]
    )
  _merge_intel_imc_cas_mbw(frame, jt)
  _merge_amd_df_mbw(frame, jt)
  _merge_cha_counter_arc_sum(frame, jt)
  _merge_opa_fabric_if_no_ib(frame, jt)
  add_node_power_est_column(frame)
  _clamp_gpu_link_rates(frame)

  metrics = list(sp._summary_metric_specs())
  if "acycles" in frame.columns and "mcycles" in frame.columns:
    with np.errstate(divide="ignore", invalid="ignore"):
      frame.set(
        "freq",
        FREQ_GHZ_SCALE * frame.columns["acycles"] / frame.columns["mcycles"],
      )
    metrics.append(("freq", "arc", [], "freq", 1, "CPU freq [GHz]"))
    frame.drop("mcycles", "acycles", "instr")
  if "amd_acycles" in frame.columns and "amd_mcycles" in frame.columns:
    frame.drop("amd_mcycles", "amd_acycles", "amd_instr")

  render: list[tuple[str, str, float | None]] = []
  skip_lumped = any(
    n in frame.columns and frame.any_finite(n)
    for n in sp._SUMMARY_TENSOR_SPLIT_METRICS
  )
  shared_gpu_count = None
  if getattr(jt, "_shared_node", False):
    shared_gpu_count = len(getattr(jt, "gpu_affinity_devs", None) or [])
  for _typ, _val, _events, name, _conv, label in metrics:
    if name not in frame.columns:
      continue
    if name == "node_power_est_w" and not frame.any_finite(name):
      continue
    if name in sp._SUMMARY_SKIP_PLOT_METRICS:
      continue
    if name == "nv_tensor_active" and skip_lumped:
      continue
    if name == "freq":
      fmax = _nanmax(frame.columns[name])
      if fmax is None or fmax <= FREQ_PLOT_MIN_MAX:
        continue
    y_top = None
    if name == "nv_mem_used_mb":
      y_top = _nv_mem_used_y_top(frame)
    elif name == "nv_gpu_util":
      y_top = _nv_gpu_util_y_top(frame, shared_gpu_count)
    render.append((name, label, y_top))
  render.sort(key=lambda item: sp._summary_plot_order_key(item[0]))
  return SummarySeries(frame, render, metrics)


def hardware_error_series(
  jt: Any,
) -> list[tuple[str, str, np.ndarray, np.ndarray, np.ndarray]]:
  """
  ``_one_error_host_series`` for every Summary hardware-error counter.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    list[tuple[str, str, np.ndarray, np.ndarray, np.ndarray]]: ``(event,
    label, host_id, time_us, rates)`` per counter with a non-zero rate.

  Examples:
    >>> hardware_error_series(jt)  # doctest: +SKIP
  """
  out = []
  for typ, event, label in _sp()._iter_hardware_error_event_specs():
    agg = agg_if_feasible(jt, typ, "arc", [event], 1.0)
    if agg.empty or not (~np.isnan(agg.sum_val)).any():
      continue
    if not np.nan_to_num(agg.sum_val, nan=0.0).any():
      continue
    out.append((event, label, agg.host_id, agg.time_us, agg.sum_val))
  return out
