"""Unit tests for shared Bokeh embed sizing helpers (no Django)."""

from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw


def test_figure_embed_kw_sets_stretch_width_and_height():
  kw = figure_embed_kw(200, title="T", x_axis_label="X")
  assert kw["sizing_mode"] == "stretch_width"
  assert kw["height"] == 200
  assert kw["title"] == "T"
  assert kw["x_axis_label"] == "X"


def test_figure_embed_kw_caller_overrides():
  kw = figure_embed_kw(150, title="x", sizing_mode="fixed")
  assert kw["height"] == 150
  assert kw["sizing_mode"] == "fixed"
  assert kw["title"] == "x"
