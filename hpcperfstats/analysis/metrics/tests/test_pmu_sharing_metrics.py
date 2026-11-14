"""D6 (cpu-precision Phase 1b): Grace PMU sharing job metrics.

Monitor 3.1 (Phase 1b) emits counter-time lifetimes in ns on ``host_cpu_hw``:
``pmu_enabled_time`` (events enabled), ``pmu_running_time`` (events on a
counter) and ``pmu_solo_running_time`` (what they would run with no other PMU
user). They are counters because ingest sums ``host_cpu_hw`` rows across CPUs,
so the job metrics are ratios of rates:

* ``avg_pmu_running_pct`` = 100 x d(running) / d(enabled)
* ``avg_pmu_outside_pct`` = 100 x (1 - d(running) / d(solo)), clamped to 0-100

The monitor alone oversubscribes Grace (8 general events on 6 counters), so
~78% running with running == solo means no other PMU user (outside 0%).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.dbload.lib.monitor_naming import load_map
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  pmu_sharing_event_names,
)

_T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
_SHARING = frozenset({"avg_pmu_running_pct", "avg_pmu_outside_pct"})
# Per host: 9 core events x 1e9 ns/s enabled; the monitor alone runs 7/9.
_SOLO_ONLY = {
  "pmu_enabled_time": 9.0e9,
  "pmu_running_time": 7.0e9,
  "pmu_solo_running_time": 7.0e9,
}


def _records(rates_by_host, minutes=15):
  """host_cpu_hw rows: one sample per minute, constant per-second ``arc``."""
  rows = []
  for host, rates in rates_by_host.items():
    for i in range(minutes + 1):
      t = _T0 + timedelta(minutes=i)
      for event, rate in rates.items():
        rows.append(
          (t, host, "host_cpu_hw", event, "-", rate * 60.0 * i, rate, None)
        )
  return rows


def _sharing_rows(monkeypatch, table):
  """Run ``compute_metrics`` on ``table`` for the two sharing metrics only."""
  monkeypatch.setattr(metrics, "open_job_table", lambda job, **_kw: table)
  monkeypatch.setattr(
    metrics, "gpu_summary_tuple", lambda jt: (None, None, None, None)
  )
  monkeypatch.setattr(
    metrics, "compute_job_detail_fsio_metric_rows", lambda jt: []
  )
  monkeypatch.setattr(
    metrics, "_in_window_telemetry_bounds_for_job", lambda job: (None, None)
  )
  m = metrics.Metrics()
  m.simple_metrics_list = {
    name: spec
    for name, spec in m.simple_metrics_list.items()
    if name in _SHARING
  }
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid="pmushare1",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  out = m.compute_metrics(job)
  return {r["metric"]: r for r in out["rows"] if r["metric"] in _SHARING}


def test_own_oversubscription_only_reports_no_outside_share(monkeypatch):
  """Running == solo (monitor alone on the PMU): ~78% running, 0% outside."""
  rows = _sharing_rows(
    monkeypatch, table_from_records(_records({"c1": _SOLO_ONLY}))
  )

  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(700.0 / 9.0)
  assert rows["avg_pmu_outside_pct"]["value"] == pytest.approx(0.0, abs=1e-9)
  for name in _SHARING:
    assert rows[name]["no_data_reason"] is None, name
    assert rows[name]["units"] == "%", name
    assert rows[name]["type"] == "host_cpu_hw", name


def test_outside_holder_reports_the_shortfall_from_solo(monkeypatch):
  """nvidia-powerd-like holder: 2.5 of 7 expected ns/s ran -> 64.3% outside."""
  holder = dict(_SOLO_ONLY, pmu_running_time=2.5e9)
  rows = _sharing_rows(
    monkeypatch, table_from_records(_records({"c1": holder}))
  )

  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(250.0 / 9.0)
  assert rows["avg_pmu_outside_pct"]["value"] == pytest.approx(
    100.0 * (1.0 - 2.5 / 7.0)
  )


def test_hosts_are_pooled_by_rate_not_by_averaging_percents(monkeypatch):
  """One clean and one shared host: shares come from summed time rates."""
  holder = dict(_SOLO_ONLY, pmu_running_time=2.5e9)
  rows = _sharing_rows(
    monkeypatch,
    table_from_records(_records({"c1": _SOLO_ONLY, "c2": holder})),
  )

  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(
    100.0 * 9.5 / 18.0
  )
  assert rows["avg_pmu_outside_pct"]["value"] == pytest.approx(
    100.0 * (1.0 - 9.5 / 14.0)
  )


def test_running_above_solo_clamps_outside_share_to_zero(monkeypatch):
  """Kernel rotation noise can run slightly above solo: never negative."""
  noisy = dict(_SOLO_ONLY, pmu_running_time=7.2e9)
  rows = _sharing_rows(monkeypatch, table_from_records(_records({"c1": noisy})))

  assert rows["avg_pmu_outside_pct"]["value"] == 0.0
  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(720.0 / 9.0)


def test_unknown_core_without_solo_key_has_no_outside_share(monkeypatch):
  """No verified counter count (no solo key): running only, no guessing."""
  no_solo = {
    "pmu_enabled_time": 2.0e9,
    "pmu_running_time": 1.4e9,
  }
  rows = _sharing_rows(
    monkeypatch, table_from_records(_records({"c1": no_solo}))
  )

  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(70.0)
  assert rows["avg_pmu_outside_pct"]["value"] is None
  assert (
    rows["avg_pmu_outside_pct"]["no_data_reason"]
    == metrics.NO_SIMPLE_SAMPLES_MSG
  )


def test_pre_3_2_monitor_without_keys_keeps_no_simple_samples_reason(
  monkeypatch,
):
  """Jobs from monitor 3.1 and older have no sharing keys at all."""
  rows = _sharing_rows(
    monkeypatch,
    table_from_records(_records({"c1": {"instr_retired": 3.0e10}})),
  )

  for name in _SHARING:
    assert rows[name]["value"] is None, name
    assert rows[name]["no_data_reason"] == metrics.NO_SIMPLE_SAMPLES_MSG, name


def test_zero_enabled_time_is_no_data_not_a_division(monkeypatch):
  """Keys present but no enabled time accrued: no share, no ZeroDivision."""
  zero = dict.fromkeys(_SOLO_ONLY, 0.0)
  rows = _sharing_rows(monkeypatch, table_from_records(_records({"c1": zero})))

  for name in _SHARING:
    assert rows[name]["value"] is None, name
    assert rows[name]["no_data_reason"] == metrics.NO_SIMPLE_SAMPLES_MSG, name


def test_sharing_metrics_are_catalog_rows_in_percent():
  """Both metrics are host_cpu_hw catalog rows with units %."""
  catalog = {e["metric"]: e for e in metrics.job_metrics_catalog_entries()}
  for name in _SHARING:
    assert catalog[name]["type"] == "host_cpu_hw"
    assert catalog[name]["units"] == "%"


def test_metrics_contract_version_bumped_for_sharing_metrics():
  """New catalog metrics recompute older jobs (contract 9)."""
  assert metrics.METRICS_CONTRACT_VERSION == 9


def test_monitor_naming_resolves_pmu_sharing_keys():
  """Canonical names match the monitor 3.1 Phase 1b keys and are identity renames."""
  assert pmu_sharing_event_names() == (
    "pmu_enabled_time",
    "pmu_running_time",
    "pmu_solo_running_time",
  )
  renames = load_map.event_renames()
  for key in pmu_sharing_event_names():
    assert renames[key] == key


def test_sharing_metrics_are_staff_only_but_stay_in_the_catalog():
  """Both sharing metrics are computed for every job but shown to staff only."""
  catalog = {e["metric"] for e in metrics.job_metrics_catalog_entries()}
  assert {
    "avg_pmu_running_pct",
    "avg_pmu_outside_pct",
  } == metrics.STAFF_ONLY_JOB_METRICS
  assert catalog >= metrics.STAFF_ONLY_JOB_METRICS
  rows = [
    {"metric": "avg_flops"},
    {"metric": "avg_pmu_running_pct"},
    {"metric": "avg_pmu_outside_pct"},
  ]
  assert metrics.filter_staff_only_job_metrics(rows, True) is rows
  assert [
    r["metric"] for r in metrics.filter_staff_only_job_metrics(rows, False)
  ] == ["avg_flops"]
