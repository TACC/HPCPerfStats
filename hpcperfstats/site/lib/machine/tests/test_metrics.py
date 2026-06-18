"""Unit tests for analysis.metrics.metrics (_Schema, _EventIndex, _Host, avg_freq).

"""
import warnings

import numpy as np
import pytest
from pandas import Timestamp

from unittest.mock import MagicMock, patch

from hpcperfstats.analysis.metrics.lib.job_metric_display_labels import JOB_METRIC_SHORT_LABELS
from hpcperfstats.analysis.metrics.lib.gen.jid_table import JID_TABLE_HOST_QUERY_BATCH
from hpcperfstats.analysis.metrics.lib.metrics import (
    METRIC_NOT_COMPUTED_YET,
    Metrics,
    _EventIndex,
    _Host,
    _Schema,
    _host_data_metric_rows_batched,
    _jid_table_host_data_time_kwargs,
    avg_ethbw,
    avg_freq,
    avg_gpuutil,
    avg_packetsize,
    build_job_metrics_display_list,
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
    max_fabricbw,
    max_gpu_clock_event_reasons,
    max_gpu_power,
    max_mds,
    max_packetrate,
    mem_hwm,
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
  """avg_flops aggregation tries AMD PMC FLOPS then Intel FP_ARITH on core PMC types."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_pmc", "amd_x86_pmc"):
      return None
    if kw.get("typename") in ("intel_x86_pmc_gpr8", "intel_8pmc3"):
      return 2.5
    if kw.get("typename") in ("intel_x86_pmc_gpr4", "intel_4pmc3"):
      return 99.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 2.5) < 1e-9
  assert typename in ("intel_x86_pmc_gpr8", "intel_8pmc3")


def test_job_arc_avg_flops_uses_intel_4pmc3_when_8_empty():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_pmc", "amd_x86_pmc"):
      return None
    if kw.get("typename") in ("intel_x86_pmc_gpr8", "intel_8pmc3"):
      return None
    if kw.get("typename") in ("intel_x86_pmc_gpr4", "intel_4pmc3"):
      return 1.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 1.25) < 1e-9
  assert typename in ("intel_x86_pmc_gpr4", "intel_4pmc3")


def test_job_arc_avg_flops_uses_amd_when_present():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_pmc", "amd_x86_pmc"):
      return 9.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 9.0) < 1e-9
  assert typename in ("amd_x86_pmc", "amd64_pmc")




def test_job_arc_avg_flops_legacy_sse_when_fp_arith_missing():
  """avg_flops sums weighted SSE/AVX double proxies when FP_ARITH bundle has no data."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "amd64_pmc":
      return None
    typ = kw.get("typename")
    ev = kw.get("events") or []
    if typ in ("intel_x86_pmc_gpr8", "intel_8pmc3"):
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
  assert typename in ("intel_x86_pmc_gpr8", "intel_8pmc3")




def test_job_arc_avg_mbw_uses_intel_imc_when_amd_df_empty():
  """avg_mbw tries AMD DF then Intel IMC dram CAS read/write events."""

  cas_pairs = (
      ["dram_cas_reads", "dram_cas_writes"],
      ["CAS_READS", "CAS_WRITES"],
  )

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_df", "amd_x86_uncore_df"):
      return None
    if kw.get("typename") in ("intel_x86_uncore_imc_skx", "intel_skx_imc"):
      ev = kw.get("events") or []
      if list(ev) in cas_pairs:
        return 2.5
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 2.5) < 1e-9
  assert typename in ("intel_x86_uncore_imc_skx", "intel_skx_imc")


def test_job_arc_avg_mbw_prefers_amd64_df():
  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_df", "amd_x86_uncore_df"):
      return 0.75
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 0.75) < 1e-9
  assert typename in ("amd_x86_uncore_df", "amd64_df")


def test_job_arc_avg_flops_host_cpu_hw():
  """avg_flops uses host_cpu_hw ARM estimate when Intel PMC types lack FP data."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_pmc", "amd_x86_pmc"):
      return None
    if kw.get("typename") in (
        "intel_x86_pmc_gpr8",
        "intel_8pmc3",
        "intel_x86_pmc_gpr4",
        "intel_4pmc3",
    ):
      return None
    if kw.get("typename") in ("host_cpu_hw", "cpu_counter_metrics"):
      ev = kw.get("events") or []
      if ev == ["ARM_EST_FLOPS"] or ev == ["arm_est_flops"]:
        return 3.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 3.0) < 1e-9
  assert typename in ("host_cpu_hw", "cpu_counter_metrics")


def test_job_arc_avg_flops_arm_counter_fallback():
  """avg_flops falls back to host_cpu_hw arm_est_flops when FP events are absent."""

  def fake_job_arc(self, jt, **kw):
    ev = list(kw.get("events") or [])
    if kw.get("typename") in ("host_cpu_hw", "cpu_counter_metrics") and ev in (
        ["arm_est_flops"],
        ["ARM_EST_FLOPS"],
    ):
      return 4.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops(object())
  assert abs(value - 4.25) < 1e-9
  assert typename in ("host_cpu_hw", "cpu_counter_metrics")


def test_job_arc_avg_mbw_arm_counter_fallback():
  """avg_mbw falls back to host_cpu_hw ARM_DRAM_BW_BYTES when IMC rows are absent."""

  def fake_job_arc(self, jt, **kw):
    ev = list(kw.get("events") or [])
    if kw.get("typename") in ("host_cpu_hw", "cpu_counter_metrics") and ev == [
        "ARM_DRAM_BW_BYTES"
    ]:
      return 6.5
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    with patch(
        "hpcperfstats.analysis.metrics.lib.metrics.imc_types_probe_order",
        return_value=(),
    ), patch(
        "hpcperfstats.analysis.metrics.lib.metrics.arm_imc_types_probe_order",
        return_value=(),
    ):
      m = Metrics()
      value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 6.5) < 1e-9
  assert typename in ("host_cpu_hw", "cpu_counter_metrics")


def test_job_arc_avg_sharedfs_iops_includes_nfs_when_available():
  """avg_sharedfs_iops sums llite and nfs operation counters when both exist."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "llite":
      return 20.0
    if kw.get("typename") == "nfs":
      return 5.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_sharedfs_iops(object())
  assert abs(value - 25.0) < 1e-9
  assert typename == "llite"


def test_job_arc_avg_sharedfs_bw_falls_back_to_nfs():
  """avg_sharedfs_bw uses nfs byte counters when llite counters are absent."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") == "llite":
      return None
    if kw.get("typename") == "nfs":
      return 7.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_sharedfs_bw(object())
  assert abs(value - 7.0) < 1e-9
  assert typename == "nfs"


def test_job_arc_avg_ibbw_falls_back_to_ethernet():
  """avg_ibbw uses net rx/tx bytes when host_ib/opa are unavailable."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("host_ib", "opa"):
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
      if typename in ("host_ib", "opa"):
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
      if typename in ("host_ib", "opa"):
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
      if typename in ("host_ib", "opa"):
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


@pytest.mark.machine_unit_mock
def test_max_gpu_clock_event_reasons_all_nan_returns_none():
  """All-NaN clocks_event_reasons must not raise int(NaN) during metrics compute."""
  schema = _Schema(["clocks_event_reasons"])
  stats = np.array([[np.nan], [np.nan]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_gpu_clock_event_reasons().compute_metric(MockU())
  assert value is None
  assert typename == "nvidia_gpu"
  assert units == "#"


@pytest.mark.machine_unit_mock
def test_max_gpu_clock_event_reasons_skips_nan_hosts_uses_finite_max():
  """Finite clocks_event_reasons on one host win when another host is all-NaN."""
  schema = _Schema(["clocks_event_reasons"])
  nan_stats = np.array([[np.nan], [np.nan]], dtype=np.float64)
  good_stats = np.array([[0.0], [7.0], [3.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"nan_host": nan_stats, "good_host": good_stats}
      return None, {}

  value, typename, units = max_gpu_clock_event_reasons().compute_metric(MockU())
  assert value == 7.0
  assert typename == "nvidia_gpu"
  assert units == "#"


@pytest.mark.machine_unit_mock
def test_max_gpu_power_all_nan_returns_none():
  schema = _Schema(["power_usage"])
  stats = np.array([[np.nan], [np.nan]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_gpu_power().compute_metric(MockU())
  assert value is None
  assert units == "W"


@pytest.mark.machine_unit_mock
def test_max_gpu_power_skips_nan_hosts_uses_finite_max():
  schema = _Schema(["power_usage"])
  nan_stats = np.array([[np.nan]], dtype=np.float64)
  good_stats = np.array([[10.0], [250.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"nan_host": nan_stats, "good_host": good_stats}
      return None, {}

  value, typename, units = max_gpu_power().compute_metric(MockU())
  assert value == pytest.approx(250.0)
  assert typename == "nvidia_gpu"


@pytest.mark.machine_unit_mock
def test_mem_hwm_all_nan_returns_none():
  schema = _Schema(["MemUsed", "Slab", "FilePages"])
  stats = np.array([[np.nan, np.nan, np.nan]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "mem":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = mem_hwm().compute_metric(MockU())
  assert value is None
  assert units == "GiB"


@pytest.mark.machine_unit_mock
def test_mem_hwm_mixed_nan_uses_finite_peak():
  schema = _Schema(["MemUsed", "Slab", "FilePages"])
  nan_stats = np.array([[np.nan, np.nan, np.nan]], dtype=np.float64)
  gi = 2**30
  good_stats = np.array([[float(gi), 0.0, 0.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "mem":
        return schema, {"nan_host": nan_stats, "good_host": good_stats}
      return None, {}

  value, typename, units = mem_hwm().compute_metric(MockU())
  assert value == pytest.approx(1.0)
  assert units == "GiB"


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


def test_avg_ethbw_skips_hosts_with_negative_byte_delta():
  """Hosts whose rx+tx counters net decrease (reset/bad data) are omitted from the mean."""
  schema = _Schema(["rx_bytes", "tx_bytes"])
  bad = np.array([[1000.0, 0.0], [400.0, 0.0]], dtype=np.float64)
  good = np.array([[0.0, 0.0], [100.0, 0.0]], dtype=np.float64)
  delta_t = 10.0
  denom = delta_t * 1024 * 1024

  class MockU:
    dt = delta_t
    nhosts = 2

    def get_type(self, typename):
      return schema, {"bad": bad, "good": good}

  u = MockU()
  value, typename, units = avg_ethbw().compute_metric(u)
  expected = 100.0 / denom
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


def test_gpu_precision_activity_metrics_are_registered_with_expected_events():
  """GPU precision activity metrics should be cataloged from monitor fp*_active fields."""
  m = Metrics()
  assert m.simple_metrics_list["avg_fp16_active"]["events"] == ["fp16_active"]
  assert m.simple_metrics_list["avg_fp32_active"]["events"] == ["fp32_active"]
  assert m.simple_metrics_list["avg_fp64_active"]["events"] == ["fp64_active"]
  metrics = {entry["metric"] for entry in job_metrics_catalog_entries()}
  assert {"avg_fp16_active", "avg_fp32_active", "avg_fp64_active"} <= metrics


def test_job_metrics_catalog_entries_matches_simple_plus_complex():
  """Catalog length is all simple + complex metric names (API / update_metrics)."""
  entries = job_metrics_catalog_entries()
  assert len(entries) == expected_job_metric_row_count()
  names = [e["metric"] for e in entries]
  assert names.count("avg_cpuusage") == 1
  assert "mem_hwm" in names


def test_job_metric_short_labels_cover_catalog():
  """Every catalog metric has a Job detail short label (JS mirror must stay in sync)."""
  for entry in job_metrics_catalog_entries():
    assert entry["metric"] in JOB_METRIC_SHORT_LABELS, entry["metric"]


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


def test_build_job_metrics_display_list_keeps_empty_reason_ahead_of_not_computed():
  """Blank DB reason still counts as a present row and must not be sorted to the tail."""
  first = job_metrics_catalog_entries()[0]
  row = MagicMock()
  row.metric = first["metric"]
  row.type = first["type"]
  row.units = first["units"]
  row.value = None
  row.no_data_reason = ""
  job = MagicMock()
  job.metrics_data_set.all.return_value = [row]

  out = build_job_metrics_display_list(job)
  assert out[0]["metric"] == first["metric"]
  assert out[0]["no_data_reason"] == ""
  assert out[1]["no_data_reason"] == METRIC_NOT_COMPUTED_YET


def test_metrics_run_uses_supplied_pool(monkeypatch):
  """Metrics.run should use caller-supplied pool and not manage lifecycle."""
  m = Metrics()
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._persist_metrics_batch",
      lambda rows, distinct_n, **kwargs: None,
  )
  monkeypatch.setattr(
      m,
      "_worker_process_count",
      lambda: 4,
  )

  class FakePool:
    def __init__(self):
      self.chunksize = None
      self.closed = False
      self.joined = False

    def imap_unordered(self, fn, args_iter, chunksize=1):
      self.chunksize = chunksize
      _ = list(args_iter)
      return iter([{"rows": [{"jid": MagicMock(jid=1)}], "distinct_time_count": 1}])

    def close(self):
      self.closed = True

    def join(self):
      self.joined = True

  pool = FakePool()
  m.run([MagicMock(jid=1), MagicMock(jid=2)], pool=pool)
  assert pool.chunksize == 1
  assert pool.closed is False
  assert pool.joined is False


def test_jid_table_host_data_time_kwargs_full_and_sampled():
  assert _jid_table_host_data_time_kwargs({}) is None
  assert _jid_table_host_data_time_kwargs({"host__in": []}) is None
  assert _jid_table_host_data_time_kwargs({
      "time__in": [1, 2],
      "host__in": ["x"],
  }) == {"time__in": [1, 2]}
  assert _jid_table_host_data_time_kwargs({
      "time__gte": 1,
      "time__lte": 2,
      "host__in": ["x"],
  }) == {"time__gte": 1, "time__lte": 2}


def test_job_arc_uses_time__in_when_jid_table_large_job_sampled():
  """Large-job jid_table uses time__in; job_arc must not require time__gte/time__lte."""
  from types import SimpleNamespace

  from django.utils import timezone as django_tz

  t1 = django_tz.now()
  jt = SimpleNamespace(
      _base_filter={
          "time__in": [t1],
          "host__in": ["n.example.com"],
      }
  )
  m = Metrics()
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects") as mock_objects:
    mock_objects.filter.return_value.values.return_value.order_by.return_value = []
    m.job_arc(jt, typename="net", events=["rx_bytes"], conv=1.0)
    kwargs = mock_objects.filter.call_args.kwargs
    assert kwargs["time__in"] == [t1]
    assert "time__gte" not in kwargs
    assert "time__lte" not in kwargs


@pytest.mark.django_db(databases=[])
def test_host_data_metric_rows_batched_splits_host__in():
  """Large host lists query host_data in jid_table-sized batches."""

  n = JID_TABLE_HOST_QUERY_BATCH + 2
  hosts = ["h{0}.x".format(i) for i in range(n)]
  chunk_sizes = []

  class Qs:
    def values(self, *cols):
      return self

    def order_by(self, *args):
      chunk_sizes.append(len(self._hosts))
      return []

  class Mgr:
    def filter(self, **kwargs):
      q = Qs()
      q._hosts = list(kwargs.get("host__in") or [])
      return q

  tkw = {"time__gte": 1, "time__lte": 2}
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", Mgr()):
    rows = _host_data_metric_rows_batched(
        tkw, hosts, "net", ["rx_bytes"], "arc")
  assert rows == []
  assert chunk_sizes == [JID_TABLE_HOST_QUERY_BATCH, 2]


@pytest.mark.django_db(databases=[])
def test_host_data_metric_rows_batched_rows_cache_reuses_fetch():
  """Same (tkw, typename, events, column) in one compute_metrics pass hits cache."""
  chunk_passes = []

  class Qs:
    def values(self, *cols):
      return self

    def order_by(self, *args):
      chunk_passes.append(1)
      return [{"host": "h1", "time": 1, "arc": 2.0}]

  class Mgr:
    def filter(self, **kwargs):
      return Qs()

  tkw = {"time__gte": 1, "time__lte": 2}
  cache = {}
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", Mgr()):
    r1 = _host_data_metric_rows_batched(
        tkw, ["h1"], "net", ["rx_bytes"], "arc", rows_cache=cache)
    r2 = _host_data_metric_rows_batched(
        tkw, ["h1"], "net", ["rx_bytes"], "arc", rows_cache=cache)
  assert r1 == r2
  assert len(chunk_passes) == 1


@pytest.mark.django_db(databases=[])
def test_job_arc_issues_multiple_queries_when_many_hosts(monkeypatch):
  from types import SimpleNamespace

  from django.utils import timezone as django_tz

  n = JID_TABLE_HOST_QUERY_BATCH + 1
  hosts = ["h{0}.x".format(i) for i in range(n)]
  t0 = django_tz.now()
  jt = SimpleNamespace(
      _base_filter={
          "time__gte": t0,
          "time__lte": t0,
          "host__in": hosts,
      }
  )
  calls = []

  class Qs:
    def values(self, *cols):
      return self

    def order_by(self, *args):
      calls.append(len(self._hosts))
      return []

  class Mgr:
    def filter(self, **kwargs):
      q = Qs()
      q._hosts = list(kwargs.get("host__in") or [])
      return q

  monkeypatch.setattr("hpcperfstats.site.lib.machine.models.host_data.objects", Mgr())
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typename: (typename,),
  )
  m = Metrics()
  m.job_arc(jt, typename="net", events=["rx_bytes"], conv=1.0)
  assert len(calls) == 2
  assert calls[0] == JID_TABLE_HOST_QUERY_BATCH
  assert calls[1] == 1
