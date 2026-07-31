"""Regression: avg_gpu_mem_bw_gbps conversion, blank filter, and sanity ceiling."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod
from hpcperfstats.analysis.metrics.lib.metrics import Metrics, _MAX_SANE_GPU_LINK_GBPS
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK


def _jt_with_hosts():
  return SimpleNamespace(
      _base_filter={
          "host__in": ["h1"],
          "time__gte": datetime(2026, 7, 28, tzinfo=timezone.utc),
          "time__lte": datetime(2026, 7, 30, tzinfo=timezone.utc),
      }
  )


def test_job_value_mean_applies_bytes_to_gbps_conversion(monkeypatch):
  """~7e10 B/s becomes ~70.9 GB/s after /1e9 (not stored as GB/s raw)."""
  m = Metrics.__new__(Metrics)
  rows = [
      {
          "host": "h1",
          "time": datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
          "value": 70883325695.33855,
      },
      {
          "host": "h1",
          "time": datetime(2026, 7, 28, 10, 5, tzinfo=timezone.utc),
          "value": 70883325695.33855,
      },
  ]

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      lambda *a, **k: rows,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._drop_first_bucket_per_host_if_safe",
      lambda grouped: grouped,
  )

  v = m.job_value_mean(
      _jt_with_hosts(),
      typename="nvidia_gpu",
      events=["gpu_mem_bw_bytes_rate"],
      conv=1.0 / 1e9,
      reject_dcgm_blank=True,
      max_sane=_MAX_SANE_GPU_LINK_GBPS,
  )
  assert v is not None
  assert abs(v - 70.88332569533855) < 1e-6


def test_job_value_mean_rejects_unconverted_magnitude_as_insane(monkeypatch):
  """If conversion is forgotten, raw ~7e10 fails the GB/s sanity ceiling."""
  m = Metrics.__new__(Metrics)
  rows = [
      {
          "host": "h1",
          "time": datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
          "value": 70883325695.33855,
      },
  ]
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      lambda *a, **k: rows,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._drop_first_bucket_per_host_if_safe",
      lambda grouped: grouped,
  )
  v = m.job_value_mean(
      _jt_with_hosts(),
      typename="nvidia_gpu",
      events=["gpu_mem_bw_bytes_rate"],
      conv=1.0,  # bug: forgot /1e9
      reject_dcgm_blank=True,
      max_sane=_MAX_SANE_GPU_LINK_GBPS,
  )
  assert v is None


def test_job_value_mean_excludes_dcgm_blank_gauges(monkeypatch):
  m = Metrics.__new__(Metrics)
  rows = [
      {
          "host": "h1",
          "time": datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
          "value": DCGM_FP64_BLANK,
      },
      {
          "host": "h1",
          "time": datetime(2026, 7, 28, 10, 5, tzinfo=timezone.utc),
          "value": 1.7e10,
      },
  ]
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      lambda *a, **k: rows,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._drop_first_bucket_per_host_if_safe",
      lambda grouped: grouped,
  )
  v = m.job_value_mean(
      _jt_with_hosts(),
      typename="nvidia_gpu",
      events=["gpu_mem_bw_bytes_rate"],
      conv=1.0 / 1e9,
      reject_dcgm_blank=True,
      max_sane=_MAX_SANE_GPU_LINK_GBPS,
  )
  assert v is not None
  assert abs(v - 17.0) < 1e-6


def test_catalog_avg_gpu_mem_bw_conv_is_1e_minus_9():
  """Lock catalog conv so job_arc path cannot skip special-case /1e9."""
  text = open(metrics_mod.__file__, encoding="utf-8").read()
  marker = '"avg_gpu_mem_bw_gbps": {'
  idx = text.index(marker)
  block = text[idx : idx + 200]
  assert '"conv": 1e-9' in block or '"conv": 1.0e-9' in block
  assert '"conv": 0.0' not in block
