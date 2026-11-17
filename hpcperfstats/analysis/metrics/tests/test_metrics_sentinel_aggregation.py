"""Metric folds with monitor value sentinels (transient vs static, Q3).

Transient gaps keep the value (bridged rates, intersection sums, aligned
ratios); static hosts are excluded (mean) or make a host sum None unless
``metrics_sentinel_scale_partial_sums``; partial-device runs scale collapsed
samples by N / (N - U) up to ``metrics_sentinel_partial_device_max_frac``
and turn them into gap buckets above it. Peaks never read the fold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
  to_time_us,
)
from hpcperfstats.analysis.metrics.lib.sentinel_profile import SentinelConfig
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import (
  SentinelRun,
  quality_codes,
  quality_note,
  researcher_visible,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  pmu_sharing_event_names,
)
from hpcperfstats.lib.monitor_sentinel import lookup

_T0 = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
_MIN = 60
_IB = ("port_xmit_data", "port_rcv_data")
_HOSTS = ("c1.x", "c2.x", "c3.x", "c4.x")


def _counter(host, typ, event, rate, *, sentinels=None, minutes=_MIN):
  """One sample per minute; ``sentinels`` maps minute -> code.

  After a gap the next valid row carries the bridged rate when every code in
  the gap is bridgeable (ingest spans it), else NULL ``arc`` (chain break).
  """
  sentinels = sentinels or {}
  rows, gap = [], []
  for i in range(minutes + 1):
    t = _T0 + timedelta(minutes=i)
    if i in sentinels:
      gap.append(sentinels[i])
      rows.append((t, host, typ, event, "-", float(sentinels[i]), None, None))
      continue
    arc = None if i == 0 else rate
    if gap and not all(lookup(c).bridge for c in gap):
      arc = None
    gap = []
    rows.append((t, host, typ, event, "-", rate * 60.0 * i, arc, None))
  return rows


def _table(rows, *, runs=(), hosts=_HOSTS, config=None):
  jt = table_from_records(rows, hosts=list(hosts), sentinel_runs=list(runs))
  jt.sentinel_config = config or SentinelConfig()
  return jt


def _ib_job(rate=10.0, *, overrides=None, runs=(), config=None):
  """IB rx+tx on every host; ``overrides`` maps (host, event) -> sentinels."""
  overrides = overrides or {}
  rows = []
  for h in _HOSTS:
    for ev in _IB:
      rows += _counter(h, "host_ib", ev, rate, sentinels=overrides.get((h, ev)))
  return _table(rows, runs=runs, config=config)


def _fold(jt, *, agg="mean", typ="host_ib", events=_IB):
  jt.sentinel_sink.clear()
  value = jt.job_arc_value(typ, list(events), 1.0, host_aggregate=agg)
  return value, list(jt.sentinel_sink)


def _partial(host, code, n_dev, n_tot=64, *, start_min=0, end_min=_MIN):
  """A partial-device run for both IB events of one host."""
  return [
    SentinelRun(
      host,
      "host_ib",
      "",
      ev,
      code,
      to_time_us(_T0 + timedelta(minutes=start_min)),
      to_time_us(_T0 + timedelta(minutes=end_min)),
      n_dev,
      n_tot,
    )
    for ev in _IB
  ]


def test_sentinel_free_fold_is_the_legacy_fold():
  """No sentinel rows and no runs: the sentinel path does not engage."""
  jt = _ib_job()
  assert jt.sentinel_fold_value("host_ib", list(_IB), "arc", 1.0) == (
    False,
    None,
  )
  value, sink = _fold(jt)
  assert value == pytest.approx(20.0)
  assert sink == []


def test_bridged_transient_gap_keeps_the_mean_and_notes_bridged():
  """READ_FAILED for 2 ticks mid-job: bridged, coverage 100 %, same mean."""
  gap = {20: -3, 21: -3}
  jt = _ib_job(overrides={("c1.x", "port_rcv_data"): gap})
  value, sink = _fold(jt)
  assert value == pytest.approx(20.0, rel=1e-12)
  assert sink[0].bridged == 2 and sink[0].covered == sink[0].samples
  assert "bridged 2 samples" in quality_note(sink)
  assert not researcher_visible(quality_codes(sink))


def test_chain_breaking_transient_gap_mean_unchanged():
  """PMU_BUSY-style gap (chain breaks): value from the covered samples."""
  gap = {30: -5, 31: -5, 32: -5}
  jt = _ib_job(overrides={("c2.x", ev): gap for ev in _IB})
  value, sink = _fold(jt)
  assert value == pytest.approx(20.0, rel=1e-12)
  # 3 sentinel samples plus the rate-less sample after the chain break.
  assert sink[0].transient_codes == {-5: 4}
  assert sink[0].covered < sink[0].samples


def test_multi_event_sum_uses_the_intersection():
  """rx valid while tx is a sentinel: that sample is dropped, no half sum."""
  rows = []
  for h in _HOSTS:
    rows += _counter(h, "host_ib", "port_rcv_data", 10.0)
    gap = {20: -6, 21: -6, 22: -6} if h == "c1.x" else None
    rows += _counter(h, "host_ib", "port_xmit_data", 30.0, sentinels=gap)
  jt = _table(rows)
  value, _sink = _fold(jt)
  assert value == pytest.approx(40.0, rel=1e-12)


def test_derived_ratio_numerator_and_denominator_aligned():
  """A running-time gap removes the same samples from the enabled side."""
  enabled_ev, running_ev, _solo = pmu_sharing_event_names()
  rows = []
  gap = {10: -6, 11: -6, 12: -6}
  for h in _HOSTS:
    rows += _counter(h, "host_cpu_hw", running_ev, 1.0, sentinels=gap)
    en = _counter(h, "host_cpu_hw", enabled_ev, 2.0)
    # The enabled counter runs hot exactly while running is unreadable.
    en = [
      (
        t,
        hh,
        ty,
        ev,
        d,
        v,
        100.0 if 10 <= (t - _T0).seconds // 60 <= 12 else a,
        dl,
      )
      for (t, hh, ty, ev, d, v, a, dl) in en
    ]
    rows += en
  jt = _table(rows)
  pct = metrics.Metrics()._job_pmu_sharing_pct(jt, "avg_pmu_running_pct")
  assert pct == pytest.approx(50.0, rel=1e-12)


def test_static_host_excluded_from_mean_with_host_note():
  """1 of 4 hosts NOT_PRESENT: mean of 3 hosts, note "excluded hosts 1/4"."""
  rows = []
  for h, rate in zip(_HOSTS, (10.0, 20.0, 30.0, 99.0), strict=True):
    gap = dict.fromkeys(range(_MIN + 1), -2) if h == "c4.x" else None
    for ev in _IB:
      rows += _counter(h, "host_ib", ev, rate, sentinels=gap)
  value, sink = _fold(_table(rows))
  assert value == pytest.approx(40.0, rel=1e-12)
  assert sink[0].excluded == {"c4.x": -2}
  note = quality_note(sink)
  assert "excluded hosts 1/4 (counter not present)" in note
  assert researcher_visible(quality_codes(sink))


def _static_c4(config=None):
  gap = dict.fromkeys(range(_MIN + 1), -2)
  return _ib_job(overrides={("c4.x", ev): gap for ev in _IB}, config=config)


def test_sum_with_static_host_is_none_by_default():
  value, sink = _fold(_static_c4(), agg="sum")
  assert value is None
  assert sink[0].none_reason == "sum_static"


def test_sum_with_static_host_scaled_when_knob_on():
  jt = _static_c4(SentinelConfig(scale_partial_sums=True))
  value, sink = _fold(jt, agg="sum")
  assert value == pytest.approx(3 * 20.0 * 4 / 3, rel=1e-12)
  assert "estimated: 3/4 hosts, scaled" in quality_note(sink)
  assert not researcher_visible(quality_codes(sink))


def test_all_hosts_static_is_none_with_histogram_reason():
  gap = dict.fromkeys(range(_MIN + 1), -2)
  jt = _ib_job(overrides={(h, ev): gap for h in _HOSTS for ev in _IB})
  value, sink = _fold(jt)
  assert value is None and sink[0].none_reason == "all_static"
  fields = metrics._metric_reason_fields(
    jt, None, ["host_ib"], list(_IB), "legacy", True
  )
  assert fields["no_data_reason"] == "host_ib: counter not present (4/4 hosts)"
  assert fields["no_data_codes"] == {"codes": {"-2": 4}}


def test_transient_on_many_hosts_under_floor_in_aggregate_is_none():
  """Each host loses 45 % (transient), one host is static: job coverage
  falls under the 0.5 floor, so the value is None, not built of fragments."""
  heavy = dict.fromkeys(range(5, 32), -5)
  over = {("c1.x", ev): heavy for ev in _IB}
  over |= {("c2.x", ev): heavy for ev in _IB}
  over |= {("c3.x", ev): heavy for ev in _IB}
  over |= {("c4.x", ev): dict.fromkeys(range(_MIN + 1), -6) for ev in _IB}
  value, sink = _fold(_ib_job(overrides=over))
  assert value is None and sink[0].none_reason == "under_floor"


def test_partial_devices_3_of_64_scaled_64_over_61():
  """Stored sums cover 61 cores; the fold scales them x64/61 for staff."""
  jt = _ib_job(10.0 * 61 / 64, runs=_partial("c1.x", -3, 3))
  value, sink = _fold(jt)
  # c1 restored to 20.0; the other hosts carry the same stored value.
  stored = 2 * 10.0 * 61 / 64
  assert value == pytest.approx((20.0 + 3 * stored) / 4, rel=1e-12)
  assert sink[0].partial_devices == {-3: [3, 64]}
  note = quality_note(sink)
  assert "estimated: 3/64 devices unreadable (read failed), scaled" in note
  assert not researcher_visible(quality_codes(sink))


def test_partial_devices_19_of_64_scaled_under_the_cap():
  jt = _ib_job(45.0, runs=[r for h in _HOSTS for r in _partial(h, -3, 19)])
  value, _sink = _fold(jt)
  assert value == pytest.approx(2 * 45.0 * 64 / 45, rel=1e-12)


def test_partial_devices_20_of_64_is_a_gap_bucket_not_scaled():
  """Over the 0.30 cap the samples are gaps; the interior bucket is filled
  from its neighbors instead of being scaled or averaged in low."""
  rows = []
  for h in _HOSTS:
    for ev in _IB:
      for row in _counter(h, "host_ib", ev, 10.0):
        t = row[0]
        hot = h == "c1.x" and 20 <= (t - _T0).seconds // 60 <= 24
        rows.append((*row[:6], 10.0 * 44 / 64 if hot else row[6], row[7]))
  runs = _partial("c1.x", -3, 20, start_min=20, end_min=24)
  value, sink = _fold(_table(rows, runs=runs))
  assert value == pytest.approx(20.0, rel=1e-12)
  assert sink[0].partial_devices == {}
  assert sink[0].filled == 1
  assert "estimated (filled" in quality_note(sink)


def test_concurrent_codes_add_up_for_the_scale_factor():
  """2 READ_FAILED + 1 NOT_PRESENT on 64 cores: x64/61, note lists both."""
  runs = _partial("c1.x", -3, 2) + _partial("c1.x", -2, 1)
  jt = _ib_job(10.0 * 61 / 64, runs=runs)
  _value, sink = _fold(jt)
  assert sink[0].partial_devices == {-3: [2, 64], -2: [1, 64]}
  note = quality_note(sink)
  assert (
    "estimated: 3/64 devices unreadable (read failed 2, counter not present 1)"
    in note
  )


def test_overlapping_duplicate_runs_are_unioned():
  """Archive and live ingest can race; the same run twice counts once."""
  runs = _partial("c1.x", -3, 3) + _partial("c1.x", -3, 3, start_min=10)
  jt = _ib_job(10.0 * 61 / 64, runs=runs)
  _value, sink = _fold(jt)
  assert sink[0].partial_devices == {-3: [3, 64]}


def test_peaks_never_scaled_by_partial_device_runs():
  """Peak reductions read the stored samples; runs do not touch them."""
  jt = _ib_job(10.0 * 61 / 64, runs=_partial("c1.x", -3, 3))
  agg = jt.index.sum_per_sample("host_ib", list(_IB), "arc")
  assert float(agg.sum_val.max()) == pytest.approx(2 * 10.0 * 61 / 64)


def test_researcher_sees_partial_note_only_for_visible_codes():
  only_absent = quality_codes(
    _fold(_ib_job(10.0, runs=_partial("c1.x", -2, 3)))[1]
  )
  assert researcher_visible(only_absent)
  mixed = quality_codes(
    _fold(
      _ib_job(10.0, runs=_partial("c1.x", -2, 1) + _partial("c1.x", -7, 1))
    )[1]
  )
  assert not researcher_visible(mixed)


def test_summary_falls_back_when_preferred_series_is_all_sentinel():
  """An all ``-2`` ``host_mem_job`` / ``gpu_util`` series is no data, so the
  Summary uses ``host_mem`` / ``utilization`` instead of plotting 0."""
  from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
    build_summary_series,
  )

  rows = []
  for i in range(3):
    t = _T0 + timedelta(minutes=i)
    rows += [
      (t, "c1.x", "host_cpu", "user", "", 1.0, 1.0, 1.0),
      (t, "c1.x", "host_mem_job", "mem_used", "j:1", -2.0, None, None),
      (
        t,
        "c1.x",
        "host_mem",
        "mem_used",
        "0",
        1024.0 * 1024 * (i + 1),
        None,
        None,
      ),
      (t, "c1.x", "nvidia_gpu", "gpu_util", "0", -2.0, None, None),
      (t, "c1.x", "nvidia_gpu", "utilization", "0", 40.0 + i, None, None),
    ]
  cols = build_summary_series(_table(rows, hosts=("c1.x",))).frame.columns
  assert cols["mem"].tolist() == [1.0, 2.0, 3.0]
  assert cols["nv_gpu_util"].tolist() == [40.0, 41.0, 42.0]


def _host_series(agg, jt, host):
  """``{minute: value}`` of one host in an aggregate table."""
  hid = jt.frame.hosts.index(host)
  sel = agg.host_id == hid
  t0 = to_time_us(_T0)
  return dict(
    zip(
      ((agg.time_us[sel] - t0) // 60_000_000).tolist(),
      agg.sum_val[sel].tolist(),
      strict=True,
    )
  )


def test_get_aggregate_fills_interior_transient_gap():
  """Series readers interpolate interior gaps within ``fill_max_gap_s`` and
  ``fill_max_frac``; edge, over-long and over-share gaps stay missing."""
  jt = _ib_job(
    overrides={
      # A -5 at minute 20 breaks the chain: minutes 20 and 21 are gaps.
      ("c1.x", "port_rcv_data"): {20: -5},
      ("c2.x", "port_rcv_data"): {_MIN: -5},
      ("c3.x", "port_rcv_data"): dict.fromkeys(range(10, 31), -5),
      ("c4.x", "port_rcv_data"): dict.fromkeys((5, 15, 25, 35, 45), -5),
    }
  )
  agg = jt.get_aggregate("host_ib", "arc", list(_IB), 1.0)
  c1 = _host_series(agg, jt, "c1.x")
  assert c1[20] == pytest.approx(20.0) and c1[21] == pytest.approx(20.0)
  assert agg.filled == 2
  assert _MIN not in _host_series(agg, jt, "c2.x")
  c3 = _host_series(agg, jt, "c3.x")
  assert not any(m in c3 for m in range(10, 32))
  c4 = _host_series(agg, jt, "c4.x")
  assert not any(m in c4 for m in (5, 6, 15, 16, 25, 26, 35, 36, 45, 46))
  assert all(v == pytest.approx(20.0) for m, v in c1.items() if m)


def test_peak_never_uses_filled_sample():
  """Peaks read measured samples only: a gap sample is neither filled nor a
  partial sum, so it cannot raise the job-wide maximum."""
  from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
    peak_arc_sum,
  )

  rows = []
  for i in range(_MIN + 1):
    t = _T0 + timedelta(minutes=i)
    a_rate = 100.0 if i == 20 else 1.0
    rows.append((t, "c1.x", "host_ib", "port_rcv_data", "-", 1.0, a_rate, None))
    for ev in _IB:
      gap = ev == "port_xmit_data" and i in (20, 21)
      code = -5.0 if gap and i == 20 else 1.0
      arc = None if gap else 25.0
      rows.append((t, "c2.x", "host_ib", ev, "-", code, arc, None))
  jt = _table(rows, hosts=("c1.x", "c2.x"))
  assert peak_arc_sum(jt, "host_ib", _IB) == pytest.approx(100.0)
  assert jt.get_aggregate("host_ib", "arc", list(_IB), 1.0).filled == 2


def test_avg_cpuusage_partial_busy_sample():
  """One unreadable busy (``nice``) or idle (``idle``) event makes the sample
  a gap on both sides: no understated or overstated busy share."""
  from types import SimpleNamespace

  rates = {
    "user": 50.0,
    "system": 0.0,
    "nice": 50.0,
    "idle": 100.0,
    "iowait": 0.0,
    "irq": 0.0,
    "softirq": 0.0,
  }
  gaps = {"nice": {20: -5}, "idle": {40: -5}}
  rows = []
  for ev, rate in rates.items():
    rows += _counter("c1.x", "host_cpu", ev, rate, sentinels=gaps.get(ev))
  jt = _table(rows, hosts=("c1.x",))
  job = SimpleNamespace(ncores=8, nhosts=1)
  value = metrics.Metrics()._job_avg_cpuusage_allocated(jt, job, None, None)
  assert value == pytest.approx(4.0, rel=1e-12)


def test_type_detail_series_fills_interior_gap():
  """Type Detail keeps an event whose only gap is a short interior one."""
  from hpcperfstats.analysis.metrics.lib.columnar.type_detail_series import (
    build_type_detail_series,
  )

  jt = _ib_job(overrides={("c1.x", "port_rcv_data"): {20: -5}})
  series = build_type_detail_series(
    jt, "host_ib", [("port_rcv_data", "B/s")], type_list=["host_ib"]
  )
  vals = series.frame.columns["port_rcv_data"]
  assert not any(v != v for v in vals.tolist())
