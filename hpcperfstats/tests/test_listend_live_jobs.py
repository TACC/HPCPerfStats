"""Unit tests for live-job metric parsing in listend."""

from pathlib import Path

import pytest


def _repo_sample_text():
  root = Path(__file__).resolve().parents[2]
  p = root / "hpcperfstats" / "dbload" / "tests" / "HPCPerfStatsdDataSample"
  return p.read_text(encoding="utf-8", errors="replace")


def test_parse_live_job_metrics_full_sample():
  import hpcperfstats.listend as listend

  text = _repo_sample_text()
  out = listend.parse_live_job_metrics(text)
  assert out is not None
  assert out["jid"] == "2946877"
  assert "stampede3.tacc.utexas.edu" in out["host"]
  assert out["cpu_util"] is not None
  assert 0 <= out["cpu_util"] <= 100
  assert out["mem_util"] is not None
  assert 0 <= out["mem_util"] <= 100


def test_parse_live_job_metrics_named_percents_override():
  import hpcperfstats.listend as listend

  body = (
      "1 99 host.example.com\n"
      "cpu_util=12.5\n"
      "mem_util=34\n"
  )
  out = listend.parse_live_job_metrics(body)
  assert out is not None
  assert out["jid"] == "99"
  assert out["host"] == "host.example.com"
  assert out["cpu_util"] == 12.5
  assert out["mem_util"] == 34.0


def test_parse_live_job_metrics_rejects_bad_jid():
  import hpcperfstats.listend as listend

  body = "1 - host.example.com\nmem 0 100 0 50 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
  assert listend.parse_live_job_metrics(body) is None
