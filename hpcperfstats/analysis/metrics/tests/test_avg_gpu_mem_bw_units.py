"""Regression: avg_gpu_mem_bw_gbps conversion, blank filter, and sanity ceiling."""

from __future__ import annotations

from datetime import UTC, datetime

from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.metrics import (
  _MAX_SANE_GPU_LINK_GBPS,
  Metrics,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

_T0 = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
_T1 = datetime(2026, 7, 28, 10, 5, tzinfo=UTC)


def _table(values):
  return table_from_records(
    [
      (t, "h1", "nvidia_gpu", "gpu_mem_bw_bytes_rate", "0", v, None, None)
      for t, v in zip((_T0, _T1), values, strict=True)
    ]
  )


def test_job_value_mean_applies_bytes_to_gbps_conversion():
  """~7e10 B/s becomes ~70.9 GB/s after /1e9 (not stored as GB/s raw)."""
  m = Metrics.__new__(Metrics)
  v = m.job_value_mean(
    _table([70883325695.33855, 70883325695.33855]),
    typename="nvidia_gpu",
    events=["gpu_mem_bw_bytes_rate"],
    conv=1.0 / 1e9,
    reject_dcgm_blank=True,
    max_sane=_MAX_SANE_GPU_LINK_GBPS,
  )
  assert v is not None
  assert abs(v - 70.88332569533855) < 1e-6


def test_job_value_mean_rejects_unconverted_magnitude_as_insane():
  """If conversion is forgotten, raw ~7e10 fails the GB/s sanity ceiling."""
  m = Metrics.__new__(Metrics)
  v = m.job_value_mean(
    _table([70883325695.33855, 70883325695.33855]),
    typename="nvidia_gpu",
    events=["gpu_mem_bw_bytes_rate"],
    conv=1.0,  # bug: forgot /1e9
    reject_dcgm_blank=True,
    max_sane=_MAX_SANE_GPU_LINK_GBPS,
  )
  assert v is None


def test_job_value_mean_excludes_dcgm_blank_gauges():
  """A DCGM blank sample is dropped before bucketing (single bucket remains)."""
  m = Metrics.__new__(Metrics)
  v = m.job_value_mean(
    _table([DCGM_FP64_BLANK, 1.7e10]),
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
