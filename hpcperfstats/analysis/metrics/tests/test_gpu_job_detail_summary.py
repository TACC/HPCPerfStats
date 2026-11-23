"""Unit tests for gpu_job_detail_summary (JobFrame aggregates, no DB)."""

from datetime import UTC

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


def test_host_without_gpu_count_sample_counts_its_util_devices():
  """A host whose window holds no gpu_count sample still counts its GPUs.

  ``gpu_count`` is a slow-tier key (full collects and job boundaries only);
  ``gpu_util`` is fast-tier. A large-job window thinned to time slots, or a
  missed boundary sample, can keep a host's util rows but none of its
  gpu_count rows. The count dropped that host while the util sum kept it
  (hs04: 65,200 % "out of" 64,800, one 4-GPU host short).
  """
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  ts = [datetime(2024, 1, 1, 1, m, tzinfo=UTC) for m in range(4)]
  recs = [
    (t, h, "nvidia_gpu", "gpu_util", dev, 100.0, None, None)
    for t in ts
    for h in ("a", "b")
    for dev in ("0", "1", "2", "3")
  ]
  recs += [
    (ts[0], "a", "nvidia_gpu", "gpu_count", dev, 4.0, None, None)
    for dev in ("0", "1", "2", "3")
  ]
  table = table_from_records(recs)
  active, mx, _mean = reduce_gpu_agg_to_util_stats(
    gpu_agg_rows_for_job_window(table)
  )
  count = gpu_count_total_for_job_window(table)
  assert (active, mx, count) == (8, 800.0, 8)
  assert mx <= 100.0 * count
  # Without any gpu_count row the util devices alone are the count.
  no_count = table_from_records([r for r in recs if r[3] == "gpu_util"])
  assert gpu_count_total_for_job_window(no_count) == 8
  # A gpu_count sample below the host's util devices (a 0 from a failed
  # device query) never shrinks the count under the devices the util sum
  # covers: 800 % stays out of 800, not 600.
  recs += [(ts[0], "b", "nvidia_gpu", "gpu_count", "0", 2.0, None, None)]
  assert gpu_count_total_for_job_window(table_from_records(recs)) == 8
  # A larger gpu_count (GPUs without util rows) still raises the count.
  recs += [(ts[0], "a", "nvidia_gpu", "gpu_count", "0", 6.0, None, None)]
  assert gpu_count_total_for_job_window(table_from_records(recs)) == 10


def test_gpu_count_uses_the_vendor_the_util_sum_used():
  """The count comes from the vendor whose util rows fed the util sum.

  nvidia ``gpu_count`` without usable nvidia util (blanks, or a stale type)
  plus AMD util rows: the util sum is AMD, so the count must be too (it was
  nvidia's 1 against 180 % of AMD util).
  """
  from datetime import datetime

  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  ts = [datetime(2024, 1, 1, 1, m, tzinfo=UTC) for m in range(4)]
  recs = [
    (t, "a", "amd_gpu", "gpu_util", dev, 90.0, None, None)
    for t in ts
    for dev in ("0", "1")
  ]
  recs += [(ts[0], "a", "nvidia_gpu", "gpu_count", "0", 1.0, None, None)]
  table = table_from_records(recs)
  _active, mx, _mean = reduce_gpu_agg_to_util_stats(
    gpu_agg_rows_for_job_window(table)
  )
  count = gpu_count_total_for_job_window(table)
  assert (mx, count) == (180.0, 2)
  # With no util rows at all, gpu_count alone still reports the inventory.
  only_count = table_from_records(recs[-1:])
  assert gpu_count_total_for_job_window(only_count) == 1
