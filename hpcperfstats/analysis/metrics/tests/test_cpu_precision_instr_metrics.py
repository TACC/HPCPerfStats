"""C1/C4/O10: CPU FP-instruction precision metrics from Grace host_cpu_hw keys.

Phase 1 of the cpu-precision-mix plan: the monitor emits lifetime counters
``arm_fp_dp_spec`` / ``arm_fp_sp_spec`` / ``arm_fp_hp_spec`` (speculative FP
instruction counts) on ``host_cpu_hw``; ``avg_fp64_instr`` / ``avg_fp32_instr``
/ ``avg_fp16_instr`` are their job rates in Ginstr/s. A measured 0 (samples
exist, rate 0) is an idle job: value 0 and no ``no_data_reason``. Only a job
whose schema never carried the key keeps ``NO_SIMPLE_SAMPLES_MSG``.
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
  arm_fp_dp_spec_event_names,
  arm_fp_hp_spec_event_names,
  arm_fp_sp_spec_event_names,
)

_T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
_PRECISION_METRICS = (
  "avg_fp64_instr",
  "avg_fp32_instr",
  "avg_fp16_instr",
  "avg_arm_int16_ops",
  "avg_arm_int8_ops",
)
_PRECISION = frozenset(_PRECISION_METRICS)
_EVENT_FOR_METRIC = {
  "avg_fp64_instr": "arm_fp_dp_spec",
  "avg_fp32_instr": "arm_fp_sp_spec",
  "avg_fp16_instr": "arm_fp_hp_spec",
  "avg_arm_int16_ops": "arm_int16_ops",
  "avg_arm_int8_ops": "arm_int8_ops",
}


def _grace_records(rates_by_host, minutes=15):
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


def _precision_rows(monkeypatch, table):
  """Run ``compute_metrics`` on ``table`` for the precision metrics only."""
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
    if name in _PRECISION
  }
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid="cpuprec1",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  out = m.compute_metrics(job)
  return {r["metric"]: r for r in out["rows"] if r["metric"] in _PRECISION}


def test_grace_fp_spec_keys_feed_fp_instr_metrics_as_ginstr_per_s(
  monkeypatch,
):
  """DP/SP/HP spec counters become Ginstr/s rates averaged across hosts."""
  host_a = {
    "arm_fp_dp_spec": 4.0e9,
    "arm_fp_sp_spec": 2.0e9,
    "arm_fp_hp_spec": 1.0e9,
    "arm_int16_ops": 0.5e9,
    "arm_int8_ops": 0.25e9,
  }
  host_b = {k: v / 2.0 for k, v in host_a.items()}
  table = table_from_records(_grace_records({"c1": host_a, "c2": host_b}))

  rows = _precision_rows(monkeypatch, table)

  expected = {
    "avg_fp64_instr": 3.0,
    "avg_fp32_instr": 1.5,
    "avg_fp16_instr": 0.75,
    "avg_arm_int16_ops": 0.375,
    "avg_arm_int8_ops": 0.1875,
  }
  for name, value in expected.items():
    row = rows[name]
    assert row["value"] == pytest.approx(value, rel=1e-9), name
    assert row["no_data_reason"] is None, name
    assert row["units"] == "Ginstr/s", name
    assert row["type"] == "host_cpu_hw", name


def test_fp_instr_metrics_keep_widths_separate(monkeypatch):
  """A DGEMM-like job (DP only) shows FP64 > 0 and measured-zero FP32/FP16."""
  table = table_from_records(
    _grace_records(
      {
        "c1": {
          "arm_fp_dp_spec": 6.0e9,
          "arm_fp_sp_spec": 0.0,
          "arm_fp_hp_spec": 0.0,
        }
      }
    )
  )

  rows = _precision_rows(monkeypatch, table)

  assert rows["avg_fp64_instr"]["value"] == pytest.approx(6.0, rel=1e-9)
  assert rows["avg_fp32_instr"]["value"] == 0.0
  assert rows["avg_fp16_instr"]["value"] == 0.0
  assert rows["avg_fp32_instr"]["no_data_reason"] is None
  assert rows["avg_fp16_instr"]["no_data_reason"] is None


def test_measured_zero_is_stored_as_zero_without_no_data_reason(monkeypatch):
  """C4: idle Grace job (all keys sampled at 0) stores 0, not a reason."""
  table = table_from_records(
    _grace_records({"c1": dict.fromkeys(_EVENT_FOR_METRIC.values(), 0.0)})
  )

  rows = _precision_rows(monkeypatch, table)

  for name in _PRECISION_METRICS:
    assert rows[name]["value"] == 0.0, name
    assert rows[name]["no_data_reason"] is None, name


def test_never_sampled_keys_keep_no_simple_samples_reason(monkeypatch):
  """C4: an old-monitor Grace job without the keys keeps the reason."""
  table = table_from_records(_grace_records({"c1": {"instr_retired": 3.0e10}}))

  rows = _precision_rows(monkeypatch, table)

  for name in _PRECISION_METRICS:
    assert rows[name]["value"] is None, name
    assert rows[name]["no_data_reason"] == metrics.NO_SIMPLE_SAMPLES_MSG, name


def test_legacy_grace_scalar_keys_do_not_feed_fp_instr_metrics(monkeypatch):
  """O10: the retired scalar keys never masquerade as FP-instruction rates."""
  table = table_from_records(
    _grace_records(
      {
        "c1": {
          "fp_arith_inst_retired_scalar_double": 5.0e9,
          "fp_arith_inst_retired_scalar_single": 5.0e9,
        }
      }
    )
  )

  rows = _precision_rows(monkeypatch, table)

  assert rows["avg_fp64_instr"]["value"] is None
  assert rows["avg_fp32_instr"]["value"] is None
  assert (
    rows["avg_fp64_instr"]["no_data_reason"] == metrics.NO_SIMPLE_SAMPLES_MSG
  )


def test_flops_precision_metrics_stay_in_catalog_for_history():
  """O10: avg_flops64b/32b remain catalog rows (GF) next to the new metrics."""
  catalog = {e["metric"]: e for e in metrics.job_metrics_catalog_entries()}
  assert catalog["avg_flops64b"]["units"] == "GF"
  assert catalog["avg_flops32b"]["units"] == "GF"
  for name in ("avg_fp64_instr", "avg_fp32_instr", "avg_fp16_instr"):
    assert catalog[name]["units"] == "Ginstr/s"
    assert catalog[name]["type"] == "host_cpu_hw"


def test_metrics_contract_version_bumped_for_precision_semantics():
  """O13: the precision change recomputes older jobs (contract 8)."""
  assert metrics.METRICS_CONTRACT_VERSION == 8


def test_monitor_naming_resolves_new_grace_fp_spec_keys():
  """C1: canonical names match the monitor 3.1 host_cpu_hw keys."""
  assert arm_fp_dp_spec_event_names() == ("arm_fp_dp_spec",)
  assert arm_fp_sp_spec_event_names() == ("arm_fp_sp_spec",)
  assert arm_fp_hp_spec_event_names() == ("arm_fp_hp_spec",)
  renames = load_map.event_renames()
  for key in ("arm_fp_dp_spec", "arm_fp_sp_spec", "arm_fp_hp_spec"):
    assert renames[key] == key
