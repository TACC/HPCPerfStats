"""Unit tests for job_detail_fsio (no Django DB)."""
from unittest.mock import MagicMock

import pandas as pd

from hpcperfstats.analysis.metrics.lib.job_detail_fsio import (
    compute_job_detail_fsio_metric_rows,
    extend_fsio_payload_lists_with_peaks,
    fsio_job_detail_catalog,
)
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


def test_fsio_job_detail_catalog_has_twelve_metrics():
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
      "detail_fsio_beegfs_read_mb",
      "detail_fsio_beegfs_write_mb",
      "detail_fsio_beegfs_peak_mb_s",
      "detail_fsio_beegfs_peak_iops",
  ]


def _llite_agg(typ, val_col, events, conv=1.0, *, peak_iops=100.0):
  """Mock get_aggregate_df; peak MB/s comes from read(4)+write(6) combined series."""
  del val_col, conv
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  ev = list(events)
  if typ in ("llite", "lustre_llite") and ev == list(LLITE_READ_BYTES_EVENTS):
    return pd.DataFrame([("n1", t0, 4.0)], columns=["host", "time", "sum_val"])
  if typ in ("llite", "lustre_llite") and ev == list(LLITE_WRITE_BYTES_EVENTS):
    return pd.DataFrame([("n1", t0, 6.0)], columns=["host", "time", "sum_val"])
  if typ in ("llite", "lustre_llite") and ev == list(LLITE_METADATA_IOPS_EVENTS):
    return pd.DataFrame([("n1", t0, peak_iops)], columns=["host", "time", "sum_val"])
  if typ in ("llite", "lustre_llite") and len(ev) > 2:
    return pd.DataFrame([("n1", t0, peak_iops)], columns=["host", "time", "sum_val"])
  if typ == "beegfs_client" and ev == list(BEEGFS_READ_BYTES_EVENTS):
    return pd.DataFrame([("n1", t0, 3.0)], columns=["host", "time", "sum_val"])
  if typ == "beegfs_client" and ev == list(BEEGFS_WRITE_BYTES_EVENTS):
    return pd.DataFrame([("n1", t0, 5.0)], columns=["host", "time", "sum_val"])
  if typ == "beegfs_client" and ev == list(BEEGFS_METADATA_IOPS_EVENTS):
    return pd.DataFrame([("n1", t0, 40.0)], columns=["host", "time", "sum_val"])
  return pd.DataFrame(columns=["host", "time", "sum_val"])


def test_compute_llite_populates_llite_rows_and_omits_nfs_when_nfs_absent():
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "vfs_read_bytes", "delta_sum": 1048576.0},
          {"event": "vfs_write_bytes", "delta_sum": 2097152.0},
      ]
  )
  jt.get_nfs_delta_totals_mb.return_value = None
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame()
  jt.get_aggregate_df.side_effect = _llite_agg
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0
  assert by_m["detail_fsio_llite_peak_mb_s"]["value"] == 10.0
  assert by_m["detail_fsio_llite_peak_iops"]["value"] == 100.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_write_mb"]["value"] is None
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] is None


def test_compute_llite_accepts_legacy_read_bytes_event_names():
  """get_llite_delta may still surface legacy names before canonicalize; FSIO accepts both."""
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "read_bytes", "delta_sum": 1048576.0},
          {"event": "write_bytes", "delta_sum": 2097152.0},
      ]
  )
  jt.get_nfs_delta_totals_mb.return_value = None
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame()
  jt.get_aggregate_df.side_effect = _llite_agg
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0


def test_compute_dual_llite_and_nfs_when_both_present():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "vfs_read_bytes", "delta_sum": 1048576.0},
          {"event": "vfs_write_bytes", "delta_sum": 2097152.0},
      ]
  )
  jt.get_nfs_delta_totals_mb.return_value = [10.0, 20.0]
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame()

  def _agg(typ, val_col, events, conv=1.0):
    base = _llite_agg(typ, val_col, events, conv)
    if not base.empty:
      return base
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
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_nfs_write_mb"]["value"] == 20.0
  assert by_m["detail_fsio_nfs_peak_mb_s"]["value"] == 3.0
  assert by_m["detail_fsio_nfs_peak_iops"]["value"] == 50.0
  assert by_m["detail_fsio_nfs_read_mb"]["no_data_reason"] is None
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] is None


def test_compute_nfs_when_no_llite():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame()
  jt.get_nfs_delta_totals_mb.return_value = [10.0, 20.0]
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame()

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


def test_compute_beegfs_only_populates_beegfs_rows():
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame()
  jt.get_nfs_delta_totals_mb.return_value = None
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "vfs_read_bytes", "delta_sum": 2097152.0},
          {"event": "vfs_write_bytes", "delta_sum": 4194304.0},
      ]
  )
  jt.get_aggregate_df.side_effect = _llite_agg
  rows = compute_job_detail_fsio_metric_rows(jt)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] == 2.0
  assert by_m["detail_fsio_beegfs_write_mb"]["value"] == 4.0
  assert by_m["detail_fsio_beegfs_peak_mb_s"]["value"] == 8.0
  assert by_m["detail_fsio_beegfs_peak_iops"]["value"] == 40.0
  assert by_m["detail_fsio_llite_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None


def test_compute_triple_llite_nfs_beegfs_when_all_present():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.get_llite_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "vfs_read_bytes", "delta_sum": 1048576.0},
          {"event": "vfs_write_bytes", "delta_sum": 2097152.0},
      ]
  )
  jt.get_nfs_delta_totals_mb.return_value = [10.0, 20.0]
  jt.get_beegfs_delta_by_event.return_value = pd.DataFrame(
      [
          {"event": "vfs_read_bytes", "delta_sum": 2097152.0},
          {"event": "vfs_write_bytes", "delta_sum": 4194304.0},
      ]
  )

  def _agg(typ, val_col, events, conv=1.0):
    base = _llite_agg(typ, val_col, events, conv)
    if not base.empty:
      return base
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
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] == 2.0
  assert by_m["detail_fsio_beegfs_write_mb"]["value"] == 4.0
  assert by_m["detail_fsio_beegfs_peak_mb_s"]["value"] == 8.0
  assert by_m["detail_fsio_beegfs_peak_iops"]["value"] == 40.0


def test_extend_fsio_payload_lists_with_peaks_fills_legacy_two_tuple():
  jt = MagicMock()
  jt.get_aggregate_df.side_effect = lambda *a, **k: _llite_agg(
      *a, **k, peak_iops=9.0)
  fsio = {"llite": [1.0, 2.0]}
  extend_fsio_payload_lists_with_peaks(fsio, jt)
  assert fsio["llite"] == [1.0, 2.0, 10.0, 9.0]


def test_extend_fsio_payload_lists_with_peaks_fills_beegfs():
  jt = MagicMock()
  jt.get_aggregate_df.side_effect = _llite_agg
  fsio = {"beegfs": [2.0, 4.0]}
  extend_fsio_payload_lists_with_peaks(fsio, jt)
  assert fsio["beegfs"] == [2.0, 4.0, 8.0, 40.0]