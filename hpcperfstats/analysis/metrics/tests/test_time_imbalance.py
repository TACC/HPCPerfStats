"""Regression tests for O(n) time_imbalance (parity vs historical O(n^2) trapz loop)."""

import os
import time
import warnings

import numpy as np

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

ensure_django()

from hpcperfstats.analysis.metrics.lib.gen.utils import utils as job_utils
from hpcperfstats.analysis.metrics.lib import metrics

try:
  from numpy import trapezoid as _trapz
except ImportError:  # pragma: no cover - older numpy
  from numpy import trapz as _trapz


def _time_imbalance_min_ratio_for_rate_quadratic(rate, tmid):
  """Historical O(n^2) reference: per-split trapz before/after (test-only)."""
  rate = np.asarray(rate, dtype=np.float64)
  tmid = np.asarray(tmid, dtype=np.float64)
  n = int(rate.shape[0])
  if n < 4 or tmid.shape[0] != n:
    return None
  # i in {2 .. nt-3} with nt = n+1 => range(2, n-1)
  min_ratio = None
  for i in range(2, n - 1):
    r1 = range(i)
    r2 = range(i, n)
    before_window = float(tmid[i] - tmid[0])
    after_window = float(tmid[-1] - tmid[i])
    if before_window <= 0 or after_window <= 0:
      continue
    a = float(_trapz(rate[r1], tmid[r1])) / before_window
    if not (a > 0) or not np.isfinite(a):
      continue
    b = float(_trapz(rate[r2], tmid[r2])) / after_window
    if not np.isfinite(b):
      continue
    ratio = b / a
    if not np.isfinite(ratio) or ratio < 0:
      continue
    if ratio > metrics._TIME_IMBALANCE_MAX_SLICE_RATIO:
      continue
    if min_ratio is None or ratio < min_ratio:
      min_ratio = ratio
  return min_ratio


def _synthetic_cpu_job(nt, nhosts=1, phase_drop_at=None):
  """Build a job_utils with cumulative user CPU; optional mid-job rate drop."""
  schema = metrics._Schema(["user", "system"])
  t = np.arange(nt, dtype=np.float64)
  hosts = {}
  for h in range(nhosts):
    # Cumulative user jiffies ~ integral of rate; rate=1 then optional drop to 0.1.
    user = np.zeros(nt, dtype=np.float64)
    rate = 1.0
    for i in range(1, nt):
      if phase_drop_at is not None and i >= phase_drop_at:
        rate = 0.1
      user[i] = user[i - 1] + rate * (t[i] - t[i - 1])
    cpu = np.column_stack([user, np.zeros(nt, dtype=np.float64)])

    class _Host:
      def __init__(self, arr):
        self.stats = {"cpu": {"agg": arr}}

    hosts[f"n{h}"] = _Host(cpu)

  class _Job:
    def __init__(self):
      self.hosts = hosts
      self.schemas = {"cpu": schema}
      self.times = t
      self.acct = {"cores": 1, "nodes": nhosts}

  return job_utils(_Job())


def test_time_imbalance_prefix_matches_quadratic_reference():
  """O(n) helper must match historical trapz loop on small fixtures."""
  rng = np.random.default_rng(42)
  for nt in (6, 12, 40, 80):
    t = np.arange(nt, dtype=np.float64)
    tmid = (t[:-1] + t[1:]) / 2.0
    rate = np.maximum(rng.random(nt - 1), 0.01)
    got = metrics._time_imbalance_min_ratio_for_rate(rate, tmid)
    ref = _time_imbalance_min_ratio_for_rate_quadratic(rate, tmid)
    if ref is None:
      assert got is None
    else:
      assert got is not None
      np.testing.assert_allclose(got, ref, rtol=1e-12, atol=1e-12)


def test_time_imbalance_phase_drop_detects_imbalance():
  """Job that drops CPU rate mid-timeline should report time imbalance < 100%."""
  u = _synthetic_cpu_job(nt=20, nhosts=2, phase_drop_at=10)
  value, typename, units = metrics.time_imbalance().compute_metric(u)
  assert typename == "cpu"
  assert units == "%"
  assert value is not None
  assert value < 50.0


def test_time_imbalance_short_timeline_returns_none():
  # Inner split loop needs len(t) >= 5 (i starts at 2); nt < 5 yields no slices.
  u = _synthetic_cpu_job(nt=4, nhosts=1)
  value, typename, units = metrics.time_imbalance().compute_metric(u)
  assert value is None
  assert typename == "cpu"
  assert units == "%"


def test_time_imbalance_large_nt_completes_under_budget():
  """Regression for MetricsComputeJobTimeoutError on long timelines (O(n^2) path)."""
  # nt=800 was enough to make the old nested trapz path dominate wall clock;
  # O(n) must finish well under 2s on a single host.
  u = _synthetic_cpu_job(nt=800, nhosts=1, phase_drop_at=400)
  t0 = time.perf_counter()
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    value, typename, units = metrics.time_imbalance().compute_metric(u)
  elapsed = time.perf_counter() - t0
  assert typename == "cpu"
  assert units == "%"
  assert value is not None
  assert np.isfinite(value)
  assert elapsed < 2.0, f"time_imbalance took {elapsed:.3f}s (expected O(n) < 2s)"
