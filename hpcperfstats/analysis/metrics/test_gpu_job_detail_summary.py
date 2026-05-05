"""Unit tests for gpu_job_detail_summary (ORM aggregate reduction, no DB)."""
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
    reduce_gpu_precision_mix,
    reduce_gpu_agg_to_util_stats,
)


def test_reduce_non_list_agg_yields_no_stats():
  """Only list/tuple aggregate rows are supported (matches ORM + cache path)."""
  active, mx, mean = reduce_gpu_agg_to_util_stats({"cnt": 4, "vmax": 1.0})
  assert active is None and mx is None and mean is None


def test_reduce_list_per_device_host_aware():
  agg = [
      {
          "host": "n1",
          "dev": "0",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 90.0,
          "vmean": 50.0,
      },
      {
          "host": "n1",
          "dev": "1",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 0.0,
          "vmean": 0.0,
      },
      {
          "host": "n2",
          "dev": "0",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 70.0,
          "vmean": 40.0,
      },
  ]
  active, mx, mean = reduce_gpu_agg_to_util_stats(agg)
  assert active == 2
  assert mx == 160.0
  assert mean == 90.0


def test_compute_job_gpu_summary_tuple_delegates(monkeypatch):
  from hpcperfstats.analysis.metrics import gpu_job_detail_summary as g

  j = MagicMock()

  def fake_agg(_jj):
    return [{"x": 1}]

  def fake_count(_jj):
    return 7

  monkeypatch.setattr(g, "gpu_agg_rows_for_job_window", fake_agg)
  monkeypatch.setattr(g, "gpu_count_total_for_job_window", fake_count)
  monkeypatch.setattr(
      g,
      "reduce_gpu_agg_to_util_stats",
      lambda _a: (1, 2.0, 3.0),
  )
  assert g.compute_job_gpu_summary_tuple(j) == (1, 2.0, 3.0, 7)


def test_compute_job_gpu_summary_tuple_swallows_errors(monkeypatch):
  from hpcperfstats.analysis.metrics import gpu_job_detail_summary as g

  j = MagicMock()

  def boom(_jj):
    raise RuntimeError("orm")

  monkeypatch.setattr(g, "gpu_agg_rows_for_job_window", boom)
  assert g.compute_job_gpu_summary_tuple(j) == (None, None, None, None)


def test_reduce_gpu_precision_mix_keeps_positive_precision_rows():
  rows = [
      {"event": "tensor_active", "vmean": 10.0},
      {"event": "fp16_active", "vmean": 30.0},
      {"event": "fp32_active", "vmean": 70.0},
      {"event": "fp64_active", "vmean": None},
      {"event": "other", "vmean": 50.0},
  ]
  assert reduce_gpu_precision_mix(rows) == {"Tensor": 10.0, "FP16": 30.0, "FP32": 70.0}
