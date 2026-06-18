"""Django tests for analysis.gen.utils.get_job_host_data_and_job_dict (requires ORM)."""
import pandas as pd
from unittest.mock import patch, MagicMock

import pytest

from hpcperfstats.analysis.metrics.lib.gen import utils as gen_utils
from hpcperfstats.analysis.metrics.lib.gen.utils import get_job_host_data_and_job_dict

pytestmark = pytest.mark.django_db(databases=[])


def test_get_job_host_data_and_job_dict_no_job():
  """get_job_host_data_and_job_dict returns (empty DataFrame, None) when job not found."""
  with patch("hpcperfstats.analysis.metrics.lib.gen.utils.job_data") as mock_job_data:
    mock_job_data.objects.filter.return_value.values.return_value.first.return_value = None
    host_df, job_dict = get_job_host_data_and_job_dict(12345)
  assert host_df.empty
  assert job_dict is None


def test_get_job_host_data_and_job_dict_with_job_and_host_data():
  """get_job_host_data_and_job_dict returns (host_df, job_dict) when job exists and jid_table has data."""
  job_row = {"jid": 999, "host_list": ["n1"], "start_time": None, "end_time": None}
  mock_df = pd.DataFrame({
      "host": ["n1"],
      "time": [pd.Timestamp("2024-01-01")],
      "value": [1.0]
  })

  mock_jt = MagicMock()
  mock_jt.start_time = pd.Timestamp("2024-01-01")
  mock_jt.end_time = pd.Timestamp("2024-01-02")
  mock_jt.get_full_host_data_df.return_value = mock_df

  with patch("hpcperfstats.analysis.metrics.lib.gen.utils.job_data") as mock_job_data:
    with patch("hpcperfstats.analysis.metrics.lib.gen.utils.jid_table", MagicMock(return_value=mock_jt)):
      mock_job_data.objects.filter.return_value.values.return_value.first.return_value = job_row
      host_df, job_dict = get_job_host_data_and_job_dict(999)
  assert len(host_df) == 1
  assert job_dict is not None
  assert job_dict["jid"] == 999


def test_get_job_host_data_and_job_dict_handles_cached_orm_exception():
  """get_job_host_data_and_job_dict returns (empty DataFrame, None) when cache/DB layer raises."""
  with patch("hpcperfstats.analysis.metrics.lib.gen.utils.job_data") as mock_job_data, patch(
      "hpcperfstats.analysis.metrics.lib.gen.utils.jid_table"
  ):
    mock_job_data.objects.filter.side_effect = Exception("boom")
    host_df, job_dict = get_job_host_data_and_job_dict(111)
  assert host_df.empty
  assert job_dict is None


def test_job_data_and_jid_table_lazy_singletons_respected():
  """Module-level job_data and jid_table singletons are used when already set (no reimport)."""
  fake_job_data = object()
  fake_jid_table = object()
  gen_utils.job_data = fake_job_data
  gen_utils.jid_table = fake_jid_table

  with patch("hpcperfstats.analysis.metrics.lib.gen.utils.job_data") as mock_job_data, patch(
      "hpcperfstats.analysis.metrics.lib.gen.utils.jid_table"
  ):
    mock_job_data.objects.filter.return_value.values.return_value.first.return_value = None
    host_df, job_dict = get_job_host_data_and_job_dict(222)

  assert host_df.empty
  assert job_dict is None
  assert gen_utils.job_data is fake_job_data
  assert gen_utils.jid_table is fake_jid_table
