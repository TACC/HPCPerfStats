"""
K2 Job Detail aggregates from a ColumnarJobTable: FSIO totals and peaks,
GPU utilisation summary / inventory, and the node power estimate.

Ports ``job_detail_fsio``, ``gpu_job_detail_summary`` and
``gen.node_power_est`` reads; the reduction rules (count > 2 devices,
DCGM blank rejection, module-only power) are unchanged.

Attributes:
  GPU_TYPE_PRECEDENCE: Vendor order for GPU summary fields.
  _GPU_UTIL_EVENTS: GPU utilisation event names (new then legacy).
  _NFS_READ_EVENTS: NFS client read byte counters.
  _NFS_WRITE_EVENTS: NFS client write byte counters.
  _NFS_IOPS_EVENTS: NFS op counters for peak IOPS.
  _BYTES_TO_MB: Bytes to MiB.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.beegfs_metadata_iops_events import (
  BEEGFS_METADATA_IOPS_EVENTS,
  BEEGFS_READ_BYTES_EVENTS,
  BEEGFS_WRITE_BYTES_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.columnar import segments as sg
from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable
from hpcperfstats.analysis.metrics.lib.columnar.series_frame import SeriesFrame
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  add_node_power_est_column,
  agg_if_feasible,
  dcgm_cpu_power_util_agg,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
  LLITE_METADATA_IOPS_EVENTS,
  LLITE_READ_BYTES_EVENTS,
  LLITE_WRITE_BYTES_EVENTS,
)
from hpcperfstats.lib.dcgm_blank import is_dcgm_numeric_blank

GPU_TYPE_PRECEDENCE: tuple[str, ...] = ("nvidia_gpu", "amd_gpu", "intel_gpu")
_GPU_UTIL_EVENTS = ("gpu_util", "utilization")
_NFS_READ_EVENTS = ("normal_read", "direct_read", "server_read")
_NFS_WRITE_EVENTS = ("normal_write", "direct_write", "server_write")
_NFS_IOPS_EVENTS = ("READ_ops", "WRITE_ops")
_BYTES_TO_MB = 1 / (1024 * 1024)


# -- FSIO --------------------------------------------------------------------


def _sum_by_time(agg: AggTable) -> tuple[np.ndarray, np.ndarray]:
  """
  ``df.groupby("time")["sum_val"].sum()``.

  Args:
    agg (AggTable): Aggregate rows.

  Returns:
    tuple[np.ndarray, np.ndarray]: Sorted times and totals.

  Examples:
    >>> import numpy as np
    >>> t, s = _sum_by_time(
    ...   AggTable(np.array([0, 1]), np.array([5, 5]), np.array([1.0, 2.0]))
    ... )
    >>> t.tolist(), s.tolist()
    ([5], [3.0])
  """
  times, sums, _c = sg.group_sum(
    agg.time_us.astype(np.int64), np.nan_to_num(agg.sum_val, nan=0.0)
  )
  return times, sums


def peak_combined_read_write_mb_s(
  jt: Any, typ: str, read_events: Sequence[str], write_events: Sequence[str]
) -> float | None:
  """
  ``_max_job_wide_combined_read_write_mb_s``: max over time of job-wide
  read + write MB/s.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    read_events (Sequence[str]): Read byte events.
    write_events (Sequence[str]): Write byte events.

  Returns:
    float | None: Peak or None.

  Examples:
    >>> peak_combined_read_write_mb_s(
    ...   jt, "lustre_llite", ("vfs_read_bytes",), ("vfs_write_bytes",)
    ... )  # doctest: +SKIP
  """
  rdf = jt.get_aggregate(typ, "arc", list(read_events), _BYTES_TO_MB)
  wdf = jt.get_aggregate(typ, "arc", list(write_events), _BYTES_TO_MB)
  if rdf.empty and wdf.empty:
    return None
  rt, rs = (
    _sum_by_time(rdf)
    if not rdf.empty
    else (np.empty(0, dtype=np.int64), np.empty(0))
  )
  wt, ws = (
    _sum_by_time(wdf)
    if not wdf.empty
    else (np.empty(0, dtype=np.int64), np.empty(0))
  )
  times = np.union1d(rt, wt)
  if times.size == 0:
    return None
  total = np.zeros(times.shape[0], dtype=np.float64)
  if rt.size:
    total[np.searchsorted(times, rt)] += rs
  if wt.size:
    total[np.searchsorted(times, wt)] += ws
  peak = float(total.max())
  return None if np.isnan(peak) else peak


def peak_arc_sum(
  jt: Any, typ: str, events: Sequence[str], conv: float = 1.0
) -> float | None:
  """
  ``_max_job_wide_arc_sum``: max over time of the job-wide arc sum.

  Args:
    jt (Any): ColumnarJobTable.
    typ (str): Type.
    events (Sequence[str]): Events.
    conv (float): Multiplier.

  Returns:
    float | None: Peak or None.

  Examples:
    >>> peak_arc_sum(jt, "lustre_llite", ("vfs_open_ops",))  # doctest: +SKIP
  """
  df = jt.get_aggregate(typ, "arc", list(events), conv)
  if df.empty:
    return None
  _t, sums = _sum_by_time(df)
  if sums.size == 0:
    return None
  peak = float(sums.max())
  return None if np.isnan(peak) else peak


def _read_write_mb(
  delta_by_event: dict[str, float],
  read_names: Sequence[str],
  write_names: Sequence[str],
) -> tuple[float, float] | None:
  """
  ``[read_mb, write_mb]`` from canonical delta sums (first present name).

  Args:
    delta_by_event (dict[str, float]): Event to byte delta.
    read_names (Sequence[str]): Read event names in preference order.
    write_names (Sequence[str]): Write event names in preference order.

  Returns:
    tuple[float, float] | None: Totals, or None when the map is empty.

  Examples:
    >>> _read_write_mb(
    ...   {"vfs_read_bytes": 1048576.0},
    ...   ("vfs_read_bytes",),
    ...   ("vfs_write_bytes",),
    ... )
    (1.0, 0.0)
  """
  if not delta_by_event:
    return None

  def _first(names: Sequence[str]) -> float:
    """
    First present event's delta (bytes) or 0.

    Args:
      names (Sequence[str]): Candidates.

    Returns:
      float: Bytes.

    Examples:
      >>> True
      True
    """
    for n in names:
      if n in delta_by_event:
        return float(delta_by_event[n] or 0.0)
    return 0.0

  return _first(read_names) / (1024 * 1024), _first(write_names) / (1024 * 1024)


def fsio_totals(jt: Any) -> dict[str, tuple[float, float]]:
  """
  Lustre / NFS / BeeGFS read and write MB totals that have byte deltas.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    dict[str, tuple[float, float]]: ``llite`` / ``nfs`` / ``beegfs`` to
    ``(read_mb, write_mb)``.

  Examples:
    >>> fsio_totals(jt)  # doctest: +SKIP
  """
  out: dict[str, tuple[float, float]] = {}
  llite = _read_write_mb(
    jt.get_llite_delta_by_event(),
    ("vfs_read_bytes", "read_bytes"),
    ("vfs_write_bytes", "write_bytes"),
  )
  if llite is not None:
    out["llite"] = llite
  nfs = jt.get_nfs_delta_totals_mb()
  if nfs is not None:
    out["nfs"] = (float(nfs[0]), float(nfs[1]))
  beegfs = _read_write_mb(
    jt.get_beegfs_delta_by_event(), ("vfs_read_bytes",), ("vfs_write_bytes",)
  )
  if beegfs is not None:
    out["beegfs"] = beegfs
  return out


def fsio_peaks(jt: Any, key: str) -> tuple[float | None, float | None]:
  """
  ``(peak_mb_s, peak_iops)`` for one FSIO family.

  Args:
    jt (Any): ColumnarJobTable.
    key (str): ``llite``, ``nfs`` or ``beegfs``.

  Returns:
    tuple[float | None, float | None]: Peaks.

  Examples:
    >>> fsio_peaks(jt, "llite")  # doctest: +SKIP
  """
  specs = {
    "llite": (
      "lustre_llite",
      LLITE_READ_BYTES_EVENTS,
      LLITE_WRITE_BYTES_EVENTS,
      LLITE_METADATA_IOPS_EVENTS,
    ),
    "nfs": ("nfs", _NFS_READ_EVENTS, _NFS_WRITE_EVENTS, _NFS_IOPS_EVENTS),
    "beegfs": (
      "beegfs_client",
      BEEGFS_READ_BYTES_EVENTS,
      BEEGFS_WRITE_BYTES_EVENTS,
      BEEGFS_METADATA_IOPS_EVENTS,
    ),
  }
  typ, read_ev, write_ev, iops_ev = specs[key]
  return (
    peak_combined_read_write_mb_s(jt, typ, read_ev, write_ev),
    peak_arc_sum(jt, typ, iops_ev, 1.0),
  )


# -- GPU ---------------------------------------------------------------------


def gpu_agg_rows(jt: Any) -> list[dict[str, Any]]:
  """
  ``gpu_agg_rows_for_job_window``: util Count/Max/Avg rows of the first
  vendor with rows (empty on shared jobs without GPU affinity).

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    list[dict[str, Any]]: Rows ``host, dev, event, cnt, vmax, vmean``.

  Examples:
    >>> gpu_agg_rows(jt)  # doctest: +SKIP
  """
  if getattr(jt, "_shared_node", False) and not getattr(
    jt, "gpu_affinity_devs", None
  ):
    return []
  if not getattr(jt, "acct_host_list", None):
    return []
  for gpu_typ in GPU_TYPE_PRECEDENCE:
    rows = jt.gpu_util_rows(gpu_typ, list(_GPU_UTIL_EVENTS))
    if rows:
      return rows
  return []


def gpu_count_total(jt: Any) -> int | None:
  """
  ``gpu_count_total_for_job_window``.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    int | None: GPU count.

  Examples:
    >>> gpu_count_total(jt)  # doctest: +SKIP
  """
  return jt.gpu_count_total(GPU_TYPE_PRECEDENCE)


def gpu_inventory(jt: Any) -> list[dict[str, Any]]:
  """
  ``gpu_inventory_for_job_window``: per-device util max/mean, sample count
  and optional power peak, first vendor with util rows.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    list[dict[str, Any]]: Inventory rows sorted by host then dev.

  Examples:
    >>> gpu_inventory(jt)  # doctest: +SKIP
  """
  if getattr(jt, "_shared_node", False) and not getattr(
    jt, "gpu_affinity_devs", None
  ):
    return []
  if not getattr(jt, "acct_host_list", None):
    return []
  for gpu_typ in GPU_TYPE_PRECEDENCE:
    util_rows = jt.gpu_util_rows(gpu_typ, list(_GPU_UTIL_EVENTS))
    if not util_rows:
      continue
    power_rows = jt.gpu_max_per_host_dev(gpu_typ, "power_usage")
    per_device: dict[tuple[str, str], dict[str, Any]] = {}
    for r in util_rows:
      key = (str(r.get("host") or ""), str(r.get("dev") or ""))
      event = str(r.get("event") or "")
      slot = per_device.setdefault(
        key, {"host": key[0], "dev": key[1], "type": gpu_typ}
      )
      if event == "gpu_util" or (
        event == "utilization" and "util_max" not in slot
      ):
        vmax = r.get("vmax")
        vmean = r.get("vmean")
        if vmax is None or is_dcgm_numeric_blank(vmax):
          continue
        slot["util_max"] = float(vmax)
        slot["util_mean"] = (
          None
          if vmean is None or is_dcgm_numeric_blank(vmean)
          else float(vmean)
        )
        slot["sample_count"] = int(r.get("cnt") or 0)
    for r in power_rows:
      key = (str(r.get("host") or ""), str(r.get("dev") or ""))
      if key not in per_device:
        continue
      pmax = r.get("pmax")
      if pmax is None or is_dcgm_numeric_blank(pmax):
        continue
      per_device[key]["power_max_w"] = float(pmax)
    out = [row for row in per_device.values() if "util_max" in row]
    out.sort(key=lambda r: (r.get("host") or "", r.get("dev") or ""))
    if out:
      return out
  return []


def gpu_summary_tuple(
  jt: Any,
) -> tuple[int | None, float | None, float | None, int | None]:
  """
  ``compute_job_gpu_summary_tuple``: ``(gpu_active, util_max, util_mean,
  gpu_count)``.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    tuple[int | None, float | None, float | None, int | None]: Summary.

  Examples:
    >>> gpu_summary_tuple(jt)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
    reduce_gpu_agg_to_util_stats,
  )

  try:
    active, vmax, vmean = reduce_gpu_agg_to_util_stats(gpu_agg_rows(jt))
    return active, vmax, vmean, gpu_count_total(jt)
  except Exception:
    return None, None, None, None


# -- node power estimate -------------------------------------------------------


def node_power_est_frame(jt: Any) -> SeriesFrame:
  """
  ``build_node_power_est_dataframe``: power components on the ``(host,
  time)`` base plus ``node_power_est_w``.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    SeriesFrame: Frame (empty when the job has no base rows).

  Examples:
    >>> node_power_est_frame(jt)  # doctest: +SKIP
  """
  ht = jt.get_host_time()
  frame = SeriesFrame.from_base(ht.host_id, ht.time_us, ht.hosts)
  if frame.empty:
    return frame
  rows = (
    ("intel_x86_rapl", "arc", ["pkg_energy"], "watts", 0.00001526),
    ("amd_x86_rapl", "arc", ["pkg_energy"], "amd_pkg_w", 0.00001526),
    ("nvidia_gpu", "value", ["power_usage"], "nv_power_w", 1.0),
    ("nvidia_gpu", "value", ["module_power_usage"], "nv_module_power_w", 1.0),
    ("host_cpu_hw", "value", ["dcgm_cpu_power_util_w"], "dcg_cpu_power_w", 1.0),
  )
  for typ, val, events, name, conv in rows:
    if name == "dcg_cpu_power_w":
      agg = dcgm_cpu_power_util_agg(jt, conv)
    else:
      agg = agg_if_feasible(jt, typ, val, list(events), conv)
    frame.set(
      name,
      frame.join_values(agg) if not agg.empty else np.full(len(frame), np.nan),
    )
  add_node_power_est_column(frame)
  return frame


def max_node_power_est_w(jt: Any) -> float | None:
  """
  ``node_power_est.max_node_power_est_w``.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    float | None: Peak estimate.

  Examples:
    >>> max_node_power_est_w(jt)  # doctest: +SKIP
  """
  frame = node_power_est_frame(jt)
  col = frame.columns.get("node_power_est_w")
  if col is None or not (~np.isnan(col)).any():
    return None
  return float(np.nanmax(col))


def mean_node_power_est_w(jt: Any) -> float | None:
  """
  ``node_power_est.mean_node_power_est_w``.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    float | None: Mean estimate over finite samples.

  Examples:
    >>> mean_node_power_est_w(jt)  # doctest: +SKIP
  """
  frame = node_power_est_frame(jt)
  col = frame.columns.get("node_power_est_w")
  if col is None:
    return None
  fin = col[~np.isnan(col)]
  return float(fin.mean()) if fin.size else None


def job_cpu_gpu_watt_hours(jt: Any) -> float | None:
  """
  ``node_power_est.job_cpu_gpu_watt_hours``: trapezoid integral per host of
  ``node_power_est_w`` (Wh), summed; None without finite CPU power.

  Args:
    jt (Any): ColumnarJobTable.

  Returns:
    float | None: Watt-hours.

  Examples:
    >>> job_cpu_gpu_watt_hours(jt)  # doctest: +SKIP
  """
  frame = node_power_est_frame(jt)
  col = frame.columns.get("node_power_est_w")
  if col is None:
    return None
  cpu_ok = any(
    name in frame.columns and np.isfinite(frame.columns[name]).any()
    for name in ("dcg_cpu_power_w", "watts", "amd_pkg_w")
  )
  if not cpu_ok:
    return None
  total_wh = 0.0
  any_host = False
  trapz = getattr(np, "trapezoid", None) or np.trapz
  for hid in np.unique(frame.host_id):
    sel = (frame.host_id == hid) & ~np.isnan(col)
    if int(sel.sum()) < 2:
      continue
    order = np.argsort(frame.time_us[sel], kind="stable")
    t = frame.time_us[sel][order].astype(np.float64) / 1_000_000.0
    t_s = t - t[0]
    p = col[sel][order]
    if not np.isfinite(p).any():
      continue
    joules = float(trapz(p, t_s))
    if not np.isfinite(joules) or joules < 0:
      continue
    total_wh += joules / 3600.0
    any_host = True
  return float(total_wh) if any_host else None
