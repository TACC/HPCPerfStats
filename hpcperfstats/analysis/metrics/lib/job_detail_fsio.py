"""
Job-detail FSIO totals (Lustre llite, NFS, BeeGFS) for metrics_data; mirrors
the job_detail artifact ``fsio``.

Attributes:
  NO_FSIO_BEEGFS_DATA: ``no_data_reason`` when BeeGFS has no byte deltas.
  NO_FSIO_BEEGFS_PEAK_IOPS: ``no_data_reason`` for a missing BeeGFS IOPS peak.
  NO_FSIO_BEEGFS_PEAK_MB_S: ``no_data_reason`` for a missing BeeGFS MB/s peak.
  NO_FSIO_LLITE_DATA: ``no_data_reason`` when llite has no byte deltas.
  NO_FSIO_LLITE_PEAK_IOPS: ``no_data_reason`` for a missing llite IOPS peak.
  NO_FSIO_LLITE_PEAK_MB_S: ``no_data_reason`` for a missing llite MB/s peak.
  NO_FSIO_NFS_DATA: ``no_data_reason`` when NFS has no byte deltas.
  NO_FSIO_NFS_PEAK_IOPS: ``no_data_reason`` for a missing NFS IOPS peak.
  NO_FSIO_NFS_PEAK_MB_S: ``no_data_reason`` for a missing NFS MB/s peak.
  _FSIO_METRICS: ``(metric, family, units)`` catalog, in persisted row order.
  _FSIO_FAMILY_REASONS: Per family ``(no data, no peak MB/s, no peak IOPS)``
    reasons, in ``fsio_peaks`` call order.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
  fsio_peaks,
  fsio_totals,
)

# Messages align with persisted ``no_data_reason`` for detail_fsio_* rows.
NO_FSIO_LLITE_DATA = "No Lustre llite read/write byte deltas for this job"
NO_FSIO_NFS_DATA = "No NFS client byte deltas for this job"
NO_FSIO_BEEGFS_DATA = "No BeeGFS client read/write byte deltas for this job"
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

_FSIO_FAMILY_REASONS: dict[str, tuple[str, str, str]] = {
  "llite": (
    NO_FSIO_LLITE_DATA,
    NO_FSIO_LLITE_PEAK_MB_S,
    NO_FSIO_LLITE_PEAK_IOPS,
  ),
  "nfs": (NO_FSIO_NFS_DATA, NO_FSIO_NFS_PEAK_MB_S, NO_FSIO_NFS_PEAK_IOPS),
  "beegfs": (
    NO_FSIO_BEEGFS_DATA,
    NO_FSIO_BEEGFS_PEAK_MB_S,
    NO_FSIO_BEEGFS_PEAK_IOPS,
  ),
}


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
  # Per family: metric suffix -> (value, no_data_reason when value is None);
  # None when the family has no read+write byte deltas.
  family_values: dict[str, dict[str, tuple[float | None, str]] | None] = {}
  for family, (_, no_peak_mb_s, no_peak_iops) in _FSIO_FAMILY_REASONS.items():
    read_mb, write_mb = totals.get(family, (None, None))
    if read_mb is None or write_mb is None:
      family_values[family] = None
      continue
    peak_mb_s, peak_iops = fsio_peaks(jt, family)
    family_values[family] = {
      "read_mb": (float(read_mb), ""),
      "write_mb": (float(write_mb), ""),
      "peak_mb_s": (peak_mb_s, no_peak_mb_s),
      "peak_iops": (peak_iops, no_peak_iops),
    }

  rows: list[dict[str, Any]] = []
  for metric_name, family, units in _FSIO_METRICS:
    values = family_values[family]
    if values is None:
      no_data = _FSIO_FAMILY_REASONS[family][0]
      rows.append(_row(metric_name, family, units, None, no_data))
      continue
    suffix = metric_name.removeprefix(f"detail_fsio_{family}_")
    val, missing_reason = values[suffix]
    reason = None if val is not None else missing_reason
    rows.append(_row(metric_name, family, units, val, reason))
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
