"""Unit tests for analysis.metrics.metrics (_Schema, _EventIndex, _Host, avg_freq).

"""
import numpy as np
import pytest
from pandas import Timestamp

from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.metrics import (
    METRIC_NOT_COMPUTED_YET,
    _EventIndex,
    _Host,
    _Schema,
    avg_ethbw,
    avg_freq,
    build_job_metrics_display_list,
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
)


def test_event_index_stores_index():
  """_EventIndex stores and exposes .index."""
  idx = _EventIndex(3)
  assert idx.index == 3


def test_schema_events_and_desc():
  """_Schema builds events list and desc from event names."""
  events = ["a", "b", "c"]
  schema = _Schema(events)
  assert schema.events == ["a", "b", "c"]
  assert schema.desc == "a b c\n"


def test_schema_accepts_non_string_events():
  """_Schema normalises non‑string event labels (e.g. pandas.Timestamp) to str."""
  ts1 = Timestamp("2020-01-01T00:00:00Z")
  ts2 = Timestamp("2020-01-01T01:00:00Z")
  events = [ts1, ts2]
  schema = _Schema(events)
  assert schema.events == [str(ts1), str(ts2)]
  assert schema.desc == f"{ts1} {ts2}\n"


def test_schema_getitem_returns_event_index():
  """_Schema.__getitem__ returns _EventIndex with correct index."""
  schema = _Schema(["x", "y", "z"])
  ei = schema["y"]
  assert isinstance(ei, _EventIndex)
  assert ei.index == 1


def test_schema_getitem_raises_for_unknown_event():
  """_Schema.__getitem__ raises KeyError for unknown event."""
  schema = _Schema(["a", "b"])
  with pytest.raises(KeyError):
    schema["c"]


def test_host_starts_with_empty_stats():
  """_Host has empty stats dict by default."""
  h = _Host()
  assert h.stats == {}


def test_avg_freq_returns_none_when_no_pmc():
  """avg_freq.compute_metric returns (None, typename, units) when get_type('pmc') has no schema."""
  class MockU:
    def get_type(self, typename):
      return None, {}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is None
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_compute_metric():
  """avg_freq.compute_metric returns (value, typename, units) from cycles and freq."""
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "CLOCKS_UNHALTED_REF"])
  stats = np.array([[0.0, 0.0], [100.0, 200.0]], dtype=np.float64)

  class MockU:
    freq = 2.5

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is not None
  assert abs(value - 1.25) < 1e-9
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_returns_none_when_cycles_ref_zero():
  """avg_freq.compute_metric returns None when cycles_ref is zero."""
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "CLOCKS_UNHALTED_REF"])
  stats = np.array([[0.0, 0.0], [100.0, 0.0]], dtype=np.float64)

  class MockU:
    freq = 2.5

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is None
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_mean_across_hosts():
  """avg_freq averages each host's implied frequency, then mean across hosts."""
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "CLOCKS_UNHALTED_REF"])
  h1 = np.array([[0.0, 0.0], [100.0, 200.0]], dtype=np.float64)
  h2 = np.array([[0.0, 0.0], [50.0, 100.0]], dtype=np.float64)

  class MockU:
    freq = 2.0

    def get_type(self, typename):
      return schema, {"a": h1, "b": h2}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  # host1: 2*100/200=1.0, host2: 2*50/100=1.0 -> mean 1.0
  assert abs(value - 1.0) < 1e-9
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_ethbw_mean_across_hosts():
  """avg_ethbw is mean of per-host (rx+tx delta)/(dt * 1MiB), not pooled sum/nhosts."""
  schema = _Schema(["rx_bytes", "tx_bytes"])
  s_lo = np.array([[0.0, 0.0], [100.0, 0.0]], dtype=np.float64)
  s_hi = np.array([[0.0, 0.0], [300.0, 0.0]], dtype=np.float64)
  delta_t = 10.0
  denom = delta_t * 1024 * 1024

  class MockU:
    dt = delta_t
    nhosts = 2

    def get_type(self, typename):
      return schema, {"h1": s_lo, "h2": s_hi}

  u = MockU()
  value, typename, units = avg_ethbw().compute_metric(u)
  expected = float((100.0 / denom + 300.0 / denom) / 2.0)
  assert abs(value - expected) < 1e-9
  assert typename == "net"
  assert units == "MB/s"


def test_job_metrics_catalog_entries_matches_simple_plus_complex():
  """Catalog length is all simple + complex metric names (API / update_metrics)."""
  entries = job_metrics_catalog_entries()
  assert len(entries) == expected_job_metric_row_count()
  assert len(entries) == 22
  names = [e["metric"] for e in entries]
  assert names.count("avg_cpuusage") == 1
  assert "mem_hwm" in names


def test_build_job_metrics_display_list_fills_catalog_when_no_rows():
  """With no DB rows, every catalog metric gets METRIC_NOT_COMPUTED_YET."""
  job = MagicMock()
  job.metrics_data_set.all.return_value = []
  out = build_job_metrics_display_list(job)
  assert len(out) == len(job_metrics_catalog_entries())
  assert all(item["no_data_reason"] == METRIC_NOT_COMPUTED_YET for item in out)


def test_build_job_metrics_display_list_merges_existing_row():
  """DB row for a metric name overrides catalog placeholder."""
  row = MagicMock()
  row.metric = "avg_cpuusage"
  row.type = "cpu"
  row.units = "#cores"
  row.value = 2.25
  row.no_data_reason = None
  job = MagicMock()
  job.metrics_data_set.all.return_value = [row]
  out = build_job_metrics_display_list(job)
  cpu = next(x for x in out if x["metric"] == "avg_cpuusage")
  assert cpu["value"] == 2.25
  assert cpu["type"] == "cpu"
  assert cpu["no_data_reason"] is None


def test_build_job_metrics_display_list_shows_no_data_reason():
  row = MagicMock()
  row.metric = "mem_hwm"
  row.type = "mem"
  row.units = "GiB"
  row.value = None
  row.no_data_reason = "No usable memory telemetry for high-water mark"
  job = MagicMock()
  job.metrics_data_set.all.return_value = [row]
  out = build_job_metrics_display_list(job)
  mem = next(x for x in out if x["metric"] == "mem_hwm")
  assert mem["value"] is None
  assert "memory" in mem["no_data_reason"].lower()


def test_build_job_metrics_display_list_puts_not_computed_yet_last():
  """Rows with METRIC_NOT_COMPUTED_YET follow all rows with DB data or other reasons."""
  entries = job_metrics_catalog_entries()
  first = entries[0]
  row = MagicMock()
  row.metric = first["metric"]
  row.type = first["type"]
  row.units = first["units"]
  row.value = 1.0
  row.no_data_reason = None
  job = MagicMock()
  job.metrics_data_set.all.return_value = [row]
  out = build_job_metrics_display_list(job)
  flags = [item["no_data_reason"] == METRIC_NOT_COMPUTED_YET for item in out]
  first_pending = next((i for i, pending in enumerate(flags) if pending), len(out))
  assert first_pending == 1
  assert not any(flags[:first_pending])
  assert all(flags[first_pending:])
  assert out[0]["metric"] == first["metric"]
  assert out[0]["value"] == 1.0
