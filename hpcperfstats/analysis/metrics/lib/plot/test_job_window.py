"""Unit tests for shared job window parsing (summary plot)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest
from hpcperfstats.analysis.metrics.lib.plot.job_window import (
    job_window_bounds_local,
    job_window_label_strings,
    job_window_timestamps_utc,
)


class _Jt:
  def __init__(self, start_time, end_time):
    self.start_time = start_time
    self.end_time = end_time


@pytest.mark.parametrize(
    "start,end",
    [
        (None, "2020-01-01"),
        ("2020-01-01", None),
        ("2020-01-01T12:00:00+00:00", "2020-01-01T12:00:00+00:00"),
    ],
)
def test_job_window_timestamps_utc_invalid_ranges(start, end):
  assert job_window_timestamps_utc(_Jt(start, end)) == (None, None)


def test_job_window_timestamps_utc_valid():
  jt = _Jt("2020-01-01T10:00:00+00:00", "2020-01-01T11:00:00+00:00")
  start_ts, end_ts = job_window_timestamps_utc(jt)
  assert start_ts == pd.Timestamp("2020-01-01 10:00:00+0000", tz="UTC")
  assert end_ts == pd.Timestamp("2020-01-01 11:00:00+0000", tz="UTC")


def test_job_window_label_strings():
  jt = _Jt("2020-01-01T10:00:00+00:00", "2020-01-01T11:00:00+00:00")
  a, b = job_window_label_strings(jt)
  assert "2020-01-01 10:00:00+00:00" in a
  assert "2020-01-01 11:00:00+00:00" in b


def test_job_window_bounds_local_converts_timezone():
  jt = _Jt(
      datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc),
      datetime(2020, 1, 1, 11, 0, tzinfo=timezone.utc),
  )
  with patch("hpcperfstats.analysis.metrics.lib.plot.job_window.cfg.get_local_timezone") as mock_tz:
    mock_tz.return_value = "America/Chicago"
    start_local, end_local = job_window_bounds_local(jt)
  assert str(start_local.tz) == "America/Chicago"
  assert start_local < end_local


def test_hover_tooltip_html_host_time_value_contains_fields():
  from hpcperfstats.analysis.metrics.lib.plot.hover_html import hover_tooltip_html_host_time_value

  html = hover_tooltip_html_host_time_value("CPU", "cpu")
  assert "@host" in html
  assert "@_hover_time" in html
  assert "<strong>CPU:</strong>" in html
  assert "@cpu_plain" in html
