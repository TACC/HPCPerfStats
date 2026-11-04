"""Unit tests for gpu_job_detail_summary (JobFrame aggregates, no DB)."""

from datetime import UTC
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
  gpu_agg_rows_for_job_window,
  gpu_count_total_for_job_window,
  gpu_inventory_for_job_window,
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


def test_gpu_agg_rows_read_from_the_job_frame():
  """Util rows come from the JobFrame: Count/Max/Avg per (host, dev, event)."""
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t0 = datetime(2024, 1, 1, 1, tzinfo=UTC)
  rows = [
    (
      t0.replace(minute=m),
      "h0.example.com",
      "nvidia_gpu",
      "gpu_util",
      "0",
      v,
      None,
      None,
    )
    for m, v in ((0, 10.0), (1, 50.0), (2, 15.0), (3, 25.0))
  ]
  out = gpu_agg_rows_for_job_window(table_from_records(rows))
  assert out == [
    {
      "host": "h0.example.com",
      "dev": "0",
      "event": "gpu_util",
      "cnt": 4,
      "vmax": 50.0,
      "vmean": 25.0,
    }
  ]


def test_shared_gpu_count_is_affinity_length():
  """Shared jobs count affinity devices, not the host gpu_count gauge."""
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t0 = datetime(2024, 1, 1, 1, tzinfo=UTC)
  table = table_from_records(
    [(t0, "h", "nvidia_gpu", "gpu_count", "0", 4.0, None, None)],
    shared=True,
    gpu_affinity_devs=["0"],
  )
  assert gpu_count_total_for_job_window(table) == 1


def test_shared_empty_gpu_list_is_zero():
  """An empty affinity list is no GPUs, not the host inventory."""
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t0 = datetime(2024, 1, 1, 1, tzinfo=UTC)
  table = table_from_records(
    [
      (t0, "h", "nvidia_gpu", "gpu_count", "0", 4.0, None, None),
      (t0, "h", "nvidia_gpu", "gpu_util", "0", 40.0, None, None),
    ],
    shared=True,
    gpu_affinity_devs=[],
  )
  assert gpu_count_total_for_job_window(table) == 0
  assert gpu_inventory_for_job_window(table) == []
  assert gpu_agg_rows_for_job_window(table) == []


def _jid_79282_shape_table():
  """Job 79282 shape: one unmarked 4-GPU host, one marked host (no GPUs).

  Per-host affinity: ``gpu_host`` has no ``job_affinity`` row (state 1,
  owns every GPU); ``cpu_host`` has ``gpu_list=[]``. Merging the lists of
  all hosts (``[]``) used to hide ``gpu_host``'s GPUs too.

  Returns:
    ColumnarJobTable: Shared table with a per-host GPU affinity map.
  """
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t = [datetime(2024, 1, 1, 1, m, tzinfo=UTC) for m in range(4)]
  recs = []
  for ts in t:
    for dev in ("0", "1", "2", "3"):
      recs.append(
        (ts, "gpu_host", "nvidia_gpu", "gpu_count", dev, 4.0, None, None)
      )
      recs.append(
        (ts, "gpu_host", "nvidia_gpu", "gpu_util", dev, 50.0, None, None)
      )
  return table_from_records(
    recs, shared=True, gpu_affinity_devs={"cpu_host": []}
  )


def test_host_without_affinity_row_keeps_its_gpus():
  """A host with no affinity row is unrestricted even on a shared job."""
  table = _jid_79282_shape_table()
  rows = gpu_agg_rows_for_job_window(table)
  assert sorted({(r["host"], r["dev"]) for r in rows}) == [
    ("gpu_host", d) for d in ("0", "1", "2", "3")
  ]
  active, mx, mean = reduce_gpu_agg_to_util_stats(rows)
  # Four active GPUs at 50 %: max and mean are sums across devices.
  assert (active, mx, mean) == (4, 200.0, 200.0)
  assert gpu_count_total_for_job_window(table) == 4
  assert len(gpu_inventory_for_job_window(table)) == 4


def test_per_host_gpu_lists_add_up_and_restrict_each_host():
  """Restricted hosts count their own list; other hosts their inventory."""
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  ts = [datetime(2024, 1, 1, 1, m, tzinfo=UTC) for m in range(4)]
  recs = [
    (t, h, "nvidia_gpu", ev, dev, v, None, None)
    for t in ts
    for h in ("a", "b")
    for dev in ("0", "1", "2", "3")
    for ev, v in (("gpu_count", 4.0), ("gpu_util", 10.0))
  ]
  table = table_from_records(
    recs, shared=True, gpu_affinity_devs={"a": ["2", "3"], "b": ["0", "1"]}
  )
  rows = gpu_agg_rows_for_job_window(table)
  assert sorted({(r["host"], r["dev"]) for r in rows}) == [
    ("a", "2"),
    ("a", "3"),
    ("b", "0"),
    ("b", "1"),
  ]
  # Two hosts x two GPUs each, not len(union of "0".."3") == 4.
  assert gpu_count_total_for_job_window(table) == 4
  table_mixed = table_from_records(
    recs, shared=True, gpu_affinity_devs={"a": ["2"]}
  )
  # Host a: its one GPU; host b (no affinity row): all four.
  assert gpu_count_total_for_job_window(table_mixed) == 5
