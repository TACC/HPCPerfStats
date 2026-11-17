"""Bounded gap fill for transient sentinel gaps (Q5).

Interior gap buckets (sentinel rows, no valid row) are interpolated from the
neighbouring bucket means, bounded by ``metrics_sentinel_fill_max_gap_s`` and
``metrics_sentinel_fill_max_frac``; bridged gaps get the measured bridged rate
spread over them and do not count toward either bound. Edges, cadence-empty
buckets and peaks are never filled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.sentinel_profile import (
  SentinelConfig,
  fill_interior_gaps,
)
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import quality_note

_T0 = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)


def _step_counter(minutes, gap=None, *, cadence=1, next_arc=None):
  """host_ib rx counter; the rate steps per 5-minute bucket (1 + bucket).

  ``gap`` maps minute -> code (sentinel rows). The first valid row after a
  gap carries ``next_arc`` (None = chain break).
  """
  gap = gap or {}
  rows, cum, after_gap = [], 0.0, False
  for i in range(0, minutes + 1, cadence):
    t = _T0 + timedelta(minutes=i)
    rate = 1.0 + i // 5
    if i in gap:
      rows.append((t, "c1.x", "host_ib", "rx", "-", float(gap[i]), None, None))
      after_gap = True
      continue
    cum += rate * 60 * cadence
    arc = None if i == 0 else rate
    if after_gap:
      arc = next_arc
      after_gap = False
    rows.append((t, "c1.x", "host_ib", "rx", "-", cum, arc, None))
  return rows


def _fold(rows, config=None):
  jt = table_from_records(rows, hosts=["c1.x"])
  jt.sentinel_config = config or SentinelConfig()
  value = jt.job_arc_value("host_ib", ["rx"], 1.0)
  return value, list(jt.sentinel_sink)


def test_interior_chain_breaking_arc_gap_is_interpolated():
  """10-minute PMU_BUSY in a 4-hour job: ~4 % filled, value = gap-free."""
  oracle, _ = _fold(_step_counter(240))
  gap = dict.fromkeys(range(100, 110), -5)
  value, sink = _fold(_step_counter(240, gap))
  assert value == pytest.approx(oracle, rel=1e-12)
  assert sink[0].filled == 2
  assert "estimated (filled 4% of time)" in quality_note(sink)


def test_gauge_gap_is_interpolated():
  """A gauge (``value`` fold) gap gets the neighbouring levels."""
  rows = []
  for i in range(61):
    t = _T0 + timedelta(minutes=i)
    level = 10.0 + i // 5
    sent = 25 <= i <= 29
    rows.append(
      (
        t,
        "c1.x",
        "host_mem",
        "MemUsed",
        "-",
        -3.0 if sent else level,
        None,
        None,
      )
    )
  jt = table_from_records(rows, hosts=["c1.x"])
  jt.sentinel_config = SentinelConfig()
  got = metrics.Metrics().job_value_mean(
    jt, typename="host_mem", events=["MemUsed"]
  )
  assert got == pytest.approx(np.mean([10.0 + b for b in range(1, 13)]))
  assert jt.sentinel_sink[0].filled == 1


def test_bridged_gap_spreads_the_measured_rate_exactly():
  """READ_FAILED x2 bridged: the gap samples take the bridge rate, so the
  bucket holds the exact time-weighted mean."""
  rows = []
  for i in range(11):
    t = _T0 + timedelta(minutes=i)
    if i in (6, 7):
      rows.append((t, "c1.x", "host_ib", "rx", "-", -3.0, None, None))
      continue
    arc = None if i == 0 else (4.0 if i == 8 else 1.0)
    rows.append((t, "c1.x", "host_ib", "rx", "-", 60.0 * i, arc, None))
  value, sink = _fold(rows)
  # Bucket 1 (minutes 5-9): 1, 4, 4, 4, 1 -> 2.8; bucket 2: minute 10 -> 1.
  assert value == pytest.approx((2.8 + 1.0) / 2, rel=1e-12)
  assert sink[0].bridged == 2 and sink[0].filled == 0
  assert "bridged 2 samples" in quality_note(sink)


def test_bridged_spread_does_not_count_toward_fill_bounds():
  rows = []
  for i in range(31):
    t = _T0 + timedelta(minutes=i)
    if 10 <= i <= 14:
      rows.append((t, "c1.x", "host_ib", "rx", "-", -9.0, None, None))
      continue
    arc = None if i == 0 else 2.0
    rows.append((t, "c1.x", "host_ib", "rx", "-", 120.0 * i, arc, None))
  cfg = SentinelConfig(fill_max_frac=0.0, fill_max_gap_s=0.0)
  value, sink = _fold(rows, cfg)
  assert value == pytest.approx(2.0, rel=1e-12)
  assert sink[0].bridged == 5 and sink[0].filled == 0


def test_unknown_code_gap_is_chain_breaking_not_bridged():
  """A code this consumer does not know never bridges, even if the next
  row carries a rate: it is a gap and gets interpolated."""
  gap = dict.fromkeys(range(50, 55), -200)
  value, sink = _fold(_step_counter(120, gap, next_arc=11.0))
  oracle, _ = _fold(_step_counter(120))
  assert sink[0].bridged == 0 and sink[0].filled == 1
  # 5 sentinel samples plus the successor whose rate spans the lost gap.
  assert sink[0].transient_codes == {-200: 6}
  assert value == pytest.approx(oracle, rel=1e-12)


def test_cadence_empty_buckets_are_not_gaps():
  """Slow-tier key (one sample per 10 min): empty buckets stay absent."""
  gap = {60: -5}
  value, sink = _fold(
    _step_counter(120, gap, cadence=10, next_arc=None),
    SentinelConfig(fill_max_frac=0.5),
  )
  # The sentinel bucket and the chain-break bucket after it; the empty
  # buckets between samples are neither gaps nor filled.
  assert sink[0].filled == 2
  # Present buckets only: 12 samples with a rate or a gap (minute 0 has no
  # rate) minus the dropped first bucket.
  assert sink[0].buckets == 11
  assert np.isfinite(value)


def test_edge_gaps_are_not_filled():
  means = np.array([np.nan, 2.0, 3.0, np.nan])
  gaps = np.array([True, False, False, True])
  out, n = fill_interior_gaps(means, gaps, 3, 1.0)
  assert n == 0 and np.isnan(out[0]) and np.isnan(out[-1])


def test_over_max_frac_fills_nothing():
  means = np.array([1.0, np.nan, np.nan, 4.0, 5.0])
  gaps = np.array([False, True, True, False, False])
  out, n = fill_interior_gaps(means, gaps, 3, 0.39)
  assert n == 0 and np.isnan(out[1:3]).all()
  out, n = fill_interior_gaps(means, gaps, 3, 0.4)
  assert n == 2 and out.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_gap_longer_than_max_gap_is_not_filled():
  means = np.array([1.0, np.nan, np.nan, np.nan, np.nan, 6.0])
  gaps = np.array([False, True, True, True, True, False])
  _out, n = fill_interior_gaps(means, gaps, 3, 1.0)
  assert n == 0
  _out, n = fill_interior_gaps(means, gaps, 4, 1.0)
  assert n == 4


def test_interpolation_weights_by_bucket_distance():
  means = np.array([1.0, np.nan, 7.0])
  out, n = fill_interior_gaps(
    means, np.array([False, True, False]), 3, 1.0, np.array([0, 1, 3])
  )
  assert n == 1 and out[1] == pytest.approx(3.0)


def test_peaks_unchanged_by_gap_fill():
  """Peak reductions read stored samples: no row appears in a gap."""
  gap = dict.fromkeys(range(100, 110), -5)
  jt = table_from_records(_step_counter(240, gap), hosts=["c1.x"])
  agg = jt.index.sum_per_sample("host_ib", ["rx"], "arc")
  minutes = (agg.time_us - agg.time_us.min()) // 60_000_000 + 1
  assert not np.isin(minutes, np.arange(100, 110)).any()
  assert float(agg.sum_val.max()) == 1.0 + 240 // 5
