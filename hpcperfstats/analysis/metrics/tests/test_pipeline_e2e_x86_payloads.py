"""Unit oracle for the pipeline E2E x86 payloads (no compose stack).

``tests/pipeline_e2e/monitor_payloads.py`` publishes one Intel SPR Max host
and one AMD Zen5 host with direct-perf ``host_cpu_hw`` keys. The compose
workflow asserts their job metrics against ``X86_E2E_*_EXPECTED``; this test
proves those numbers on the host by running the same text through the real
parser and delta step, then through ``compute_metrics``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
  max_node_power_est_w,
)
from hpcperfstats.dbload.lib import monitor_mark_side_ingest
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  DeltaCarryState,
  IncrementalStatsParser,
  compute_deltas_and_arc,
)
from tests.pipeline_e2e.monitor_payloads import (
  X86_E2E_AMD_EXPECTED,
  X86_E2E_INTEL_EXPECTED,
  X86_E2E_NOT_PRESENT,
  pipeline_e2e_x86_publish_bodies,
)

_EPOCHS = [1.7e9 + s for s in (0, 2400, 5700, 9000, 10500, 11100)]
_RECORD_COLS = [
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "value",
  "wid",
  "mult",
  "unit",
]


@pytest.fixture(autouse=True)
def _no_side_table_writes(monkeypatch):
  """Sentinel-run side tables need a database; this oracle reads rows only."""
  monkeypatch.setattr(
    monitor_mark_side_ingest, "persist_monitor_marks", lambda marks: None
  )


def _ingested_rows(vendor: str) -> pd.DataFrame:
  """Parse one x86 host's bodies and apply ingest's delta / arc step."""
  carry = DeltaCarryState()
  parser = IncrementalStatsParser(0, delta_carry=carry)
  for body in pipeline_e2e_x86_publish_bodies(
    fqdn=f"x86{vendor}.e2e", jid="x86job", vendor=vendor, epoch_samples=_EPOCHS
  ):
    parser.feed_lines(body.splitlines())
  records, _procs = parser.finish()
  return compute_deltas_and_arc(
    pd.DataFrame(records, columns=_RECORD_COLS), carry=carry
  )


def _table(rows: pd.DataFrame):
  """ColumnarJobTable over the ingested rows (whole-node, so ``dev`` is -)."""
  return table_from_records(
    (
      pd.to_datetime(r.time, unit="s", utc=True),
      r.host,
      r.type,
      r.event,
      r.dev or "-",
      r.value,
      None if pd.isna(r.arc) else r.arc,
      None if pd.isna(r.delta) else r.delta,
    )
    for r in rows.itertuples()
  )


def _metrics(monkeypatch, table, names):
  """Run ``compute_metrics`` on ``table`` restricted to simple ``names``."""
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
    n: spec for n, spec in m.simple_metrics_list.items() if n in names
  }
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid="x86job",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  out = m.compute_metrics(job)
  return {r["metric"]: r["value"] for r in out["rows"] if r["metric"] in names}


@pytest.mark.parametrize(
  ("vendor", "expected"),
  [("intel", X86_E2E_INTEL_EXPECTED), ("amd", X86_E2E_AMD_EXPECTED)],
)
def test_x86_payload_job_metrics_match_e2e_expectations(
  monkeypatch, vendor, expected
):
  """Every expected x86 E2E metric comes out of the real ingest + metrics."""
  table = _table(_ingested_rows(vendor))
  simple = {k for k in expected if k != "max_node_power_est_w"}
  got = _metrics(monkeypatch, table, simple)
  if "max_node_power_est_w" in expected:
    got["max_node_power_est_w"] = max_node_power_est_w(table)
  for name, want in expected.items():
    assert got[name] == pytest.approx(want, rel=1e-6), name


def test_x86_payload_sentinels_and_rapl_wrap_survive_ingest():
  """NOT_PRESENT keys keep the code with no arc; the RAPL wrap is undone."""
  rows = _ingested_rows("intel")
  amd_keys = rows[rows["event"].isin(["fp_ops_retired", "amd_fp_dp_ops"])]
  assert not amd_keys.empty
  assert (amd_keys["value"] == X86_E2E_NOT_PRESENT).all()
  assert amd_keys["arc"].isna().all()
  rapl = rows[(rows["type"] == "intel_x86_rapl")].sort_values("time")
  assert (rapl["value"].diff().dropna() < 0).any()  # the counter wrapped
  assert (rapl["delta"].dropna() > 0).all()
