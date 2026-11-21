"""
Per-host min/max plot decimation (``gen.utils.decimate_minmax``) and its
use by the Summary and Type Detail builders.
"""

from __future__ import annotations

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.gen.utils import (
  PLOT_MAX_POINTS_PER_HOST,
  decimate_minmax,
)


def _bucket_ids(times: np.ndarray, max_points: int) -> np.ndarray:
  """Independent time-bucket oracle matching the documented split."""
  n_buckets = max_points // 3
  t = times.astype(np.float64)
  span = t.max() - t.min()
  b = ((t - t.min()) * (n_buckets / span)).astype(np.int64)
  return np.minimum(b, n_buckets - 1)


def test_decimate_minmax_under_limit_is_identity():
  times = np.arange(10, dtype=np.int64) * 60_000_000
  values = np.array([3, 1, np.nan, 4, 1, 5, 9, 2, 6, 5], dtype=np.float64)
  for limit in (10, 11, PLOT_MAX_POINTS_PER_HOST):
    idx = decimate_minmax(times, values, limit)
    assert idx.tolist() == list(range(10))
    np.testing.assert_array_equal(values[idx], values)


def test_decimate_minmax_empty_series():
  assert decimate_minmax([], [], 3).tolist() == []


def test_decimate_minmax_point_count_bound_and_time_order():
  rng = np.random.default_rng(7)
  n = 16_000
  times = np.cumsum(rng.integers(1, 120, size=n)).astype(np.int64)
  values = rng.normal(size=n)
  for limit in (3, 4, 100, 2000, 2001):
    idx = decimate_minmax(times, values, limit)
    assert 0 < idx.size <= limit
    assert np.all(np.diff(idx) > 0)
    assert np.all(np.diff(times[idx]) >= 0)


def test_decimate_minmax_keeps_bucket_min_max_and_global_extremes():
  rng = np.random.default_rng(11)
  n = 5000
  limit = 300
  times = np.arange(n, dtype=np.int64) * 1_000_000
  values = rng.normal(size=n)
  values[1234] = 50.0
  values[4321] = -50.0
  idx = decimate_minmax(times, values, limit)
  assert 1234 in idx
  assert 4321 in idx
  buckets = _bucket_ids(times, limit)
  kept = set(idx.tolist())
  for b in np.unique(buckets):
    members = np.flatnonzero(buckets == b)
    seg = values[members]
    assert int(members[np.argmin(seg)]) in kept
    assert int(members[np.argmax(seg)]) in kept
    assert len(kept.intersection(members.tolist())) == 2
  # Plotted envelope equals the full-resolution envelope per bucket.
  assert values[idx].max() == values.max()
  assert values[idx].min() == values.min()


def test_decimate_minmax_nan_gap_stays_a_gap():
  n = 3000
  limit = 30
  times = np.arange(n, dtype=np.int64)
  values = np.sin(np.arange(n) / 50.0)
  values[1000:1600] = np.nan
  values[2500] = np.nan
  idx = decimate_minmax(times, values, limit)
  kept_values = values[idx]
  buckets = _bucket_ids(times, limit)
  for b in np.unique(buckets):
    members = np.flatnonzero(buckets == b)
    nan_members = members[np.isnan(values[members])]
    kept_in_bucket = np.intersect1d(idx, members)
    if nan_members.size == members.size:
      # All-NaN bucket: exactly one NaN so the line breaks there.
      assert kept_in_bucket.tolist() == [int(nan_members[0])]
    elif nan_members.size:
      # Mixed bucket: min, max and its first NaN.
      assert int(nan_members[0]) in kept_in_bucket
      assert kept_in_bucket.size == 3
    else:
      assert not np.isnan(values[kept_in_bucket]).any()
  # The single-sample gap at 2500 still separates finite points.
  pos = int(np.flatnonzero(idx == 2500)[0])
  assert np.isfinite(kept_values[pos - 1])
  assert np.isfinite(kept_values[pos + 1])


def test_decimate_minmax_all_nan_series():
  idx = decimate_minmax(np.arange(100), np.full(100, np.nan), 9)
  assert idx.size == 3
  assert np.isnan(np.full(100, np.nan)[idx]).all()


def test_decimate_minmax_datetime64_and_constant_time():
  times = np.datetime64("2026-11-21T00:00:00", "ms") + np.arange(1000).astype(
    "timedelta64[s]"
  )
  values = np.arange(1000, dtype=np.float64)
  idx = decimate_minmax(times, values, 30)
  assert idx.size <= 30
  assert idx[0] == 0
  assert idx[-1] == 999
  same = decimate_minmax(np.zeros(1000, dtype=np.int64), values, 30)
  assert same.size <= 30
  assert same[0] == 0 and same[-1] == 999


def test_decimate_minmax_rejects_bad_input():
  with pytest.raises(ValueError):
    decimate_minmax([0, 1, 2, 3], [1.0, 2.0, 3.0, 4.0], 2)
  with pytest.raises(ValueError):
    decimate_minmax([0, 1], [1.0, 2.0, 3.0], 3)


def _summary_frame(n_hosts: int, n_samples: int):
  """SeriesFrame with one ``cpu`` column, ``n_hosts x n_samples`` rows."""
  from hpcperfstats.analysis.metrics.lib.columnar.series_frame import (
    SeriesFrame,
  )

  hosts = [f"c{i:03d}" for i in range(n_hosts)]
  host_id = np.repeat(np.arange(n_hosts), n_samples)
  time_us = np.tile(
    1_700_000_000_000_000 + np.arange(n_samples, dtype=np.int64) * 60_000_000,
    n_hosts,
  )
  frame = SeriesFrame.from_base(host_id, time_us, hosts)
  rng = np.random.default_rng(3)
  values = rng.random(frame.time_us.size) * 100.0
  values[5] = np.nan
  frame.set("cpu", values)
  return hosts, frame


class _Jt:
  """Minimal job table stand-in for ``SummaryPlot.plot_metric``."""

  def __init__(self, hosts: list[str]) -> None:
    self.jid = "1"
    self.host_list = hosts


def _summary_sources(n_hosts: int, n_samples: int):
  from bokeh.models import ColumnDataSource

  from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot

  hosts, frame = _summary_frame(n_hosts, n_samples)
  sp = SummaryPlot(_Jt(hosts))
  sp.hc = dict.fromkeys(hosts, "#000000")
  fig = sp.plot_metric(frame, "cpu", "CPU")
  sources = [
    r.data_source
    for r in fig.renderers
    if isinstance(getattr(r, "data_source", None), ColumnDataSource)
  ]
  line = next(s for s in sources if "xs" in s.data)
  scatter = next(s for s in sources if "cpu" in s.data)
  return frame, line, scatter


def test_summary_plot_metric_under_limit_keeps_every_sample():
  _frame, line, scatter = _summary_sources(3, 50)
  assert [len(x) for x in line.data["ys"]] == [50, 50, 50]
  assert isinstance(line.data["ys"][0], np.ndarray)
  assert len(scatter.data["cpu"]) == 3 * 50 - 1
  assert len(scatter.data["cpu_plain"]) == len(scatter.data["cpu"])
  assert len(scatter.data["_hover_time"]) == len(scatter.data["cpu"])
  assert np.isnan(line.data["ys"][0]).sum() == 1


def test_summary_plot_metric_decimates_each_host_and_aligns_hover():
  n_samples = 3 * PLOT_MAX_POINTS_PER_HOST
  frame, line, scatter = _summary_sources(2, n_samples)
  for xs, ys in zip(line.data["xs"], line.data["ys"], strict=True):
    assert 0 < len(ys) <= PLOT_MAX_POINTS_PER_HOST
    assert len(xs) == len(ys)
    assert np.all(np.diff(xs.astype(np.int64)) > 0)
  n = len(scatter.data["cpu"])
  assert n <= 2 * PLOT_MAX_POINTS_PER_HOST
  assert len(scatter.data["host"]) == n
  assert len(scatter.data["time"]) == n
  assert scatter.data["cpu_plain"] == [f"{v:,.2f}" for v in scatter.data["cpu"]]
  values = frame.columns["cpu"]
  assert np.nanmax(scatter.data["cpu"]) == np.nanmax(values)
  assert np.nanmin(scatter.data["cpu"]) == np.nanmin(values)


def test_devplot_decimates_each_host_and_aligns_hover():
  from bokeh.models import ColumnDataSource

  from hpcperfstats.analysis.metrics.lib.plot.devplot import DevPlot

  hosts, frame = _summary_frame(2, 3 * PLOT_MAX_POINTS_PER_HOST)

  class _Series:
    type_name = "cpu"

  series = _Series()
  series.frame = frame
  dp = DevPlot(series, hosts)
  dp.hc = dict.fromkeys(hosts, "#000000")
  fig = dp.plot_metric("cpu", "%")
  sources = {
    id(r.data_source): r.data_source
    for r in fig.renderers
    if isinstance(getattr(r, "data_source", None), ColumnDataSource)
    and "cpu" in r.data_source.data
  }
  assert len(sources) == 2
  for src in sources.values():
    n = len(src.data["cpu"])
    assert 0 < n <= PLOT_MAX_POINTS_PER_HOST
    assert len(src.data["time"]) == n
    assert len(src.data["cpu_plain"]) == n
    assert len(set(src.data["host"])) == 1
