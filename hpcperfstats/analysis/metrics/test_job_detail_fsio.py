"""Unit tests for job_detail_fsio (no Django DB)."""
from unittest.mock import MagicMock

import pandas as pd

from hpcperfstats.analysis.metrics.job_detail_fsio import (
    compute_job_detail_fsio_metric_rows,
    extend_fsio_payload_lists_with_peaks,
    fsio_job_detail_catalog,
)


def test_fsio_job_detail_catalog_has_eight_metrics():
  names = [m for m, _, _ in fsio_job_detail_catalog()]
  assert names == [
      "detail_fsio_llite_read_mb",
      "detail_fsio_llite_write_mb",
      "detail_fsio_llite_peak_mb_s",
      "detail_fsio_llite_peak_iops",
      "detail_fsio_nfs_read_mb",
      "detail_fsio_nfs_write_mb",
      "detail_fsio_nfs_peak_mb_s",
      "detail_fsio_nfs_peak_iops",
  ]


def test_compute_llite_populates_llite_rows_and_omits_nfs():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "read_bytes", "delta_sum": 1048576.0},
          {"event": "write_bytes", "delta_sum": 2097152.0},
      ]
  )

  def _agg(typ, val_col, events, conv=1.0):
    del val_col, conv
    ev = list(events)
    if typ == "llite" and ev == ["read_bytes"]:
      return pd.DataFrame([("n1", t0, 4.0)], columns=["host", "time", "sum_val"])
    if typ == "llite" and ev == ["write_bytes"]:
      return pd.DataFrame([("n1", t0, 6.0)], columns=["host", "time", "sum_val"])
    if typ == "llite" and len(ev) > 2:
      return pd.DataFrame([("n1", t0, 100.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = _agg
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0
  assert by_m["detail_fsio_llite_peak_mb_s"]["value"] == 10.0
  assert by_m["detail_fsio_llite_peak_iops"]["value"] == 100.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_write_mb"]["value"] is None


def test_compute_nfs_when_no_llite():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame()
  jt.get_nfs_delta_totals_mb.return_value = [10.0, 20.0]

  def _agg(typ, val_col, events, conv=1.0):
    del val_col, conv
    ev = list(events)
    if typ == "nfs" and set(ev) == {"normal_read", "direct_read", "server_read"}:
      return pd.DataFrame([("n1", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ == "nfs" and set(ev) == {"normal_write", "direct_write", "server_write"}:
      return pd.DataFrame([("n1", t0, 2.0)], columns=["host", "time", "sum_val"])
    if typ == "nfs" and set(ev) == {"READ_ops", "WRITE_ops"}:
      return pd.DataFrame([("n1", t0, 50.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = _agg
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_nfs_write_mb"]["value"] == 20.0
  assert by_m["detail_fsio_nfs_peak_mb_s"]["value"] == 3.0
  assert by_m["detail_fsio_nfs_peak_iops"]["value"] == 50.0


def test_extend_fsio_payload_lists_with_peaks_fills_legacy_two_tuple():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()

  def _agg(typ, val_col, events, conv=1.0):
    del val_col, conv
    ev = list(events)
    if typ == "llite" and ev == ["read_bytes"]:
      return pd.DataFrame([("n1", t0, 4.0)], columns=["host", "time", "sum_val"])
    if typ == "llite" and ev == ["write_bytes"]:
      return pd.DataFrame([("n1", t0, 6.0)], columns=["host", "time", "sum_val"])
    if typ == "llite" and len(ev) > 2:
      return pd.DataFrame([("n1", t0, 9.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = _agg
  fsio = {"llite": [1.0, 2.0]}
  extend_fsio_payload_lists_with_peaks(fsio, jt)
  assert fsio["llite"] == [1.0, 2.0, 10.0, 9.0]
