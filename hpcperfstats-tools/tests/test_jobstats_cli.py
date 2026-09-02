"""Tests for jobstats_cli helpers and main() exit codes."""

from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats_tools.jobstats_cli import (
    _bar,
    _compute_metrics,
    _format_timedelta,
    main,
    print_jobstats,
)


def test_format_timedelta_none_and_negative():
  assert _format_timedelta(None) == "N/A"
  assert _format_timedelta(-5) == "00:00:00"


def test_bar_clamps_percentage():
  s = _bar(150.0)
  assert "100" in s
  assert _bar(None).strip().startswith("[no data]")


def test_compute_metrics_cpu_util():
  job = {"ncores": 4}
  metrics_list = [{"metric": "avg_cpuusage", "value": 2.0}]
  m = _compute_metrics(job, metrics_list)
  assert m["cpu_util_pct"] == pytest.approx(50.0)


def test_main_returns_1_without_api_key(monkeypatch, capsys):
  monkeypatch.delenv("HPCPERFSTATS_TOOLS_INI", raising=False)
  with patch("hpcperfstats_tools.jobstats_cli.load_cached_api_key", return_value=None), patch(
      "hpcperfstats_tools.jobstats_cli.api_key_help_url", return_value="http://x/api-key/"
  ):
    code = main(["--api-url", "http://localhost:8000/api/", "12345"])
  assert code == 1
  err = capsys.readouterr().err
  assert "No API key" in err


def test_print_jobstats_auth_failure_prints_display_cache_path(capsys):
  """401 help must use API_KEY_CACHE_DISPLAY, not the Path storage object."""
  from hpcperfstats_tools.api_key_cache import API_KEY_CACHE_DISPLAY
  from hpcperfstats_tools.jobstats_cli import _get_json

  client = MagicMock()
  client.get_json.return_value = MagicMock(
      status_code=401, ok=False, data=None, error="unauthorized"
  )
  with patch(
      "hpcperfstats_tools.jobstats_cli.api_key_help_url",
      return_value="http://x/api-key/",
  ):
    data, code = _get_json(
        client, "http://localhost:8000/api/", "jobs/1/", True, "bad-key"
    )
  assert data is None
  assert code == 401
  out = capsys.readouterr().out
  assert API_KEY_CACHE_DISPLAY in out
  assert "cached in" in out.lower()
  # Must not print the Path object representation of home/.hpcperfstats-api
  assert "PosixPath" not in out
  assert "WindowsPath" not in out
  # Auth-failure help must not echo the supplied API key (CodeQL #23).
  assert "bad-key" not in out


def test_print_jobstats_returns_0(capsys):
  detail = {
      "job_data": {
          "jid": "9",
          "username": "u",
          "account": "a",
          "state": "COMPLETED",
          "nhosts": 1,
          "ncores": 4,
          "runtime": 3600.0,
          "timelimit": 7200.0,
      },
      "metrics_list": [{"metric": "avg_cpuusage", "value": 2.0, "units": ""}],
  }
  client = MagicMock()
  client.get_json.side_effect = [
      MagicMock(status_code=200, ok=True, data=detail, error=None),
      MagicMock(status_code=200, ok=True, data={"machine_name": "test"}, error=None),
  ]
  with patch("hpcperfstats_tools.jobstats_cli.ApiClient", return_value=client):
    code = print_jobstats("9", "http://localhost:8000/api/", True, "key")
  assert code == 0
  out = capsys.readouterr().out
  assert "9" in out
  assert "Slurm Job Statistics" in out
