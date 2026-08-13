import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.job_dataframe import get_job_full_dataframe


def test_get_job_full_dataframe_builds_rows_from_type_detail():
  detail_payload = {
      "job_data": {
          "jid": 42,
          "jobname": "demo",
          "username": "alice",
          "account": "proj",
          "start_time": "2026-01-01T00:00:00Z",
          "end_time": "2026-01-01T00:10:00Z",
      },
      "schema": {"cpu": ["user", "system"]},
  }
  type_payload = {
      "stats_data": [["0 days 00:00:05", [1.0, 2.0]], ["0 days 00:00:10", [3.0, 4.0]]],
      "schema": ["user", "system"],
  }
  responses = {
      "jobs/42/": Mock(ok=True, data=detail_payload, error=None),
      "jobs/42/cpu/": Mock(ok=True, data=type_payload, error=None),
  }
  fake_client = Mock()
  fake_client.get_json.side_effect = lambda path: responses[path]

  from hpcperfstats_tools import job_dataframe as mod
  orig = mod.ApiClient
  mod.ApiClient = Mock(return_value=fake_client)
  try:
    df = get_job_full_dataframe("42", api_url="https://example.org/api/", api_key="k")
  finally:
    mod.ApiClient = orig

  assert isinstance(df, pd.DataFrame)
  assert len(df) == 2
  assert set(["jid", "type_name", "user", "system", "dt", "dt_seconds"]).issubset(df.columns)
  assert df["jid"].iloc[0] == 42
  assert df["type_name"].iloc[0] == "cpu"
  assert df["dt_seconds"].iloc[1] == 10.0

