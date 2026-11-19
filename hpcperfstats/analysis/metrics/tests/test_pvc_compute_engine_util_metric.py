"""avg_compute_engine_util from the intel_gpu compute_engine_util key, and the
GPU help prose the Python and SPA copies must share.

The monitor publishes ``compute_engine_util`` (XPU Manager compute engine
group utilization, percent, max over tiles) on ``intel_gpu``. On PVC nodes
with GFX firmware below PVC2_1.23423 the EU metrics (``sm_active``, EU Array
Active) are NOT_PRESENT, so this key is the working PVC busy signal. The
metric is the job mean of the per-sample value, a measured 0 is a value, and
a sentinel or absent key is a no-data reason, never 0.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
  to_time_us,
)
from hpcperfstats.analysis.metrics.lib.job_metric_display_labels import (
  JOB_METRIC_SHORT_LABELS,
)
from hpcperfstats.analysis.metrics.lib.plot.job_detail_bokeh_plot_descriptions import (
  JOB_DETAIL_BOKEH_HELP,
)
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
  SUMMARY_METRIC_DESCRIPTIONS,
  SUMMARY_METRIC_RESEARCHER_USE,
)
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import SentinelRun
from hpcperfstats.dbload.lib.monitor_naming import canonical

_T0 = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
_HOSTS = ["p1.x", "p2.x"]
_METRIC = "avg_compute_engine_util"
_LABEL = "Average Intel PVC compute engine busy"
_UTILS = (
  Path(__file__).resolve().parents[3] / "site" / "frontend" / "src" / "utils"
)


def _pvc_rows(event, values):
  """One intel_gpu row per minute per host for ``event`` (dev 0)."""
  return [
    (_T0 + timedelta(minutes=i), host, "intel_gpu", event, "0", v, None, None)
    for host in _HOSTS
    for i, v in enumerate(values)
  ]


def _cpu_rows(n=4):
  """host_cpu rows so the job has a window and a schema on every host."""
  return [
    (
      _T0 + timedelta(minutes=i),
      h,
      "host_cpu",
      "user",
      "-",
      10.0 * i,
      1.0,
      None,
    )
    for h in _HOSTS
    for i in range(n)
  ]


def _compute(monkeypatch, table):
  """Run ``compute_metrics`` for the PVC metric only."""
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
    k: v for k, v in m.simple_metrics_list.items() if k == _METRIC
  }
  m.complex_metrics_list = []
  out = m.compute_metrics(SimpleNamespace(jid="pvc1"))
  return {r["metric"]: r for r in out["rows"]}[_METRIC]


def _not_present_runs(event):
  """NOT_PRESENT (-2) sentinel run for ``event`` on every host."""
  return [
    SentinelRun(
      h,
      "intel_gpu",
      "0",
      event,
      -2,
      to_time_us(_T0),
      to_time_us(_T0 + timedelta(minutes=3)),
      1,
      1,
    )
    for h in _HOSTS
  ]


def _ts_entry(text, key):
  """``(description, researcherUse)`` of one ``key: { ... }`` TS entry."""
  m = re.search(
    rf"\n  {key}: \{{\n    description:\n      \"((?:[^\"\\]|\\.)*)\",\n"
    rf"    researcherUse:\n      \"((?:[^\"\\]|\\.)*)\",\n  \}}",
    text,
  )
  assert m, f"{key} not found in variableMetadata.ts"
  return m.group(1), m.group(2)


def test_metric_is_registered_on_intel_gpu_with_canonical_event():
  """The metric reads only intel_gpu compute_engine_util, in %."""
  assert canonical.INTEL_GPU_COMPUTE_ENGINE_UTIL == "compute_engine_util"
  spec = metrics.Metrics().simple_metrics_list[_METRIC]
  assert spec["typename"] == "intel_gpu"
  assert spec["events"] == ["compute_engine_util"]
  assert spec["units"] == "%"
  names = [e["metric"] for e in metrics.job_metrics_catalog_entries()]
  assert _METRIC in names


def test_short_label_in_python_and_spa_copies():
  """Job Detail table label, identical in the TS mirror."""
  assert JOB_METRIC_SHORT_LABELS[_METRIC] == _LABEL
  ts = (_UTILS / "jobMetricDisplayLabels.ts").read_text(encoding="utf-8")
  assert f'  {_METRIC}: "{_LABEL}",' in ts


def test_pvc_busy_under_stress_while_eu_metrics_not_present(monkeypatch):
  """PVC below firmware PVC2_1.23423: sm_active NOT_PRESENT, compute engine
  busy 99 under a stress run; the metric is that job mean."""
  table = table_from_records(
    _pvc_rows("compute_engine_util", [99.0, 99.0, 99.0, 99.0]),
    sentinel_runs=_not_present_runs("sm_active"),
  )
  row = _compute(monkeypatch, table)
  assert row["value"] == pytest.approx(99.0)
  assert row["type"] == "intel_gpu"
  assert row["units"] == "%"


def test_job_mean_and_measured_zero(monkeypatch):
  """Mean over samples; an idle PVC stays a stored 0, not missing."""
  row = _compute(
    monkeypatch,
    table_from_records(_pvc_rows("compute_engine_util", [40.0, 60.0])),
  )
  assert row["value"] == pytest.approx(50.0)
  assert row["no_data_reason"] is None
  row = _compute(
    monkeypatch,
    table_from_records(_pvc_rows("compute_engine_util", [0.0, 0.0, 0.0])),
  )
  assert row["value"] == 0.0


def test_not_present_key_is_a_reason_not_zero(monkeypatch):
  """A NOT_PRESENT compute_engine_util on every host gives no value and the
  researcher-visible reason."""
  table = table_from_records(
    _cpu_rows(),
    hosts=_HOSTS,
    schema={"host_cpu": ["user"], "intel_gpu": ["compute_engine_util"]},
    sentinel_runs=_not_present_runs("compute_engine_util"),
  )
  row = _compute(monkeypatch, table)
  assert row["value"] is None
  assert row["no_data_reason"] == "intel_gpu: counter not present (2/2 hosts)"


def test_no_intel_gpu_collector_has_no_value(monkeypatch):
  """CPU-only or NVIDIA job: no intel_gpu rows, no value, a reason."""
  row = _compute(monkeypatch, table_from_records(_cpu_rows(), hosts=_HOSTS))
  assert row["value"] is None
  assert row["no_data_reason"]
  assert row["type"] == "intel_gpu"


def test_metrics_contract_bumped_for_pvc_compute_busy():
  """Contract 15 adds avg_compute_engine_util (one bump over 14)."""
  assert metrics.METRICS_CONTRACT_VERSION == 15


def test_gpu_pie_help_names_pipes_and_matches_spa_copy():
  """Pie help: pipe busy share, IMMA / HMMA / DMMA / unattributed tensor and
  the integer pipe, no DFMA wedge; byte-identical in variableMetadata.ts."""
  desc, use = JOB_DETAIL_BOKEH_HELP["jobDetailPlot_multiprecision_gpu"]
  for phrase in (
    "pipe busy share, not FLOPs",
    "idle excluded",
    "Tensor IMMA (INT8/INT4)",
    "Tensor HMMA (FP16/BF16/TF32/FP8)",
    "Tensor DMMA (FP64)",
    "Tensor (unattributed",
    "Integer pipe",
  ):
    assert phrase in desc, phrase
  assert "DFMA" not in desc
  ts = (_UTILS / "variableMetadata.ts").read_text(encoding="utf-8")
  assert _ts_entry(ts, "jobDetailPlot_multiprecision_gpu") == (desc, use)


def test_summary_dfma_prose_is_the_fp64_fma_pipe_in_both_copies():
  """nv_tensor_dfma_active is the FP64 FMA pipe, not a tensor pipe."""
  desc = SUMMARY_METRIC_DESCRIPTIONS["nv_tensor_dfma_active"]
  use = SUMMARY_METRIC_RESEARCHER_USE["nv_tensor_dfma_active"]
  assert "FP64 FMA pipe" in desc
  assert "Hopper/Blackwell" in desc
  assert "not a tensor pipe" in desc
  assert "tensor-pipe activity" not in desc
  ts = (_UTILS / "variableMetadata.ts").read_text(encoding="utf-8")
  assert _ts_entry(ts, "nv_tensor_dfma_active") == (desc, use)


def test_spa_metadata_describes_pvc_firmware_caveat():
  """Derived-metric tooltip names the firmware floor for the EU metrics."""
  ts = (_UTILS / "variableMetadata.ts").read_text(encoding="utf-8")
  desc, use = _ts_entry(ts, _METRIC)
  assert "PVC2_1.23423" in desc
  assert use
