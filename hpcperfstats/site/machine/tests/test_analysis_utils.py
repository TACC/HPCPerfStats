"""Django tests for analysis.gen.utils.get_job_host_data_and_job_dict (requires ORM)."""
import pandas as pd
from unittest.mock import patch, MagicMock

import pytest

from hpcperfstats.analysis.gen.utils import get_job_host_data_and_job_dict


@pytest.mark.django_db
def test_get_job_host_data_and_job_dict_no_job():
  """get_job_host_data_and_job_dict returns (empty DataFrame, None) when job not found."""
  with patch("hpcperfstats.analysis.gen.utils.job_data") as mock_job_data:
    mock_job_data.objects.filter.return_value.values.return_value.first.return_value = None
    host_df, job_dict = get_job_host_data_and_job_dict(12345)
  assert host_df.empty
  assert job_dict is None


@pytest.mark.django_db
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

  with patch("hpcperfstats.analysis.gen.utils.job_data") as mock_job_data:
    with patch("hpcperfstats.analysis.gen.utils.jid_table", MagicMock(return_value=mock_jt)):
      mock_job_data.objects.filter.return_value.values.return_value.first.return_value = job_row
      host_df, job_dict = get_job_host_data_and_job_dict(999)
  assert len(host_df) == 1
  assert job_dict is not None
  assert job_dict["jid"] == 999
