"""Job-detail FSIO totals (Lustre llite vs NFS) for metrics_data; mirrors ``api.job_detail`` _fetch_fsio."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Messages align with persisted ``no_data_reason`` for detail_fsio_* rows.
NO_FSIO_LLITE_DATA = "No Lustre llite read/write byte deltas for this job"
NO_FSIO_NFS_DATA = "No NFS client byte deltas for this job"
NO_FSIO_NFS_WHEN_LLITE = "NFS totals omitted when Lustre llite data is used for job detail FSIO"

_FSIO_METRICS: Tuple[Tuple[str, str, str], ...] = (
    ("detail_fsio_llite_read_mb", "llite", "MB"),
    ("detail_fsio_llite_write_mb", "llite", "MB"),
    ("detail_fsio_nfs_read_mb", "nfs", "MB"),
    ("detail_fsio_nfs_write_mb", "nfs", "MB"),
)


def fsio_job_detail_catalog() -> Tuple[Tuple[str, str, str], ...]:
  """(metric, type, units) for catalog and compute_metrics."""
  return _FSIO_METRICS


def compute_job_detail_fsio_metric_rows(jt: Any) -> List[Dict[str, Any]]:
  """Build four metrics_data-shaped dicts from ``jid_table`` (same rules as job_detail API)."""
  llite_read: Optional[float] = None
  llite_write: Optional[float] = None
  try:
    llite_df = jt.get_llite_delta_by_event()
    if not llite_df.empty and "delta_sum" in llite_df.columns:
      llite_df = llite_df.copy()
      llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
      read_row = llite_df[llite_df["event"] == "read_bytes"]
      write_row = llite_df[llite_df["event"] == "write_bytes"]
      read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
      write_val = float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
      llite_read, llite_write = read_val, write_val
  except Exception:
    pass

  nfs_read: Optional[float] = None
  nfs_write: Optional[float] = None
  if llite_read is None and llite_write is None:
    try:
      nfs_totals = jt.get_nfs_delta_totals_mb()
      if nfs_totals is not None:
        nfs_read, nfs_write = float(nfs_totals[0]), float(nfs_totals[1])
    except Exception:
      pass

  rows: List[Dict[str, Any]] = []
  llite_ok = llite_read is not None and llite_write is not None
  nfs_ok = nfs_read is not None and nfs_write is not None

  for metric_name, row_type, units in _FSIO_METRICS:
    if metric_name.startswith("detail_fsio_llite_"):
      if llite_ok:
        val = llite_read if "read" in metric_name else llite_write
        rows.append({
            "type": row_type,
            "metric": metric_name,
            "units": units,
            "value": float(val),
            "no_data_reason": None,
        })
      else:
        rows.append({
            "type": row_type,
            "metric": metric_name,
            "units": units,
            "value": None,
            "no_data_reason": NO_FSIO_LLITE_DATA,
        })
    else:
      if llite_ok:
        rows.append({
            "type": row_type,
            "metric": metric_name,
            "units": units,
            "value": None,
            "no_data_reason": NO_FSIO_NFS_WHEN_LLITE,
        })
      elif nfs_ok:
        val = nfs_read if "read" in metric_name else nfs_write
        rows.append({
            "type": row_type,
            "metric": metric_name,
            "units": units,
            "value": float(val),
            "no_data_reason": None,
        })
      else:
        rows.append({
            "type": row_type,
            "metric": metric_name,
            "units": units,
            "value": None,
            "no_data_reason": NO_FSIO_NFS_DATA,
        })

  return rows
