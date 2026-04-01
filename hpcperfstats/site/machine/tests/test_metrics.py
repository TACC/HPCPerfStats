"""Unit tests for analysis.metrics.metrics (_Schema, _EventIndex, _Host, avg_freq).

"""
import warnings

import numpy as np
import pytest
from pandas import Timestamp

from unittest.mock import MagicMock, patch

from hpcperfstats.analysis.metrics.metrics import (
    METRIC_NOT_COMPUTED_YET,
    Metrics,
    _EventIndex,
    _Host,
    _Schema,
    avg_ethbw,
    avg_freq,
    avg_gpuutil,
    avg_packetsize,
    build_job_metrics_display_list,
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
    max_fabricbw,
    max_mds,
    max_packetrate,
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


def test_avg_freq_aperf_mperf_path():
  """avg_freq uses APERF/MPERF when fixed counter names are absent (Intel/AMD PMC)."""
  schema = _Schema(["APERF", "MPERF", "INST_RETIRED"])
  stats = np.array([[0.0, 0.0, 0.0], [200.0, 100.0, 50.0]], dtype=np.float64)

  class MockU:
    freq = 2.5

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert abs(value - 5.0) < 1e-9
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_aperf_mperf_returns_none_without_freq():
  """APERF/MPERF branch requires u.freq (nominal reference GHz)."""
  schema = _Schema(["APERF", "MPERF"])
  stats = np.array([[0.0, 0.0], [100.0, 50.0]], dtype=np.float64)

  class MockU:
    freq = None

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is None
  assert typename == "pmc"
  assert units == "GHz"


def test_job_arc_avg_flops_falls_back_to_intel_when_amd_missing():
  """avg_flops aggregation tries amd64_pmc FLOPS then intel_8pmc3 FP_ARITH sum."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return None
    if kw.get("typename") == "intel_8pmc3":
      return 2.5
    if kw.get("typename") == "intel_4pmc3":
      return 99.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 2.5) < 1e-9
  assert typename == "intel_8pmc3"


def test_job_arc_avg_flops_uses_intel_4pmc3_when_8_empty():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return None
    if kw.get("typename") == "intel_8pmc3":
      return None
    if kw.get("typename") == "intel_4pmc3":
      return 1.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 1.25) < 1e-9
  assert typename == "intel_4pmc3"


def test_job_arc_avg_flops_uses_amd_when_present():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return 9.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 9.0) < 1e-9
  assert typename == "amd64_pmc"




def test_job_arc_avg_flops_legacy_sse_when_fp_arith_missing():
  """avg_flops sums weighted SSE/AVX double proxies when FP_ARITH bundle has no data."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return None
    typ = kw.get("typename")
    ev = kw.get("events") or []
    if typ == "intel_8pmc3":
      if len(ev) > 1:
        return None
      if ev == ["SSE_DOUBLE_SCALAR"]:
        return 1.0
      if ev == ["SSE_DOUBLE_PACKED"]:
        return 0.5
      if ev == ["SIMD_DOUBLE_256"]:
        return 0.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 1.75) < 1e-9
  assert typename == "intel_8pmc3"




def test_job_arc_avg_mbw_uses_intel_imc_when_amd_df_empty():
  """avg_mbw tries AMD DF then Intel IMC CAS_READS+CAS_WRITES."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_df":
      return None
    if kw.get("typename") == "intel_skx_imc":
      ev = kw.get("events") or []
      if list(ev) == ["CAS_READS", "CAS_WRITES"]:
        return 2.5
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    with patch(
        "hpcperfstats.analysis.metrics.metrics.INTEL_IMC_STATS_TYPES",
        ("intel_skx_imc",),
    ):
      m = Metrics()
      value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 2.5) < 1e-9
  assert typename == "intel_skx_imc"


def test_job_arc_avg_mbw_prefers_amd64_df():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_df":
      return 0.75
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 0.75) < 1e-9
  assert typename == "amd64_df"


def test_job_arc_avg_flops_cpu_counter_metrics():
  """avg_flops uses cpu_counter_metrics when Intel PMC types lack FP data."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return None
    if kw.get("typename") in ("intel_8pmc3", "intel_4pmc3"):
      return None
    if kw.get("typename") == "cpu_counter_metrics":
      if len(kw.get("events") or []) > 1:
        return 3.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 3.0) < 1e-9
  assert typename == "cpu_counter_metrics"


def test_job_arc_avg_flops_arm_counter_fallback():
  """avg_flops falls back to cpu_counter_metrics ARM_EST_FLOPS when FP events are absent."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "cpu_counter_metrics" and list(kw.get("events") or []) == ["ARM_EST_FLOPS"]:
      return 4.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 4.25) < 1e-9
  assert typename == "cpu_counter_metrics"


def test_job_arc_avg_mbw_arm_counter_fallback():
  """avg_mbw falls back to cpu_counter_metrics ARM_DRAM_BW_BYTES when IMC rows are absent."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "cpu_counter_metrics" and list(kw.get("events") or []) == ["ARM_DRAM_BW_BYTES"]:
      return 6.5
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    with patch(
        "hpcperfstats.analysis.metrics.metrics.INTEL_IMC_STATS_TYPES",
        (),
    ), patch(
        "hpcperfstats.analysis.metrics.metrics.ARM_IMC_STATS_TYPES",
        (),
    ):
      m = Metrics()
      value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 6.5) < 1e-9
  assert typename == "cpu_counter_metrics"


def test_job_arc_avg_lustreiops_includes_nfs_when_available():
  """avg_lustreiops sums llite and nfs operation counters when both exist."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "llite":
      return 20.0
    if kw.get("typename") == "nfs":
      return 5.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_lustreiops(object())
  assert abs(value - 25.0) < 1e-9
  assert typename == "llite"


def test_job_arc_avg_lustrebw_falls_back_to_nfs():
  """avg_lustrebw uses nfs byte counters when llite counters are absent."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "llite":
      return None
    if kw.get("typename") == "nfs":
      return 7.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_lustrebw(object())
  assert abs(value - 7.0) < 1e-9
  assert typename == "nfs"


def test_job_arc_avg_ibbw_falls_back_to_ethernet():
  """avg_ibbw uses net rx/tx bytes when ib_ext/opa are unavailable."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("ib_ext", "opa"):
      return None
    if kw.get("typename") == "net":
      return 12.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_ibbw(object())
  assert value == pytest.approx(12.0)
  assert typename == "net"


def test_max_mds_uses_nfs_ops_when_llite_missing():
  """max_mds falls back to nfs READ_ops/WRITE_ops when llite telemetry is missing."""
  schema_nfs = _Schema(["READ_ops", "WRITE_ops"])
  stats = np.array(
      [[0.0, 0.0], [100.0, 20.0], [220.0, 40.0]],
      dtype=np.float64,
  )

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename == "llite":
        return None, {}
      if typename == "nfs":
        return schema_nfs, {"h1": stats}
      return None, {}

  value, typename, units = max_mds().compute_metric(MockU())
  assert value is not None
  assert value == pytest.approx(14.0)
  assert typename == "llite"
  assert units == "iops"


def test_avg_packetsize_falls_back_to_ethernet_packets():
  """avg_packetsize uses net bytes/packets when IB and OPA are absent."""
  schema = _Schema(["tx_packets", "rx_packets", "tx_bytes", "rx_bytes"])
  stats = np.array([[0.0, 0.0, 0.0, 0.0], [100.0, 100.0, 1000.0, 1000.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename in ("ib_ext", "opa"):
        return None, {}
      if typename == "net":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = avg_packetsize().compute_metric(MockU())
  expected = 2000.0 / (200.0 * 1024 * 1024)
  assert value == pytest.approx(expected)
  assert typename == "net"
  assert units == "MB"


def test_max_fabricbw_falls_back_to_ethernet():
  """max_fabricbw uses net tx/rx byte rate when IB and OPA are absent."""
  schema = _Schema(["tx_bytes", "rx_bytes"])
  stats = np.array([[0.0, 0.0], [100.0, 50.0], [300.0, 150.0]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename in ("ib_ext", "opa"):
        return None, {}
      if typename == "net":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_fabricbw().compute_metric(MockU())
  # max interval bytes/s = (200+100)/10 = 30, /MiB
  assert value == pytest.approx(30.0 / (1024 * 1024))
  assert typename == "net"
  assert units == "MB/s"


def test_max_packetrate_falls_back_to_ethernet():
  """max_packetrate uses net tx/rx packet rate when IB and OPA are absent."""
  schema = _Schema(["tx_packets", "rx_packets"])
  stats = np.array([[0.0, 0.0], [100.0, 20.0], [220.0, 40.0]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename in ("ib_ext", "opa"):
        return None, {}
      if typename == "net":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_packetrate().compute_metric(MockU())
  assert value == pytest.approx(14.0)
  assert typename == "net"
  assert units == "#/s"


def test_avg_gpuutil_amd_gpu_uses_gpu_util_column():
  """avg_gpuutil reads amd_gpu.gpu_util when nvidia is absent."""
  schema = _Schema(["gpu_util"])
  stats = np.array([[0.0], [60.0], [80.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return None, {}
      if typename == "amd_gpu":
        return schema, {"h1": stats}
      return None, {}

  u = MockU()
  value, typename, units = avg_gpuutil().compute_metric(u)
  assert typename == "amd_gpu"
  assert units == "%"
  assert value == pytest.approx(60.0)


def test_avg_gpuutil_nvidia_takes_precedence_over_amd():
  schema_n = _Schema(["gpu_util"])
  schema_a = _Schema(["gpu_util"])
  sn = np.array([[0.0], [20.0], [40.0]], dtype=np.float64)
  sa = np.array([[0.0], [99.0], [99.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema_n, {"h1": sn}
      if typename == "amd_gpu":
        return schema_a, {"h1": sa}
      return None, {}

  u = MockU()
  value, typename, units = avg_gpuutil().compute_metric(u)
  assert typename == "nvidia_gpu"
  assert value == pytest.approx(20.0)


def test_avg_gpuutil_nvidia_prefers_gpu_util_over_legacy_utilization():
  """When both columns exist, use gpu_util (monitor) not utilization (legacy)."""
  schema_n = _Schema(["gpu_util", "utilization"])
  sn = np.array([[0.0, 0.0], [10.0, 99.0], [20.0, 99.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema_n, {"h1": sn}
      return None, {}

  u = MockU()
  value, typename, units = avg_gpuutil().compute_metric(u)
  assert typename == "nvidia_gpu"
  assert units == "%"
  assert value == pytest.approx(10.0)


def test_avg_gpuutil_nvidia_legacy_utilization_only():
  """Older archives with only utilization still compute avg_gpuutil."""
  schema = _Schema(["utilization"])
  stats = np.array([[0.0], [30.0], [50.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  u = MockU()
  value, typename, units = avg_gpuutil().compute_metric(u)
  assert typename == "nvidia_gpu"
  assert value == pytest.approx(30.0)


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


def test_avg_gpuutil_returns_none_for_short_series_without_warning():
  """avg_gpuutil skips hosts with <3 samples (empty trimmed window) and returns None."""
  schema = _Schema(["gpu_util"])
  short_stats = np.array([[10.0], [20.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      return schema, {"h1": short_stats}

  u = MockU()
  with warnings.catch_warnings(record=True) as captured:
    warnings.simplefilter("always")
    value, typename, units = avg_gpuutil().compute_metric(u)
  assert value is None
  assert typename == "gpu"
  assert units == "%"
  assert len(captured) == 0


def test_job_metrics_catalog_avg_gpuutil_placeholder_type_is_gpu():
  entries = job_metrics_catalog_entries()
  gpu = next(e for e in entries if e["metric"] == "avg_gpuutil")
  assert gpu["type"] == "gpu"


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
