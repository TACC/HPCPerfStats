"""Unit tests for analysis.gen.utils (plot value / time formatting helpers)."""

import numpy as np


def test_non_degenerate_y_range_for_series_handles_nan_and_negatives():
  from hpcperfstats.analysis.metrics.lib.gen.utils import (
    non_degenerate_y_range_for_series,
  )

  assert non_degenerate_y_range_for_series([1.0, 3.0]) == (
    0.0,
    3.3000000000000003,
  )
  assert non_degenerate_y_range_for_series([np.nan, np.nan]) == (0.0, 1.0)
  lo, hi = non_degenerate_y_range_for_series([-2.0, 4.0])
  assert lo == -2.0 and hi > 4.0
  assert non_degenerate_y_range_for_series([5.0], y_range_end=2.0) == (0.0, 2.0)


def test_tz_aware_bokeh_tick_formatter_returns_datetime_formatter():
  """tz_aware_bokeh_tick_formatter uses built-in DatetimeTickFormatter (no CustomJS)."""
  from bokeh.models import DatetimeTickFormatter

  from hpcperfstats.analysis.metrics.lib.gen.utils import (
    tz_aware_bokeh_tick_formatter,
  )

  formatter = tz_aware_bokeh_tick_formatter()
  assert isinstance(formatter, DatetimeTickFormatter)


def test_timestamps_as_cluster_naive_matches_hover_wall_clock(monkeypatch):
  """Axis-bound naive times share wall clock with format_cluster_hover_datetime."""
  from datetime import UTC, datetime

  from hpcperfstats.analysis.metrics.lib.gen import utils as u

  monkeypatch.setattr(u, "local_timezone", "America/Chicago")
  utc = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
  micros = np.array([int(utc.timestamp() * 1_000_000)], dtype=np.int64)
  naive = u.timestamps_as_cluster_naive(micros)
  # 12:00 UTC -> 07:00 CDT in July
  assert naive.dtype == np.dtype("datetime64[ms]")
  assert naive[0].astype("datetime64[h]").astype(int) % 24 == 7
  assert u.format_cluster_hover_datetime(naive[0]) == "7:00 AM"
  assert u.format_cluster_hover_datetime(utc) == "7:00 AM"
  assert u.format_cluster_hover_datetime(utc.timestamp() * 1000.0) == "7:00 AM"
  assert u.format_cluster_hover_datetime(None) == ""


def test_format_plain_decimal_avoids_scientific():
  from hpcperfstats.analysis.metrics.lib.gen.utils import format_plain_decimal

  assert format_plain_decimal(1234567.8) == "1,234,567.80"
  assert format_plain_decimal(0.00012) == "0.00"


def test_hover_plain_columns_adds_formatted_fields():
  from hpcperfstats.analysis.metrics.lib.gen import utils as u

  times = u.timestamps_as_cluster_naive(
    np.array([1_704_112_200_000_000], dtype=np.int64)
  )
  out = u.hover_plain_columns(times, {"cpu": [1.5]})
  assert "_hover_time" in out
  assert out["cpu_plain"] == ["1.50"]
  assert u.hover_plain_columns(None, {"x": [np.nan]}) == {"x_plain": ["nan"]}


def test_plain_linear_tick_formatter_disables_scientific():
  """Linear tick formatter opts out of scientific notation for website-facing plots."""
  from bokeh.plotting import figure

  from hpcperfstats.analysis.metrics.lib.gen.utils import (
    new_plain_linear_tick_formatter,
    set_linear_axes_plain_numeric,
  )

  assert new_plain_linear_tick_formatter().use_scientific is False
  p = figure(width=80, height=60)
  set_linear_axes_plain_numeric(p)
  assert p.yaxis.formatter.use_scientific is False


def test_utils_module_has_no_pandas_dependency():
  import importlib
  import inspect

  from hpcperfstats.analysis.metrics.lib.gen import utils as u

  src = inspect.getsource(importlib.import_module(u.__name__))
  assert "import pandas" not in src
  assert "from pandas" not in src
