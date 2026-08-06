"""Unit tests for gpu_job_detail_summary (ORM aggregate reduction, no DB)."""
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
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


def test_reduce_skips_dcgm_blank_vmax():
  from hpcperfstats.lib.dcgm_blank import DCGM_INT64_BLANK

  agg = [
      {
          "host": "n1",
          "dev": "0",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": float(DCGM_INT64_BLANK),
          "vmean": float(DCGM_INT64_BLANK),
      },
      {
          "host": "n1",
          "dev": "1",
          "event": "gpu_util",
          "cnt": 4,
          "vmax": 80.0,
          "vmean": 40.0,
      },
  ]
  active, mx, mean = reduce_gpu_agg_to_util_stats(agg)
  assert active == 1
  assert mx == 80.0
  assert mean == 40.0


def test_compute_job_gpu_summary_tuple_delegates(monkeypatch):
  from hpcperfstats.analysis.metrics.lib import gpu_job_detail_summary as g

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
  from hpcperfstats.analysis.metrics.lib import gpu_job_detail_summary as g

  j = MagicMock()

  def boom(_jj):
    raise RuntimeError("orm")

  monkeypatch.setattr(g, "gpu_agg_rows_for_job_window", boom)
  assert g.compute_job_gpu_summary_tuple(j) == (None, None, None, None)


def test_gpu_agg_rows_uses_type_detail_batch_and_time_chunks(monkeypatch):
  """GPU util aggregates must use batch=8 and host×time helpers."""
  from datetime import datetime, timezone

  from hpcperfstats.analysis.metrics.lib import gpu_job_detail_summary as g
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  j = MagicMock()
  j.acct_host_list = ["h{0}.example.com".format(i) for i in range(10)]
  j.start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
  j.end_time = datetime(2024, 1, 1, 2, tzinfo=timezone.utc)

  seen_batches = []

  def fake_iter(hosts, tkw, *, batch_size=None, slice_s=None):
    seen_batches.append(batch_size)
    yield (["h0.example.com"], {"time__gte": j.start_time, "time__lte": j.end_time})

  def fake_retry(hosts, tf, run, merge, **kwargs):
    return [
        {
            "host": "h0.example.com",
            "dev": "0",
            "event": "gpu_util",
            "cnt": 4,
            "vmax": 50.0,
            "vmean": 25.0,
        }
    ]

  monkeypatch.setattr(jt_mod, "_iter_host_time_query_chunks", fake_iter)
  monkeypatch.setattr(jt_mod, "_run_with_host_time_timeout_retry", fake_retry)
  monkeypatch.setattr(g.cfg, "get_metrics_plot_aggregate_time_slice_s", lambda: 3600)

  out = g.gpu_agg_rows_for_job_window(j)
  assert out
  assert seen_batches == [jt_mod.TYPE_DETAIL_HOST_QUERY_BATCH]
