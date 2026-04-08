"""Unit tests for job_detail_fsio (no Django DB)."""
from unittest.mock import MagicMock

import pandas as pd

from hpcperfstats.analysis.metrics.job_detail_fsio import (
    compute_job_detail_fsio_metric_rows,
    fsio_job_detail_catalog,
)


def test_fsio_job_detail_catalog_has_four_metrics():
  names = [m for m, _, _ in fsio_job_detail_catalog()]
  assert names == [
      "detail_fsio_llite_read_mb",
      "detail_fsio_llite_write_mb",
      "detail_fsio_nfs_read_mb",
      "detail_fsio_nfs_write_mb",
  ]


def test_compute_llite_populates_llite_rows_and_omits_nfs():
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "read_bytes", "delta_sum": 1048576.0},
          {"event": "write_bytes", "delta_sum": 2097152.0},
      ]
  )
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_write_mb"]["value"] is None


def test_compute_nfs_when_no_llite():
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame()
  jt.get_nfs_delta_totals_mb.return_value = [10.0, 20.0]
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_nfs_write_mb"]["value"] == 20.0
