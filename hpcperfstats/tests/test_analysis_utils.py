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
  """queryset_to_dataframe converts list of dicts-like queryset to DataFrame.

    """
  from hpcperfstats.analysis.gen.utils import queryset_to_dataframe

  class MockQs:

    def values(self):
      return [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

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


def test_get_job_host_data_and_job_dict_no_job():
  """get_job_host_data_and_job_dict returns (empty DataFrame, None) when job not found."""
  from unittest.mock import patch
  from hpcperfstats.analysis.gen.utils import get_job_host_data_and_job_dict

  with patch("hpcperfstats.analysis.gen.utils.job_data") as mock_job_data:
    mock_job_data.objects.filter.return_value.values.return_value.first.return_value = None
    host_df, job_dict = get_job_host_data_and_job_dict(12345)
  assert host_df.empty
  assert job_dict is None


def test_get_job_host_data_and_job_dict_with_job_and_host_data():
  """get_job_host_data_and_job_dict returns (host_df, job_dict) when job exists and jid_table has data."""
  from unittest.mock import patch, MagicMock
  from hpcperfstats.analysis.gen.utils import get_job_host_data_and_job_dict

  job_row = {"jid": 999, "host_list": ["n1"], "start_time": None, "end_time": None}
  mock_df = pd.DataFrame({"host": ["n1"], "time": [pd.Timestamp("2024-01-01")], "value": [1.0]})

  mock_jt = MagicMock()
  mock_jt.start_time = pd.Timestamp("2024-01-01")
  mock_jt.end_time = pd.Timestamp("2024-01-02")
  mock_jt.get_full_host_data_df.return_value = mock_df

  with patch("hpcperfstats.analysis.gen.utils.job_data") as mock_job_data:
    with patch("hpcperfstats.analysis.gen.utils.jid_table", MagicMock(return_value=mock_jt)):
      mock_job_data.objects.filter.return_value.values.return_value.first.return_value = job_row
      host_df, job_dict = get_job_host_data_and_job_dict(999)
  assert len(host_df) == 1
  assert job_dict is not None
  assert job_dict["jid"] == 999
