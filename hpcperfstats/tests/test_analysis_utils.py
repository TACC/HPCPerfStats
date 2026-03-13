"""Unit tests for analysis.gen.utils (clean_dataframe, queryset_to_dataframe).

"""
import numpy as np
import pandas as pd

import pytest


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


def test_tz_aware_bokeh_tick_formatter_returns_formatter():
  """tz_aware_bokeh_tick_formatter returns a Bokeh CustomJSTickFormatter with tz in args."""
  from hpcperfstats.analysis.gen.utils import tz_aware_bokeh_tick_formatter
  from bokeh.models import CustomJSTickFormatter

  formatter = tz_aware_bokeh_tick_formatter()
  assert isinstance(formatter, CustomJSTickFormatter)
  assert "tz" in formatter.args
  assert formatter.code is not None
  assert "tick" in formatter.code


