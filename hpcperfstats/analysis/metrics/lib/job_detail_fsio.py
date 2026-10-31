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
  _FSIO_METRICS: ``_FSIO_METRICS``.
  logger: ``logger``.
"""

from __future__ import annotations

import logging
from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
  fsio_peaks,
  fsio_totals,
)

logger = logging.getLogger(__name__)

# Messages align with persisted ``no_data_reason`` for detail_fsio_* rows.
NO_FSIO_LLITE_DATA = "No Lustre llite read/write byte deltas for this job"
NO_FSIO_NFS_DATA = "No NFS client byte deltas for this job"
NO_FSIO_BEEGFS_DATA = "No BeeGFS client read/write byte deltas for this job"
# Retained for older persisted rows / tests that may still reference the string.
NO_FSIO_NFS_WHEN_LLITE = (
  "NFS totals omitted when Lustre llite data is used for job detail FSIO"
)
NO_FSIO_LLITE_PEAK_MB_S = (
  "No Lustre llite byte counter time series for peak MB/s"
)
NO_FSIO_LLITE_PEAK_IOPS = (
  "No Lustre llite metadata operation time series for peak IOPS"
)
NO_FSIO_NFS_PEAK_MB_S = "No NFS client byte counter time series for peak MB/s"
NO_FSIO_NFS_PEAK_IOPS = "No NFS READ/WRITE op time series for peak IOPS"
NO_FSIO_BEEGFS_PEAK_MB_S = (
  "No BeeGFS client byte counter time series for peak MB/s"
)
NO_FSIO_BEEGFS_PEAK_IOPS = (
  "No BeeGFS client metadata operation time series for peak IOPS"
)

_FSIO_METRICS: tuple[tuple[str, str, str], ...] = (
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


def fsio_job_detail_catalog() -> tuple[tuple[str, str, str], ...]:
  """
  (metric, type, units) for catalog and compute_metrics.

  Returns:
    Tuple[Tuple[str, str, str], ...]: Ordered FSIO catalog entries (12 metrics).

  Examples:
    >>> len(fsio_job_detail_catalog())
    12
  """
  return _FSIO_METRICS


def compute_job_detail_fsio_metric_rows(jt: Any) -> list[dict[str, Any]]:
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
  totals = fsio_totals(jt)
  llite_read, llite_write = totals.get("llite", (None, None))
  nfs_read, nfs_write = totals.get("nfs", (None, None))
  beegfs_read, beegfs_write = totals.get("beegfs", (None, None))

  rows: list[dict[str, Any]] = []
  llite_ok = llite_read is not None and llite_write is not None
  nfs_ok = nfs_read is not None and nfs_write is not None
  beegfs_ok = beegfs_read is not None and beegfs_write is not None

  llite_peak_mb, llite_peak_iops = (
    fsio_peaks(jt, "llite") if llite_ok else (None, None)
  )
  nfs_peak_mb, nfs_peak_iops = fsio_peaks(jt, "nfs") if nfs_ok else (None, None)
  beegfs_peak_mb, beegfs_peak_iops = (
    fsio_peaks(jt, "beegfs") if beegfs_ok else (None, None)
  )

  for metric_name, row_type, units in _FSIO_METRICS:
    if metric_name.startswith("detail_fsio_llite_"):
      if not llite_ok:
        rows.append(
          _row(metric_name, row_type, units, None, NO_FSIO_LLITE_DATA)
        )
        continue
      if metric_name == "detail_fsio_llite_read_mb":
        rows.append(_row(metric_name, row_type, units, float(llite_read), None))
      elif metric_name == "detail_fsio_llite_write_mb":
        rows.append(
          _row(metric_name, row_type, units, float(llite_write), None)
        )
      elif metric_name == "detail_fsio_llite_peak_mb_s":
        if llite_peak_mb is not None:
          rows.append(_row(metric_name, row_type, units, llite_peak_mb, None))
        else:
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_LLITE_PEAK_MB_S,
            )
          )
      else:
        if llite_peak_iops is not None:
          rows.append(_row(metric_name, row_type, units, llite_peak_iops, None))
        else:
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_LLITE_PEAK_IOPS,
            )
          )
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
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_NFS_PEAK_MB_S,
            )
          )
      else:
        if nfs_peak_iops is not None:
          rows.append(_row(metric_name, row_type, units, nfs_peak_iops, None))
        else:
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_NFS_PEAK_IOPS,
            )
          )
    else:
      if not beegfs_ok:
        rows.append(
          _row(metric_name, row_type, units, None, NO_FSIO_BEEGFS_DATA)
        )
      elif metric_name == "detail_fsio_beegfs_read_mb":
        rows.append(
          _row(metric_name, row_type, units, float(beegfs_read), None)
        )
      elif metric_name == "detail_fsio_beegfs_write_mb":
        rows.append(
          _row(metric_name, row_type, units, float(beegfs_write), None)
        )
      elif metric_name == "detail_fsio_beegfs_peak_mb_s":
        if beegfs_peak_mb is not None:
          rows.append(_row(metric_name, row_type, units, beegfs_peak_mb, None))
        else:
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_BEEGFS_PEAK_MB_S,
            )
          )
      else:
        if beegfs_peak_iops is not None:
          rows.append(
            _row(metric_name, row_type, units, beegfs_peak_iops, None)
          )
        else:
          rows.append(
            _row(
              metric_name,
              row_type,
              units,
              None,
              NO_FSIO_BEEGFS_PEAK_IOPS,
            )
          )

  return rows


def _row(
  metric: str,
  row_type: str,
  units: str,
  val: float | None,
  reason: str | None,
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


def extend_fsio_payload_lists_with_peaks(fsio: dict[str, Any], jt: Any) -> None:
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
  for key in ("llite", "nfs", "beegfs"):
    if key not in fsio:
      continue
    raw = fsio[key]
    if not isinstance(raw, (list, tuple)):
      continue
    lst = list(raw)
    if len(lst) < 2:
      continue
    pm, pi = fsio_peaks(jt, key)
    while len(lst) < 4:
      lst.append(None)
    if lst[2] is None:
      lst[2] = pm
    if lst[3] is None:
      lst[3] = pi
    fsio[key] = lst[:4]
