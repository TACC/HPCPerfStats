"""Tests for safe per-interval rates used by metrics (duplicate sample times)."""

import os
import warnings

import numpy as np

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from hpcperfstats.django_bootstrap import ensure_django

ensure_django()

from hpcperfstats.analysis.gen.utils import utils as job_utils
from hpcperfstats.analysis.metrics import metrics


def test_per_interval_rate_nan_when_zero_dt():
  t = np.array([0.0, 0.0, 1.0, 2.0], dtype=np.float64)
  y = np.array([0.0, 0.0, 10.0, 30.0], dtype=np.float64)
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    r = metrics._per_interval_rate(y, t)
  assert np.isnan(r[0])
  np.testing.assert_allclose(r[1:], [10.0, 20.0])


def test_node_imbalance_and_time_imbalance_no_runtime_warning():
  """Duplicate timestamps on the job time axis must not emit divide-by-zero warnings."""
  schema = metrics._Schema(["user", "system"])
  # Six samples; first two share the same time -> diff(t)[0] == 0.
  # time_imbalance only enters its inner loop when len(t) >= 6.
  cpu = np.array(
      [
          [0.0, 0.0],
          [1.0, 0.0],
          [4.0, 0.0],
          [9.0, 0.0],
          [16.0, 0.0],
          [25.0, 0.0],
      ],
      dtype=np.float64,
  )

  class _Host:
    def __init__(self):
      self.stats = {"cpu": {"agg": cpu}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"cpu": schema}
      self.times = np.array([0.0, 0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    metrics.node_imbalance().compute_metric(u)
    metrics.time_imbalance().compute_metric(u)
