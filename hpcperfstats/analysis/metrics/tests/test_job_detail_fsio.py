"""Unit tests for job_detail_fsio on the columnar job table (no Django DB)."""

import pandas as pd

from hpcperfstats.analysis.metrics.lib.beegfs_metadata_iops_events import (
  BEEGFS_METADATA_IOPS_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.job_detail_fsio import (
  compute_job_detail_fsio_metric_rows,
  extend_fsio_payload_lists_with_peaks,
  fsio_job_detail_catalog,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
  LLITE_METADATA_IOPS_EVENTS,
)

_T0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
_MB = 1024 * 1024


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


def _llite_rows(
  *, read_event="vfs_read_bytes", write_event="vfs_write_bytes", peak_iops=100.0
):
  """Lustre rows: 1 MB read / 2 MB write deltas; 4 + 6 MB/s arcs; metadata IOPS."""
  rows = [
    (_T0, "n1", "lustre_llite", read_event, None, None, 4.0 * _MB, 1.0 * _MB),
    (_T0, "n1", "lustre_llite", write_event, None, None, 6.0 * _MB, 2.0 * _MB),
  ]
  rows.append(
    (
      _T0,
      "n1",
      "lustre_llite",
      LLITE_METADATA_IOPS_EVENTS[0],
      None,
      None,
      peak_iops,
      1.0,
    )
  )
  return rows


def _nfs_rows():
  """NFS rows: 10 MB read / 20 MB write deltas; 1 + 2 MB/s arcs; 50 IOPS."""
  return [
    (_T0, "n1", "nfs", "normal_read", None, None, 1.0 * _MB, 10.0 * _MB),
    (_T0, "n1", "nfs", "normal_write", None, None, 2.0 * _MB, 20.0 * _MB),
    (_T0, "n1", "nfs", "READ_ops", None, None, 30.0, 1.0),
    (_T0, "n1", "nfs", "WRITE_ops", None, None, 20.0, 1.0),
  ]


def _beegfs_rows():
  """BeeGFS rows: 2 MB read / 4 MB write deltas; 3 + 5 MB/s arcs; 40 IOPS."""
  return [
    (
      _T0,
      "n1",
      "beegfs_client",
      "vfs_read_bytes",
      None,
      None,
      3.0 * _MB,
      2.0 * _MB,
    ),
    (
      _T0,
      "n1",
      "beegfs_client",
      "vfs_write_bytes",
      None,
      None,
      5.0 * _MB,
      4.0 * _MB,
    ),
    (
      _T0,
      "n1",
      "beegfs_client",
      BEEGFS_METADATA_IOPS_EVENTS[0],
      None,
      None,
      40.0,
      1.0,
    ),
  ]


def test_compute_llite_populates_llite_rows_and_omits_nfs_when_nfs_absent():
  rows = compute_job_detail_fsio_metric_rows(table_from_records(_llite_rows()))
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0
  assert by_m["detail_fsio_llite_peak_mb_s"]["value"] == 10.0
  assert by_m["detail_fsio_llite_peak_iops"]["value"] == 100.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_write_mb"]["value"] is None
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] is None


def test_compute_llite_accepts_legacy_read_bytes_event_names():
  """Legacy ``read_bytes`` / ``write_bytes`` rows are canonicalised to ``vfs_*``."""
  table = table_from_records(
    _llite_rows(read_event="read_bytes", write_event="write_bytes")
  )
  rows = compute_job_detail_fsio_metric_rows(table)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_llite_write_mb"]["value"] == 2.0


def test_compute_dual_llite_and_nfs_when_both_present():
  rows = compute_job_detail_fsio_metric_rows(
    table_from_records(_llite_rows() + _nfs_rows())
  )
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_nfs_write_mb"]["value"] == 20.0
  assert by_m["detail_fsio_nfs_peak_mb_s"]["value"] == 3.0
  assert by_m["detail_fsio_nfs_peak_iops"]["value"] == 50.0
  assert by_m["detail_fsio_nfs_read_mb"]["no_data_reason"] is None
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] is None


def test_compute_nfs_when_no_llite():
  rows = compute_job_detail_fsio_metric_rows(table_from_records(_nfs_rows()))
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_nfs_write_mb"]["value"] == 20.0
  assert by_m["detail_fsio_nfs_peak_mb_s"]["value"] == 3.0
  assert by_m["detail_fsio_nfs_peak_iops"]["value"] == 50.0


def test_compute_beegfs_only_populates_beegfs_rows():
  rows = compute_job_detail_fsio_metric_rows(table_from_records(_beegfs_rows()))
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] == 2.0
  assert by_m["detail_fsio_beegfs_write_mb"]["value"] == 4.0
  assert by_m["detail_fsio_beegfs_peak_mb_s"]["value"] == 8.0
  assert by_m["detail_fsio_beegfs_peak_iops"]["value"] == 40.0
  assert by_m["detail_fsio_llite_read_mb"]["value"] is None
  assert by_m["detail_fsio_nfs_read_mb"]["value"] is None


def test_compute_triple_llite_nfs_beegfs_when_all_present():
  table = table_from_records(_llite_rows() + _nfs_rows() + _beegfs_rows())
  rows = compute_job_detail_fsio_metric_rows(table)
  by_m = {r["metric"]: r for r in rows}
  assert by_m["detail_fsio_llite_read_mb"]["value"] == 1.0
  assert by_m["detail_fsio_nfs_read_mb"]["value"] == 10.0
  assert by_m["detail_fsio_beegfs_read_mb"]["value"] == 2.0
  assert by_m["detail_fsio_beegfs_write_mb"]["value"] == 4.0
  assert by_m["detail_fsio_beegfs_peak_mb_s"]["value"] == 8.0
  assert by_m["detail_fsio_beegfs_peak_iops"]["value"] == 40.0


def test_extend_fsio_payload_lists_with_peaks_fills_legacy_two_tuple():
  fsio = {"llite": [1.0, 2.0]}
  extend_fsio_payload_lists_with_peaks(
    fsio, table_from_records(_llite_rows(peak_iops=9.0))
  )
  assert fsio["llite"] == [1.0, 2.0, 10.0, 9.0]


def test_extend_fsio_payload_lists_with_peaks_fills_beegfs():
  fsio = {"beegfs": [2.0, 4.0]}
  extend_fsio_payload_lists_with_peaks(fsio, table_from_records(_beegfs_rows()))
  assert fsio["beegfs"] == [2.0, 4.0, 8.0, 40.0]
