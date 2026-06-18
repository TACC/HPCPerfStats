"""Regression tests for Job Detail Bokeh screen-space help marker."""
from bokeh.embed import json_item
from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
    add_job_detail_bokeh_help_marker,
)


def test_json_item_contains_screen_space_help_label():
  p = figure(width=200, height=150, x_range=(0, 10), y_range=(0, 10))
  p.line([1, 9], [1, 9])
  add_job_detail_bokeh_help_marker(
      p,
      "Test plot description for help hover.",
      "Optional researcher-facing line.",
  )
  payload = json_item(p)
  blob = str(payload)
  assert "x_units" in blob and "screen" in blob
  assert "?" in blob or "\\u003f" in blob
