"""SentinelProfile / classify_series: clean, transient and static series.

A host series is clean without sentinel samples, transient when its covered
share (valid plus bridged samples) is at least ``metrics_sentinel_min_coverage``,
and static below the floor or when a static-kind code (counter not present,
not built, permission denied) is all it has.
"""

from __future__ import annotations

import pytest

from hpcperfstats.analysis.metrics.lib.sentinel_profile import (
  CLEAN,
  STATIC,
  TRANSIENT,
  SentinelConfig,
  SentinelProfile,
  classify_series,
)


def test_no_sentinel_samples_is_clean():
  assert classify_series(SentinelProfile(10, 0, 0, {}), 0.5) == CLEAN


@pytest.mark.parametrize(
  ("valid", "gap", "expected"),
  [(5, 5, TRANSIENT), (49, 51, STATIC), (50, 50, TRANSIENT), (1, 0, CLEAN)],
)
def test_coverage_floor_boundary(valid, gap, expected):
  """Exactly at the floor is transient; just under it is static."""
  prof = SentinelProfile(valid, 0, gap, {-3: gap} if gap else {})
  assert classify_series(prof, 0.5) == expected


def test_bridged_samples_count_as_covered():
  """Bridged samples recover the activity: 2 valid + 8 bridged = 100 %."""
  prof = SentinelProfile(2, 8, 0, {-3: 8})
  assert prof.valid_frac == 1.0
  assert classify_series(prof, 0.5) == TRANSIENT


@pytest.mark.parametrize("code", [-2, -4, -10])
def test_static_kind_code_without_valid_samples_is_static(code):
  """A static-kind code with no covered sample is static even at floor 0."""
  assert classify_series(SentinelProfile(0, 0, 3, {code: 3}), 0.0) == STATIC


def test_transient_code_without_valid_samples_static_only_by_floor():
  """READ_FAILED alone is static through coverage, not through its kind."""
  prof = SentinelProfile(0, 0, 3, {-3: 3})
  assert classify_series(prof, 0.0) == TRANSIENT
  assert classify_series(prof, 0.5) == STATIC


def test_static_kind_code_with_valid_samples_is_transient():
  prof = SentinelProfile(8, 0, 2, {-2: 2})
  assert classify_series(prof, 0.5) == TRANSIENT


def test_config_defaults_match_ini_registry():
  """The fold defaults and the INI registry defaults are the same values."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  d = SentinelConfig()
  for key, want in (
    ("metrics_sentinel_min_coverage", d.min_coverage),
    ("metrics_sentinel_partial_device_max_frac", d.partial_device_max_frac),
    ("metrics_sentinel_fill_max_gap_s", d.fill_max_gap_s),
    ("metrics_sentinel_fill_max_frac", d.fill_max_frac),
  ):
    assert float(cfg.INI_OPTION_DEFAULTS[key]) == want
  assert cfg.INI_OPTION_DEFAULTS["metrics_sentinel_scale_partial_sums"] == "no"
  assert d.scale_partial_sums is False
