"""Regression tests for window-coverage metrics readiness (dual-edge margins)."""

import os
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.utils import timezone

from hpcperfstats.analysis.metrics import update_metrics as um


def _patch_coverage_on(monkeypatch):
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_start_margin_seconds",
      lambda: 600.0,
  )
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_end_margin_seconds",
      lambda: 600.0,
  )


def _jid739342_window():
  start = datetime(2026, 6, 5, 22, 58, 35, tzinfo=dt_timezone.utc)
  end = datetime(2026, 6, 6, 13, 39, 44, tzinfo=dt_timezone.utc)
  first = datetime(2026, 6, 6, 4, 57, 32, tzinfo=dt_timezone.utc)
  last = datetime(2026, 6, 6, 13, 39, 30, tzinfo=dt_timezone.utc)
  return start, end, first, last


@pytest.mark.machine_unit_mock
def test_job_window_coverage_ready_both_margins_met():
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=5)
  last = end - timedelta(minutes=5)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is True
  assert reason["start_ok"] is True
  assert reason["end_ok"] is True


@pytest.mark.machine_unit_mock
def test_job_window_coverage_rejects_tail_only_739342_shape(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start, end, first, last = _jid739342_window()
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is False
  assert reason["start_ok"] is False
  assert reason["end_ok"] is True
  assert reason["start_lag_s"] > 600.0


@pytest.mark.machine_unit_mock
def test_job_window_coverage_rejects_missing_start_margin_only(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=15)
  last = end - timedelta(minutes=5)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is False
  assert reason["start_ok"] is False
  assert reason["end_ok"] is True


@pytest.mark.machine_unit_mock
def test_job_window_coverage_rejects_missing_end_margin_only(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=5)
  last = end - timedelta(minutes=30)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is False
  assert reason["start_ok"] is True
  assert reason["end_ok"] is False


@pytest.mark.machine_unit_mock
def test_job_window_coverage_empty_window_no_rows(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, None, None)
  assert ready is False
  assert reason["start_ok"] is False
  assert reason["end_ok"] is False


@pytest.mark.machine_unit_mock
def test_job_window_coverage_respects_custom_margins(monkeypatch):
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_start_margin_seconds",
      lambda: 120.0,
  )
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_end_margin_seconds",
      lambda: 120.0,
  )
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=1)
  last = end - timedelta(minutes=1)
  ready, _reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is True
  first_fail = start + timedelta(minutes=3)
  ready2, reason2 = um.evaluate_job_window_coverage_ready(
      start, end, first_fail, last)
  assert ready2 is False
  assert reason2["start_ok"] is False


@pytest.mark.machine_unit_mock
def test_job_window_coverage_disabled_falls_back_to_legacy_post_end(monkeypatch):
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  latest = {
      "h1": end + timedelta(seconds=1),
      "h2": end + timedelta(seconds=2),
  }
  assert um._legacy_all_hosts_sample_after_end(end, {"h1", "h2"}, latest) is True
  latest["h2"] = end
  assert um._legacy_all_hosts_sample_after_end(end, {"h1", "h2"}, latest) is False


@pytest.mark.machine_unit_mock
def test_filter_jids_with_samples_after_end_uses_window_coverage(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start, end, first, last = _jid739342_window()
  jobs_rows = [{
      "jid": "739342",
      "start_time": start,
      "end_time": end,
      "host_list": ["c641-032.vista.tacc.utexas.edu"],
  }]

  class _JobManager:
    def filter(self, **kwargs):
      class _Qs:
        def order_by(self, *args):
          return self

        def values(self, *fields):
          return jobs_rows
      return _Qs()

  monkeypatch.setattr(um.job_data, "objects", _JobManager())
  monkeypatch.setattr(
      um,
      "_in_window_min_max_by_job_rows",
      lambda jobs: {"739342": (first, last)},
  )
  monkeypatch.setattr(
      um,
      "persist_window_coverage_gate_failure",
      lambda *args, **kwargs: False,
  )
  assert um._filter_jids_with_samples_after_end(["739342"]) == []


@pytest.mark.machine_unit_mock
def test_ready_jids_from_job_rows_uses_window_coverage(monkeypatch):
  _patch_coverage_on(monkeypatch)
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  jobs = [{
      "jid": "ok1",
      "start_time": start,
      "end_time": end,
      "host_list": ["n1.example.org"],
  }]
  monkeypatch.setattr(
      um,
      "_in_window_min_max_by_job_rows",
      lambda rows: {
          "ok1": (start + timedelta(minutes=1), end - timedelta(minutes=1)),
      },
  )
  assert um._ready_jids_from_job_rows(jobs) == ["ok1"]


@pytest.mark.machine_unit_mock
def test_scheduler_defers_jid_logs_coverage_reason(capsys):
  """Deferred coverage logs start_ok/end_ok once per jid per scheduler session."""
  um.reset_metrics_coverage_defer_log_session()
  reason = {
      "start_ok": False,
      "end_ok": True,
      "start_lag_s": 3600.0,
      "end_lag_s": 14.0,
      "start_margin_s": 600.0,
      "end_margin_s": 600.0,
  }
  um._log_metrics_deferred_coverage_once("739342", reason)
  out = capsys.readouterr().out
  assert "metrics_deferred_coverage jid=739342" in out
  assert "start_ok=False" in out
  assert "end_ok=True" in out
  um._log_metrics_deferred_coverage_once("739342", reason)
  assert capsys.readouterr().out == ""


@pytest.mark.machine_unit_mock
def test_evaluate_accepts_explicit_margins_kw():
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=2)
  last = end - timedelta(minutes=2)
  ready, reason = um.evaluate_job_window_coverage_ready(
      start, end, first, last, start_margin_s=180.0, end_margin_s=180.0)
  assert ready is True
  assert reason["start_margin_s"] == 180.0
  assert reason["end_margin_s"] == 180.0


@pytest.mark.machine_unit_mock
def test_evaluate_rejects_mixed_naive_aware_datetimes():
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0)
  first = start + timedelta(minutes=5)
  last = end - timedelta(minutes=5)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert ready is False
  assert reason["mixed_naive_aware"] is True


@pytest.mark.machine_unit_mock
def test_huge_margins_log_or_fail_fast(monkeypatch, capsys):
  um.reset_metrics_coverage_defer_log_session()
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_start_margin_seconds",
      lambda: 700000.0,
  )
  monkeypatch.setattr(
      um.cfg,
      "get_metrics_readiness_end_margin_seconds",
      lambda: 700000.0,
  )
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=5)
  last = end - timedelta(minutes=5)
  ready, reason = um.evaluate_job_window_coverage_ready(start, end, first, last)
  assert reason["margin_exceeds_duration"] is True
  assert "metrics_readiness" in capsys.readouterr().out


@pytest.mark.machine_unit_mock
def test_margin_defer_churn(monkeypatch, capsys):
  """Boundary ±1s toggles ready; defer log fires once per jid per session."""
  _patch_coverage_on(monkeypatch)
  um.reset_metrics_coverage_defer_log_session()
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  margin = 600.0
  on_boundary_first = start + timedelta(seconds=margin)
  on_boundary_last = end - timedelta(seconds=margin)
  ready_ok, _ = um.evaluate_job_window_coverage_ready(
      start, end, on_boundary_first, on_boundary_last,
      start_margin_s=margin, end_margin_s=margin)
  assert ready_ok is True
  ready_fail, reason_fail = um.evaluate_job_window_coverage_ready(
      start, end,
      on_boundary_first + timedelta(seconds=1),
      on_boundary_last,
      start_margin_s=margin, end_margin_s=margin)
  assert ready_fail is False
  um._log_metrics_deferred_coverage_once("marginjid", reason_fail)
  um._log_metrics_deferred_coverage_once("marginjid", reason_fail)
  out = capsys.readouterr().out
  assert out.count("metrics_deferred_coverage jid=marginjid") == 1


@pytest.mark.machine_unit_mock
def test_ready_jids_batch_hoists_margin_config_calls(monkeypatch):
  """Coverage batch path reads margin getters once per batch, not per job."""
  _patch_coverage_on(monkeypatch)
  margin_calls = {"start": 0, "end": 0}

  def _start_margin():
    margin_calls["start"] += 1
    return 600.0

  def _end_margin():
    margin_calls["end"] += 1
    return 600.0

  monkeypatch.setattr(
      um.cfg, "get_metrics_readiness_start_margin_seconds", _start_margin)
  monkeypatch.setattr(
      um.cfg, "get_metrics_readiness_end_margin_seconds", _end_margin)
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  jobs = [
      {
          "jid": "j1",
          "start_time": start,
          "end_time": end,
          "host_list": ["n1.example.org"],
      },
      {
          "jid": "j2",
          "start_time": start,
          "end_time": end,
          "host_list": ["n2.example.org"],
      },
  ]
  monkeypatch.setattr(
      um,
      "_in_window_min_max_by_job_rows",
      lambda rows: {
          r["jid"]: (start + timedelta(minutes=1), end - timedelta(minutes=1))
          for r in rows
      },
  )
  um._ready_jids_from_job_rows(jobs)
  assert margin_calls == {"start": 1, "end": 1}


@pytest.mark.machine_unit_mock
def test_compute_metrics_uses_passed_bounds_without_second_host_aggregate(monkeypatch):
  """Scheduler-provided telemetry bounds skip duplicate host_data aggregate."""
  import contextlib
  import pandas as pd

  from hpcperfstats.analysis.metrics.lib.metrics import Metrics, jid_table

  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  t_first = start + timedelta(minutes=5)
  t_last = end - timedelta(minutes=5)
  aggregate_calls = [0]

  def _spy_bounds(job):
    aggregate_calls[0] += 1
    return None, None

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._in_window_telemetry_bounds_for_job",
      _spy_bounds,
  )

  class _FakeJt:
    schema = {}
    jid = "nobounds1"

    def get_full_host_data_df(self, columns=None):
      return pd.DataFrame()

  @contextlib.contextmanager
  def _fake_jid_table(jid):
    yield _FakeJt()

  monkeypatch.setattr(jid_table, "jid_table", _fake_jid_table)
  job_ref = SimpleNamespace(
      jid="nobounds1",
      telemetry_first_time=t_first,
      telemetry_last_time=t_last,
  )
  metrics_obj = Metrics()
  metrics_obj.simple_metrics_list = {}
  payload = metrics_obj.compute_metrics(job_ref)
  assert aggregate_calls[0] == 0
  assert payload["telemetry_first_time"] == t_first
  assert payload["telemetry_last_time"] == t_last


@pytest.mark.machine_unit_mock
def test_persist_window_coverage_gate_failure_writes_insufficient_catalog(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.metrics import (
      INSUFFICIENT_DATA_FOR_METRICS_PROCESSING,
      persist_window_coverage_gate_failure,
  )

  deleted = []
  persisted = {}

  class _MdQs:
    def filter(self, **kwargs):
      return self

    def delete(self):
      deleted.append(True)
      return (0, {})

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._gate_failure_catalog_already_clean",
      lambda _jid: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.metrics_data.objects",
      SimpleNamespace(filter=lambda **kw: _MdQs()),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.job_data.objects",
      SimpleNamespace(filter=lambda **kw: SimpleNamespace(first=lambda: None)),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.cache_utils.invalidate_job_plot_cache_keys_for_jids",
      lambda jids: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.cache_utils.invalidate_jid_derived_cache_keys",
      lambda jids: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
      lambda _jid: 7,
  )

  def _capture_batch(rows, distinct_n, **kwargs):
    persisted["rows"] = list(rows)
    persisted["distinct_n"] = distinct_n
    persisted["kwargs"] = kwargs

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._persist_metrics_batch",
      _capture_batch,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.job_metrics_catalog_entries",
      lambda: [{"type": "cpu", "metric": "avg_cpuusage", "units": "#cores"}],
  )

  assert persist_window_coverage_gate_failure("gate_j1") is True
  assert deleted
  assert len(persisted["rows"]) == 1
  assert persisted["rows"][0]["no_data_reason"] == INSUFFICIENT_DATA_FOR_METRICS_PROCESSING
  assert persisted["rows"][0]["value"] is None
  assert persisted["distinct_n"] == 7


@pytest.mark.machine_unit_mock
def test_persist_window_coverage_gate_failure_idempotent_skip(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.metrics import persist_window_coverage_gate_failure

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._gate_failure_catalog_already_clean",
      lambda _jid: True,
  )
  called = {"delete": False}

  class _MdQs:
    def filter(self, **kwargs):
      return self

    def delete(self):
      called["delete"] = True
      return (0, {})

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.metrics_data.objects",
      SimpleNamespace(filter=lambda **kw: _MdQs()),
  )
  assert persist_window_coverage_gate_failure("gate_j2") is False
  assert called["delete"] is False


@pytest.mark.machine_unit_mock
def test_maybe_persist_window_coverage_gate_failure_on_start_edge(monkeypatch):
  _patch_coverage_on(monkeypatch)
  calls = []
  monkeypatch.setattr(
      um,
      "persist_window_coverage_gate_failure",
      lambda jid, **kw: calls.append((jid, kw)) or True,
  )
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=15)
  last = end - timedelta(minutes=5)
  stats = {"gate_failure_persisted_jids": 0}
  lock = threading.Lock()
  um._maybe_persist_window_coverage_gate_failure(
      "gate_j3",
      start,
      end,
      first,
      last,
      stats=stats,
      scheduler_shared_lock=lock,
  )
  assert calls
  assert calls[0][0] == "gate_j3"
  assert stats["gate_failure_persisted_jids"] == 1


@pytest.mark.machine_unit_mock
def test_maybe_persist_window_coverage_gate_failure_noop_when_ready(monkeypatch):
  _patch_coverage_on(monkeypatch)
  calls = []
  monkeypatch.setattr(
      um,
      "persist_window_coverage_gate_failure",
      lambda jid, **kw: calls.append(jid) or True,
  )
  start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
  end = datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
  first = start + timedelta(minutes=5)
  last = end - timedelta(minutes=5)
  um._maybe_persist_window_coverage_gate_failure(
      "gate_j4",
      start,
      end,
      first,
      last,
  )
  assert calls == []
