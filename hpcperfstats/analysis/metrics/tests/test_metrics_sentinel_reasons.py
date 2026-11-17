"""Job Detail no-data reasons from monitor value sentinels.

A metric without a value explains itself from the ``host_sentinel_summary``
runs of the job window ("<type>: <description> (n/N hosts)", one part per
code joined by "; ", capped at 512 chars) or, when the job schema never
carried the type, "<type> collector not found on this node's monitor".
Jobs without sentinel evidence keep today's generic reasons, and the codes
are persisted in ``metrics_data.no_data_codes``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
  to_time_us,
)
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import (
  REASON_ELLIPSIS,
  REASON_MAX_CHARS,
  SentinelRun,
  cap_reason,
  format_histogram_reason,
  no_data_reason,
  sentinel_host_histogram,
)

_T0 = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
_HOSTS = ["c1.x", "c2.x", "c3.x", "c4.x"]


def _run(host, code, *, typ="host_ib", event="port_rcv_data", n_dev=1, n_tot=1):
  """A side-table run covering the first 15 minutes of the job."""
  return SentinelRun(
    host,
    typ,
    "",
    event,
    code,
    to_time_us(_T0),
    to_time_us(_T0 + timedelta(minutes=15)),
    n_dev,
    n_tot,
  )


def _cpu_rows():
  """host_cpu rows on every host so the job has host_data and a schema."""
  rows = []
  for host in _HOSTS:
    for i in range(16):
      t = _T0 + timedelta(minutes=i)
      rows.append((t, host, "host_cpu", "user", "-", 100.0 * i, 1.0, None))
  return rows


_SCHEMA = {
  "host_cpu": ["user"],
  "host_block": ["rd_sectors", "wr_sectors"],
  "host_ib": ["port_rcv_data"],
}


def _compute(monkeypatch, table, simple=("avg_blockbw",), complex_=()):
  """Run ``compute_metrics`` on ``table`` for the named metrics only."""
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
    k: v for k, v in m.simple_metrics_list.items() if k in simple
  }
  m.complex_metrics_list = list(complex_)
  out = m.compute_metrics(SimpleNamespace(jid="sent1"))
  return {r["metric"]: r for r in out["rows"]}


def test_histogram_reason_names_type_description_and_host_share():
  """One code on 2 of 4 hosts: "<type>: <description> (2/4 hosts)"."""
  hist = sentinel_host_histogram([_run("c1.x", -2), _run("c2.x", -2)])
  assert format_histogram_reason("host_ib", hist, 4) == (
    "host_ib: counter not present (2/4 hosts)"
  )


def test_histogram_reason_joins_codes_most_hosts_first():
  """Several codes: one part each, most hosts first, joined by "; "."""
  runs = [_run("c1.x", -3), _run("c2.x", -2), _run("c3.x", -2)]
  reason, codes = no_data_reason(
    schema={"host_ib": ["port_rcv_data"]},
    runs=runs,
    types=["host_ib"],
    events=["port_rcv_data"],
    n_hosts=4,
    legacy_reason="legacy",
    check_schema=True,
  )
  assert reason == (
    "host_ib: counter not present (2/4 hosts); host_ib: read failed (1/4 hosts)"
  )
  assert codes == {"codes": {"-2": 2, "-3": 1}}


def test_partial_device_runs_do_not_enter_the_host_histogram():
  """A run with valid devices left keeps the host's value; not a reason."""
  hist = sentinel_host_histogram([_run("c1.x", -3, n_dev=3, n_tot=64)])
  assert hist == {}


def test_compute_metrics_reason_from_side_table_and_codes(monkeypatch):
  """avg_blockbw whose block series was NOT_PRESENT on 2 of 4 hosts and
  absent elsewhere: histogram reason + codes from the side table."""
  table = table_from_records(
    _cpu_rows(),
    hosts=_HOSTS,
    schema=_SCHEMA,
    sentinel_runs=[
      _run(h, -2, typ="host_block", event=ev)
      for h in _HOSTS[:2]
      for ev in ("rd_sectors", "wr_sectors")
    ],
  )
  row = _compute(monkeypatch, table)["avg_blockbw"]
  assert row["value"] is None
  assert row["no_data_reason"] == "host_block: counter not present (2/4 hosts)"
  assert row["no_data_codes"] == {"codes": {"-2": 2}}


def test_compute_metrics_schema_absence_reason(monkeypatch):
  """No host_block in the schema: "host_block collector not found ..."."""
  table = table_from_records(_cpu_rows(), hosts=_HOSTS)
  row = _compute(monkeypatch, table)["avg_blockbw"]
  assert row["value"] is None
  assert row["no_data_reason"] == (
    "host_block collector not found on this node's monitor"
  )
  assert row["no_data_codes"] == {"codes": {}, "schema_absent": True}


def test_legacy_jobs_keep_generic_reasons(monkeypatch):
  """No sentinel runs and the types in the schema: today's reasons, no
  codes (simple and complex metrics)."""
  table = table_from_records(_cpu_rows(), hosts=_HOSTS, schema=_SCHEMA)
  out = _compute(monkeypatch, table, complex_=("max_packetrate",))
  assert out["avg_blockbw"]["value"] is None
  assert out["avg_blockbw"]["no_data_reason"] == metrics.NO_SIMPLE_SAMPLES_MSG
  assert out["avg_blockbw"]["no_data_codes"] is None
  assert out["max_packetrate"]["value"] is None
  assert (
    out["max_packetrate"]["no_data_reason"]
    == (metrics._COMPLEX_NO_DATA_REASONS["max_packetrate"])
  )
  assert out["max_packetrate"]["no_data_codes"] is None


def test_reason_capped_at_column_width_with_ellipsis():
  """A reason over 512 chars is cut to 512 and ends in the ellipsis."""
  hist = {-(n + 1): {f"h{n}"} for n in range(40)}
  reason = format_histogram_reason("host_ib", hist, 40)
  assert len(reason) == REASON_MAX_CHARS
  assert reason.endswith(REASON_ELLIPSIS)
  assert cap_reason("x" * REASON_MAX_CHARS) == "x" * REASON_MAX_CHARS


def test_no_data_codes_shape_is_persisted_with_reason():
  """``_persist_metrics_batch`` writes ``no_data_codes`` and updates it on
  conflict, next to ``no_data_reason``."""
  assert metrics.METRICS_CONTRACT_VERSION == 11
  rows = metrics._sanitize_metrics_compute_rows(
    [
      {
        "jid": "1",
        "type": "host_ib",
        "metric": "avg_ibbw",
        "units": "MB/s",
        "value": None,
        "no_data_reason": "host_ib: counter not present (1/1 hosts)",
        "no_data_codes": {"codes": {"-2": 1}},
      }
    ]
  )
  assert rows[0]["no_data_codes"] == {"codes": {"-2": 1}}
  conn = MagicMock()
  conn.vendor = "sqlite"
  with (
    patch.object(metrics, "connections", {"default": conn}),
    patch.object(metrics.transaction, "atomic"),
    patch.object(metrics, "metrics_data") as md,
  ):
    metrics._persist_metrics_batch(rows, None)
  kwargs = md.call_args.kwargs
  assert kwargs["no_data_codes"] == {"codes": {"-2": 1}}
  assert (
    "no_data_codes" in md.objects.bulk_create.call_args.kwargs["update_fields"]
  )


@pytest.mark.parametrize("check_schema", [True, False])
def test_vendor_cascade_metrics_do_not_claim_missing_collector(check_schema):
  """Only metrics that read their own type alone report schema absence."""
  reason, codes = no_data_reason(
    schema={"host_cpu": ["user"]},
    runs=[],
    types=["amd_x86_pmc"],
    events=None,
    n_hosts=1,
    legacy_reason="legacy",
    check_schema=check_schema,
  )
  assert (codes is not None) is check_schema
  assert (reason == "legacy") is not check_schema


def test_complex_metric_reason_ignores_earlier_fold_records(monkeypatch):
  """A simple metric's sentinel quality must not leak into the next
  (complex) metric's reason: the sink is cleared per metric."""
  from hpcperfstats.analysis.metrics.lib.sentinel_profile import FoldQuality

  table = table_from_records(_cpu_rows(), hosts=_HOSTS, schema=_SCHEMA)
  table.sentinel_sink.append(FoldQuality("host_ib", 4, excluded={"c1.x": -5}))
  out = _compute(monkeypatch, table, simple=(), complex_=("max_packetrate",))
  assert (
    out["max_packetrate"]["no_data_reason"]
    == (metrics._COMPLEX_NO_DATA_REASONS["max_packetrate"])
  )
  assert out["max_packetrate"]["no_data_codes"] is None
