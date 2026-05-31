"""Unit tests for analysis.gen.utils (clean_dataframe, queryset_to_dataframe).

"""
import numpy as np
import pandas as pd



def test_clean_dataframe_fillna():
  """clean_dataframe replaces NaN with empty string.

    """
  from hpcperfstats.analysis.gen.utils import clean_dataframe
  df = pd.DataFrame({"a": [1, np.nan, 3]})
  out = clean_dataframe(df)
  assert out["a"].iloc[1] == ""


def test_clean_dataframe_inf():
  """clean_dataframe replaces inf with empty string.

    """
  from hpcperfstats.analysis.gen.utils import clean_dataframe
  df = pd.DataFrame({"a": [1.0, np.inf, -np.inf]})
  out = clean_dataframe(df)
  assert out["a"].iloc[1] == ""
  assert out["a"].iloc[2] == ""


def test_queryset_to_dataframe_empty():
  """queryset_to_dataframe returns empty DataFrame for None.

    """
  from hpcperfstats.analysis.gen.utils import queryset_to_dataframe
  out = queryset_to_dataframe(None)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 0


def test_queryset_to_dataframe_mock_queryset():
  """queryset_to_dataframe converts iterable of dicts (e.g. queryset) to DataFrame."""
  from hpcperfstats.analysis.gen.utils import queryset_to_dataframe

  class MockQs:
    def __iter__(self):
      return iter([{"a": 1, "b": 2}, {"a": 3, "b": 4}])

  out = queryset_to_dataframe(MockQs())
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 2
  assert list(out.columns) == ["a", "b"]
  assert out["a"].tolist() == [1, 3]


def test_queryset_to_dataframe_empty_values_with_columns_kwarg():
  """Empty values(*columns) still exposes column names for concat/sort."""
  from hpcperfstats.analysis.gen.utils import queryset_to_dataframe

  class QsEmpty:
    def values(self, *cols):
      return []

  out = queryset_to_dataframe(QsEmpty(), columns=["host", "time"])
  assert list(out.columns) == ["host", "time"]
  assert len(out) == 0


def test_queryset_to_dataframe_empty_iter_with_values_select():
  """Empty .values() queryset: use query.values_select for DataFrame columns."""
  from hpcperfstats.analysis.gen.utils import queryset_to_dataframe

  class QsEmptyValues:
    class _Query:
      values_select = ("host", "time")

    query = _Query()

    def __iter__(self):
      return iter([])

  out = queryset_to_dataframe(QsEmptyValues())
  assert list(out.columns) == ["host", "time"]
  assert len(out) == 0


def test_tz_aware_bokeh_tick_formatter_returns_formatter():
  """tz_aware_bokeh_tick_formatter returns a Bokeh CustomJSTickFormatter with tz in args."""
  from hpcperfstats.analysis.gen.utils import tz_aware_bokeh_tick_formatter
  from bokeh.models import CustomJSTickFormatter

  formatter = tz_aware_bokeh_tick_formatter()
  assert isinstance(formatter, CustomJSTickFormatter)
  assert "tz" in formatter.args
  assert formatter.code is not None
  assert "tick" in formatter.code
  assert "hour12: true" in formatter.code


def test_tz_aware_bokeh_datetime_hover_formatter_returns_formatter():
  """Hover time formatter uses configured tz and 12-hour am/pm like axis ticks."""
  from bokeh.models.tools import CustomJSHover

  from hpcperfstats.analysis.gen.utils import new_tz_aware_bokeh_datetime_hover_formatter

  formatter = new_tz_aware_bokeh_datetime_hover_formatter()
  assert isinstance(formatter, CustomJSHover)
  assert "tz" in formatter.args
  assert formatter.code is not None
  assert "value" in formatter.code
  assert "hour12: true" in formatter.code
  assert "timeZone: tz" in formatter.code


def test_plain_linear_tick_formatter_disables_scientific():
  """Linear tick formatter opts out of scientific notation for website-facing plots."""
  from bokeh.plotting import figure

  from hpcperfstats.analysis.gen.utils import (
    new_plain_linear_tick_formatter,
    set_linear_axes_plain_numeric,
  )

  assert new_plain_linear_tick_formatter().use_scientific is False
  p = figure(width=80, height=60)
  set_linear_axes_plain_numeric(p)
  assert p.yaxis.formatter.use_scientific is False


