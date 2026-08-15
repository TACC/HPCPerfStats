"""
Job-detail FSIO totals (Lustre llite, NFS, BeeGFS) for metrics_data; mirrors.

job_detail artifact ``fsio``.

Attributes:
  NO_FSIO_BEEGFS_DATA: ``NO_FSIO_BEEGFS_DATA``.
  NO_FSIO_BEEGFS_PEAK_IOPS: ``NO_FSIO_BEEGFS_PEAK_IOPS``.
  NO_FSIO_BEEGFS_PEAK_MB_S: ``NO_FSIO_BEEGFS_PEAK_MB_S``.
  NO_FSIO_LLITE_DATA: ``NO_FSIO_LLITE_DATA``.
  NO_FSIO_LLITE_PEAK_IOPS: ``NO_FSIO_LLITE_PEAK_IOPS``.
  NO_FSIO_LLITE_PEAK_MB_S: ``NO_FSIO_LLITE_PEAK_MB_S``.
  NO_FSIO_NFS_DATA: ``NO_FSIO_NFS_DATA``.
  NO_FSIO_NFS_PEAK_IOPS: ``NO_FSIO_NFS_PEAK_IOPS``.
  NO_FSIO_NFS_PEAK_MB_S: ``NO_FSIO_NFS_PEAK_MB_S``.
  NO_FSIO_NFS_WHEN_LLITE: ``NO_FSIO_NFS_WHEN_LLITE``.
  _BYTES_TO_MB: ``_BYTES_TO_MB``.
  _FSIO_METRICS: ``_FSIO_METRICS``.
  _NFS_IOPS_EVENTS: ``_NFS_IOPS_EVENTS``.
  _NFS_READ_EVENTS: ``_NFS_READ_EVENTS``.
  _NFS_WRITE_EVENTS: ``_NFS_WRITE_EVENTS``.
  logger: ``logger``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from hpcperfstats.analysis.metrics.lib.beegfs_metadata_iops_events import (
    BEEGFS_METADATA_IOPS_EVENTS,
    BEEGFS_READ_BYTES_EVENTS,
    BEEGFS_WRITE_BYTES_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
    LLITE_METADATA_IOPS_EVENTS,
    LLITE_READ_BYTES_EVENTS,
    LLITE_WRITE_BYTES_EVENTS,
)

logger = logging.getLogger(__name__)

_BYTES_TO_MB = 1 / (1024 * 1024)

# Messages align with persisted ``no_data_reason`` for detail_fsio_* rows.
NO_FSIO_LLITE_DATA = "No Lustre llite read/write byte deltas for this job"
NO_FSIO_NFS_DATA = "No NFS client byte deltas for this job"
NO_FSIO_BEEGFS_DATA = "No BeeGFS client read/write byte deltas for this job"
# Retained for older persisted rows / tests that may still reference the string.
NO_FSIO_NFS_WHEN_LLITE = "NFS totals omitted when Lustre llite data is used for job detail FSIO"
NO_FSIO_LLITE_PEAK_MB_S = "No Lustre llite byte counter time series for peak MB/s"
NO_FSIO_LLITE_PEAK_IOPS = "No Lustre llite metadata operation time series for peak IOPS"
NO_FSIO_NFS_PEAK_MB_S = "No NFS client byte counter time series for peak MB/s"
NO_FSIO_NFS_PEAK_IOPS = "No NFS READ/WRITE op time series for peak IOPS"
NO_FSIO_BEEGFS_PEAK_MB_S = "No BeeGFS client byte counter time series for peak MB/s"
NO_FSIO_BEEGFS_PEAK_IOPS = "No BeeGFS client metadata operation time series for peak IOPS"

_NFS_READ_EVENTS = ("normal_read", "direct_read", "server_read")
_NFS_WRITE_EVENTS = ("normal_write", "direct_write", "server_write")
_NFS_IOPS_EVENTS = ("READ_ops", "WRITE_ops")

_FSIO_METRICS: Tuple[Tuple[str, str, str], ...] = (
    ("detail_fsio_llite_read_mb", "llite", "MB"),
    ("detail_fsio_llite_write_mb", "llite", "MB"),
    ("detail_fsio_llite_peak_mb_s", "llite", "MB/s"),
    ("detail_fsio_llite_peak_iops", "llite", "#/s"),
    ("detail_fsio_nfs_read_mb", "nfs", "MB"),
    ("detail_fsio_nfs_write_mb", "nfs", "MB"),
    ("detail_fsio_nfs_peak_mb_s", "nfs", "MB/s"),
    ("detail_fsio_nfs_peak_iops", "nfs", "#/s"),
    ("detail_fsio_beegfs_read_mb", "beegfs", "MB"),
    ("detail_fsio_beegfs_write_mb", "beegfs", "MB"),
    ("detail_fsio_beegfs_peak_mb_s", "beegfs", "MB/s"),
    ("detail_fsio_beegfs_peak_iops", "beegfs", "#/s"),
)


def fsio_job_detail_catalog() -> Tuple[Tuple[str, str, str], ...]:
  """
  (metric, type, units) for catalog and compute_metrics.

  Returns:
    Tuple[Tuple[str, str, str], ...]: Ordered FSIO catalog entries (12 metrics).

  Examples:
    >>> len(fsio_job_detail_catalog())
    12
  """
  return _FSIO_METRICS


def _max_job_wide_combined_read_write_mb_s(
  jt: Any,
  typ: str,
  read_events: Tuple[str, ...],
  write_events: Tuple[str, ...],
) -> Optional[float]:
  """
  Peak aggregate client MB/s: max over time of (read_mb/s + write_mb/s), summed
  across hosts.

  Args:
    jt (Any): Job table helper with ``get_aggregate_df``.
    typ (str): Monitor type name passed to ``get_aggregate_df``.
    read_events (Tuple[str, ...]): Read-byte event names.
    write_events (Tuple[str, ...]): Write-byte event names.

  Returns:
    Optional[float]: Peak combined MB/s, or None when unavailable.

  Examples:
    >>> _max_job_wide_combined_read_write_mb_s(None, "x", (), ())
  """
  try:
    rdf = jt.get_aggregate_df(typ, "arc", list(read_events), conv=_BYTES_TO_MB)
    wdf = jt.get_aggregate_df(typ, "arc", list(write_events), conv=_BYTES_TO_MB)
  except Exception:
    logger.debug("FSIO peak MB/s aggregate failed typ=%s", typ, exc_info=True)
    return None
  r_empty = rdf is None or rdf.empty or "time" not in getattr(rdf, "columns", [])
  w_empty = wdf is None or wdf.empty or "time" not in getattr(wdf, "columns", [])
  if r_empty and w_empty:
    return None
  r_t = (
      rdf.groupby("time", as_index=False)["sum_val"].sum().rename(columns={"sum_val": "r"})
      if not r_empty
      else pd.DataFrame(columns=["time", "r"])
  )
  w_t = (
      wdf.groupby("time", as_index=False)["sum_val"].sum().rename(columns={"sum_val": "w"})
      if not w_empty
      else pd.DataFrame(columns=["time", "w"])
  )
  if r_t.empty and w_t.empty:
    return None
  merged = pd.merge(r_t, w_t, on="time", how="outer").fillna(0.0)
  if merged.empty:
    return None
  merged["total"] = merged["r"] + merged["w"]
  peak = merged["total"].max()
  if peak is None or bool(pd.isna(peak)):
    return None
  return float(peak)


def _max_job_wide_arc_sum(
  jt: Any,
  typ: str,
  events: Tuple[str, ...],
  conv: float = 1.0,
) -> Optional[float]:
  """
  Peak aggregate IOPS-style rate: max over time of sum(arc) across hosts for
  ``events``.

  Args:
    jt (Any): Job table helper with ``get_aggregate_df``.
    typ (str): Monitor type name passed to ``get_aggregate_df``.
    events (Tuple[str, ...]): Event names to sum.
    conv (float): Multiplier applied inside ``get_aggregate_df``.

  Returns:
    Optional[float]: Peak rate, or None when unavailable.

  Examples:
    >>> _max_job_wide_arc_sum(None, "x", (), 0)  # doctest: +SKIP
  """
  try:
    df = jt.get_aggregate_df(typ, "arc", list(events), conv=conv)
  except Exception:
    logger.debug("FSIO peak IOPS aggregate failed typ=%s", typ, exc_info=True)
    return None
  if df is None or df.empty or "time" not in df.columns:
    return None
  by_t = df.groupby("time", as_index=False)["sum_val"].sum()
  if by_t.empty:
    return None
  peak = by_t["sum_val"].max()
  if peak is None or bool(pd.isna(peak)):
    return None
  return float(peak)


def compute_job_detail_fsio_metric_rows(jt: Any) -> List[Dict[str, Any]]:
  """
  Build metrics_data-shaped dicts from ``jid_table`` (independent Lustre, NFS,
  and BeeGFS families when each has byte deltas).

  Args:
    jt (Any): Job table helper exposing FSIO delta and aggregate helpers.

  Returns:
    List[Dict[str, Any]]: One row per ``_FSIO_METRICS`` catalog entry.

  Examples:
    >>> compute_job_detail_fsio_metric_rows(None)  # doctest: +SKIP
  """
  llite_read: Optional[float] = None
  llite_write: Optional[float] = None
  try:
    llite_df = jt.get_llite_delta_by_event()
    if not llite_df.empty and "delta_sum" in llite_df.columns:
      llite_df = llite_df.copy()
      llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
      read_row = llite_df[llite_df["event"] == "vfs_read_bytes"]
      write_row = llite_df[llite_df["event"] == "vfs_write_bytes"]
      if read_row.empty:
        read_row = llite_df[llite_df["event"] == "read_bytes"]
      if write_row.empty:
        write_row = llite_df[llite_df["event"] == "write_bytes"]
      read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
      write_val = float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
      llite_read, llite_write = read_val, write_val
  except Exception:
    pass

  nfs_read: Optional[float] = None
  nfs_write: Optional[float] = None
  try:
    nfs_totals = jt.get_nfs_delta_totals_mb()
    if nfs_totals is not None:
      nfs_read, nfs_write = float(nfs_totals[0]), float(nfs_totals[1])
  except Exception:
    pass

  beegfs_read: Optional[float] = None
  beegfs_write: Optional[float] = None
  try:
    beegfs_df = jt.get_beegfs_delta_by_event()
    if not beegfs_df.empty and "delta_sum" in beegfs_df.columns:
      beegfs_df = beegfs_df.copy()
      beegfs_df["delta_mb"] = beegfs_df["delta_sum"].fillna(0) / (1024 * 1024)
      read_row = beegfs_df[beegfs_df["event"] == "vfs_read_bytes"]
      write_row = beegfs_df[beegfs_df["event"] == "vfs_write_bytes"]
      read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
      write_val = float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
      beegfs_read, beegfs_write = read_val, write_val
  except Exception:
    pass

  rows: List[Dict[str, Any]] = []
  llite_ok = llite_read is not None and llite_write is not None
  nfs_ok = nfs_read is not None and nfs_write is not None
  beegfs_ok = beegfs_read is not None and beegfs_write is not None

  llite_peak_mb: Optional[float] = None
  llite_peak_iops: Optional[float] = None
  if llite_ok:
    llite_peak_mb = _max_job_wide_combined_read_write_mb_s(
        jt, "lustre_llite", LLITE_READ_BYTES_EVENTS, LLITE_WRITE_BYTES_EVENTS)
    llite_peak_iops = _max_job_wide_arc_sum(jt, "lustre_llite", LLITE_METADATA_IOPS_EVENTS, 1.0)

  nfs_peak_mb: Optional[float] = None
  nfs_peak_iops: Optional[float] = None
  if nfs_ok:
    nfs_peak_mb = _max_job_wide_combined_read_write_mb_s(
        jt, "nfs", _NFS_READ_EVENTS, _NFS_WRITE_EVENTS)
    nfs_peak_iops = _max_job_wide_arc_sum(jt, "nfs", _NFS_IOPS_EVENTS, 1.0)

  beegfs_peak_mb: Optional[float] = None
  beegfs_peak_iops: Optional[float] = None
  if beegfs_ok:
    beegfs_peak_mb = _max_job_wide_combined_read_write_mb_s(
        jt, "beegfs_client", BEEGFS_READ_BYTES_EVENTS, BEEGFS_WRITE_BYTES_EVENTS)
    beegfs_peak_iops = _max_job_wide_arc_sum(
        jt, "beegfs_client", BEEGFS_METADATA_IOPS_EVENTS, 1.0)

  for metric_name, row_type, units in _FSIO_METRICS:
    if metric_name.startswith("detail_fsio_llite_"):
      if not llite_ok:
        rows.append(_row(metric_name, row_type, units, None, NO_FSIO_LLITE_DATA))
        continue
      if metric_name == "detail_fsio_llite_read_mb":
        rows.append(_row(metric_name, row_type, units, float(llite_read), None))
      elif metric_name == "detail_fsio_llite_write_mb":
        rows.append(_row(metric_name, row_type, units, float(llite_write), None))
      elif metric_name == "detail_fsio_llite_peak_mb_s":
        if llite_peak_mb is not None:
          rows.append(_row(metric_name, row_type, units, llite_peak_mb, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_LLITE_PEAK_MB_S))
      else:
        if llite_peak_iops is not None:
          rows.append(_row(metric_name, row_type, units, llite_peak_iops, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_LLITE_PEAK_IOPS))
    elif metric_name.startswith("detail_fsio_nfs_"):
      if not nfs_ok:
        rows.append(_row(metric_name, row_type, units, None, NO_FSIO_NFS_DATA))
      elif metric_name == "detail_fsio_nfs_read_mb":
        rows.append(_row(metric_name, row_type, units, float(nfs_read), None))
      elif metric_name == "detail_fsio_nfs_write_mb":
        rows.append(_row(metric_name, row_type, units, float(nfs_write), None))
      elif metric_name == "detail_fsio_nfs_peak_mb_s":
        if nfs_peak_mb is not None:
          rows.append(_row(metric_name, row_type, units, nfs_peak_mb, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_NFS_PEAK_MB_S))
      else:
        if nfs_peak_iops is not None:
          rows.append(_row(metric_name, row_type, units, nfs_peak_iops, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_NFS_PEAK_IOPS))
    else:
      if not beegfs_ok:
        rows.append(_row(metric_name, row_type, units, None, NO_FSIO_BEEGFS_DATA))
      elif metric_name == "detail_fsio_beegfs_read_mb":
        rows.append(_row(metric_name, row_type, units, float(beegfs_read), None))
      elif metric_name == "detail_fsio_beegfs_write_mb":
        rows.append(_row(metric_name, row_type, units, float(beegfs_write), None))
      elif metric_name == "detail_fsio_beegfs_peak_mb_s":
        if beegfs_peak_mb is not None:
          rows.append(_row(metric_name, row_type, units, beegfs_peak_mb, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_BEEGFS_PEAK_MB_S))
      else:
        if beegfs_peak_iops is not None:
          rows.append(_row(metric_name, row_type, units, beegfs_peak_iops, None))
        else:
          rows.append(_row(metric_name, row_type, units, None, NO_FSIO_BEEGFS_PEAK_IOPS))

  return rows


def _row(
  metric: str,
  row_type: str,
  units: str,
  val: Optional[float],
  reason: Optional[str],
) -> Any:
  """
  Build one metrics_data-shaped FSIO row dict.

  Args:
    metric (str): Metric name (``detail_fsio_*``).
    row_type (str): Family type label (``llite`` / ``nfs`` / ``beegfs``).
    units (str): Unit string for the catalog.
    val (Optional[float]): Numeric value, or None when unavailable.
    reason (Optional[str]): ``no_data_reason`` when ``val`` is None.

  Returns:
    Any: Dict with type/metric/units/value/no_data_reason.

  Examples:
    >>> _row("detail_fsio_beegfs_read_mb", "beegfs", "MB", 1.0, None)["value"]
    1.0
  """
  return {
      "type": row_type,
      "metric": metric,
      "units": units,
      "value": val,
      "no_data_reason": reason,
  }


def extend_fsio_payload_lists_with_peaks(fsio: Dict[str, Any], jt: Any) -> None:
  """
  Mutate ``fsio`` ``llite`` / ``nfs`` / ``beegfs`` lists from legacy length-2 to
  ``[r,w,peak_mb_s,peak_iops]``.

  Args:
    fsio (Dict[str, Any]): Mutable job-detail ``fsio`` payload.
    jt (Any): Job table helper used for peak aggregates.

  Returns:
    None

  Examples:
    >>> extend_fsio_payload_lists_with_peaks({}, None)  # doctest: +SKIP
  """
  specs: Tuple[
      Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...
  ] = (
      (
          "llite",
          "lustre_llite",
          LLITE_READ_BYTES_EVENTS,
          LLITE_WRITE_BYTES_EVENTS,
          LLITE_METADATA_IOPS_EVENTS,
      ),
      ("nfs", "nfs", _NFS_READ_EVENTS, _NFS_WRITE_EVENTS, _NFS_IOPS_EVENTS),
      (
          "beegfs",
          "beegfs_client",
          BEEGFS_READ_BYTES_EVENTS,
          BEEGFS_WRITE_BYTES_EVENTS,
          BEEGFS_METADATA_IOPS_EVENTS,
      ),
  )
  for key, typ, read_ev, write_ev, iops_ev in specs:
    if key not in fsio:
      continue
    raw = fsio[key]
    if not isinstance(raw, (list, tuple)):
      continue
    lst = list(raw)
    if len(lst) < 2:
      continue
    pm = _max_job_wide_combined_read_write_mb_s(jt, typ, read_ev, write_ev)
    pi = _max_job_wide_arc_sum(jt, typ, iops_ev, 1.0)
    while len(lst) < 4:
      lst.append(None)
    if lst[2] is None:
      lst[2] = pm
    if lst[3] is None:
      lst[3] = pi
    fsio[key] = lst[:4]
