"""Unit tests for PMC column fallbacks in legacy HeatMap (utils job)."""
import numpy as np

from hpcperfstats.analysis.metrics.metrics import _Schema
from hpcperfstats.analysis.plot.heatmap import _host_cpi_series


def test_host_cpi_series_clock_and_instructions_retired():
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "INSTRUCTIONS_RETIRED"])
  stats = np.array([[0.0, 0.0], [100.0, 50.0]], dtype=np.float64)
  cpi = _host_cpi_series(schema, stats)
  assert cpi is not None
  assert cpi.shape == (1,)
  assert abs(cpi[0] - 2.0) < 1e-9


def test_host_cpi_series_aperf_inst_retired():
  schema = _Schema(["APERF", "INST_RETIRED"])
  stats = np.array([[0.0, 0.0], [80.0, 40.0]], dtype=np.float64)
  cpi = _host_cpi_series(schema, stats)
  assert cpi is not None
  assert abs(cpi[0] - 2.0) < 1e-9


def test_host_cpi_series_mperf_inst_retired():
  schema = _Schema(["MPERF", "INST_RETIRED"])
  stats = np.array([[0.0, 0.0], [90.0, 30.0]], dtype=np.float64)
  cpi = _host_cpi_series(schema, stats)
  assert cpi is not None
  assert abs(cpi[0] - 3.0) < 1e-9
