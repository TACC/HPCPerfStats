"""Unit tests for analysis.metrics.metrics (_Schema, _EventIndex, _Host, avg_freq).

"""
import warnings

import numpy as np
import pytest
from pandas import Timestamp

from unittest.mock import MagicMock, patch

from hpcperfstats.analysis.metrics.lib.gen import jid_table
from hpcperfstats.analysis.metrics.lib.job_metric_display_labels import JOB_METRIC_SHORT_LABELS
from hpcperfstats.analysis.metrics.lib.metrics import (
    METRIC_NOT_COMPUTED_YET,
    METRICS_HOST_QUERY_BATCH,
    Metrics,
    _EventIndex,
    _Host,
    _Schema,
    _host_data_metric_rows_batched,
    _host_data_row_cache_key,
    _jid_table_host_data_time_kwargs,
    _metric_type_events_feasible,
    avg_ethbw,
    avg_freq,
    avg_gpuutil,
    avg_packetsize,
    build_job_metrics_display_list,
    expected_job_metric_row_count,
    job_metrics_catalog_entries,
    max_fabricbw,
    max_gpu_clock_event_reasons,
    max_gpu_link_gbps,
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




@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
def test_job_arc_avg_mbw_spr_hbm_cas_only():
  """avg_mbw uses SPR hbm_cas_* when dram_cas aggregates are empty."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_df", "amd_x86_uncore_df"):
      return None
    if kw.get("typename") == "intel_x86_uncore_imc_spr":
      ev = list(kw.get("events") or [])
      if ev == ["hbm_cas_reads", "hbm_cas_writes"]:
        return 3.25
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 3.25) < 1e-9
  assert typename == "intel_x86_uncore_imc_spr"


@pytest.mark.machine_unit_mock
def test_job_arc_avg_mbw_spr_sums_dram_and_hbm_cas():
  """avg_mbw sums SPR dram_cas and hbm_cas scalars when both resolve."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("amd64_df", "amd_x86_uncore_df"):
      return None
    if kw.get("typename") == "intel_x86_uncore_imc_spr":
      ev = list(kw.get("events") or [])
      if ev == ["dram_cas_reads", "dram_cas_writes"]:
        return 1.0
      if ev == ["hbm_cas_reads", "hbm_cas_writes"]:
        return 2.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_mbw(object())
  assert abs(value - 3.0) < 1e-9
  assert typename == "intel_x86_uncore_imc_spr"


@pytest.mark.machine_unit_mock
def test_dram_bw_weighted_events_include_hbm_when_present():
  from hpcperfstats.analysis.metrics.lib.metrics import (
      _dram_bw_weighted_events_for_imbalance,
  )

  class _Schema(list):
    pass

  class MockU:
    imc = "intel_x86_uncore_imc_spr"

    def get_type(self, typename):
      del typename
      return None, {}

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics.resolve_get_type",
      return_value=(
          _Schema(
              [
                  "hbm_cas_reads",
                  "hbm_cas_writes",
              ]
          ),
          {"h1": object()},
          "intel_x86_uncore_imc_spr",
      ),
  ):
    typ, weighted = _dram_bw_weighted_events_for_imbalance(MockU())
  assert typ == "intel_x86_uncore_imc_spr"
  assert [e for e, _w in weighted] == ["hbm_cas_reads", "hbm_cas_writes"]


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
def test_job_arc_avg_flops_precision_grace_scalar_fallback():
  """avg_flops64b/32b use Grace scalar events when Intel FP_ARITH is absent."""

  def fake_job_arc(self, jt, **kw):
    typ = kw.get("typename")
    ev = list(kw.get("events") or [])
    if typ in ("host_cpu_hw", "cpu_counter_metrics"):
      if ev == ["fp_arith_inst_retired_scalar_double"]:
        return 2.5
      if ev == ["fp_arith_inst_retired_scalar_single"]:
        return 7.5
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    v64, t64 = m._job_arc_avg_flops_precision(
        object(),
        list(m.simple_metrics_list["avg_flops64b"]["events"]),
        grace_scalar_events=("fp_arith_inst_retired_scalar_double",),
    )
    v32, t32 = m._job_arc_avg_flops_precision(
        object(),
        list(m.simple_metrics_list["avg_flops32b"]["events"]),
        grace_scalar_events=("fp_arith_inst_retired_scalar_single",),
    )
  assert abs(v64 - 2.5) < 1e-9
  assert abs(v32 - 7.5) < 1e-9
  assert t64 in ("host_cpu_hw", "cpu_counter_metrics")
  assert t32 in ("host_cpu_hw", "cpu_counter_metrics")


@pytest.mark.machine_unit_mock
def test_job_arc_avg_flops_precision_prefers_intel_over_grace():
  """Intel FP_ARITH wins for precision metrics even when Grace scalars exist."""

  def fake_job_arc(self, jt, **kw):
    typ = kw.get("typename")
    ev = list(kw.get("events") or [])
    if typ in ("intel_x86_pmc_gpr8", "intel_8pmc3") and "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE" in ev:
      return 9.0
    if typ in ("host_cpu_hw", "cpu_counter_metrics") and ev == [
        "fp_arith_inst_retired_scalar_double"
    ]:
      return 1.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_flops_precision(
        object(),
        list(m.simple_metrics_list["avg_flops64b"]["events"]),
        grace_scalar_events=("fp_arith_inst_retired_scalar_double",),
    )
  assert abs(value - 9.0) < 1e-9
  assert typename in ("intel_x86_pmc_gpr8", "intel_8pmc3")


@pytest.mark.machine_unit_mock
def test_job_metrics_catalog_includes_avg_arm_int_ops():
  metrics = {entry["metric"] for entry in job_metrics_catalog_entries()}
  assert "avg_arm_int8_ops" in metrics
  assert "avg_arm_int16_ops" in metrics
  assert "avg_arm_int8_ops" in JOB_METRIC_SHORT_LABELS
  assert "avg_arm_int16_ops" in JOB_METRIC_SHORT_LABELS


@pytest.mark.machine_unit_mock
def test_job_arc_avg_mbw_arm_counter_fallback():
  """avg_mbw falls back to host_cpu_hw arm_dram_bw_bytes when IMC rows are absent."""
  from hpcperfstats.dbload.lib.monitor_naming.resolve import arm_dram_bw_event_names

  dram_events = list(arm_dram_bw_event_names())

  def fake_job_arc(self, jt, **kw):
    ev = list(kw.get("events") or [])
    if kw.get("typename") in ("host_cpu_hw", "cpu_counter_metrics") and ev == dram_events:
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


@pytest.mark.machine_unit_mock
def test_job_arc_avg_sharedfs_iops_includes_nfs_when_available():
  """avg_sharedfs_iops sums llite and nfs operation counters when both exist."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("llite", "lustre_llite"):
      return 20.0
    if kw.get("typename") == "nfs":
      return 5.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_sharedfs_iops(object())
  assert abs(value - 25.0) < 1e-9
  assert typename == "llite"


@pytest.mark.machine_unit_mock
def test_job_arc_avg_sharedfs_bw_falls_back_to_nfs():
  """avg_sharedfs_bw uses nfs byte counters when llite counters are absent."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("llite", "lustre_llite"):
      return None
    if kw.get("typename") == "nfs":
      return 7.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_sharedfs_bw(object())
  assert abs(value - 7.0) < 1e-9
  assert typename == "nfs"


@pytest.mark.machine_unit_mock
def test_job_arc_avg_sharedfs_bw_sums_llite_and_nfs():
  """avg_sharedfs_bw sums Lustre and NFS when both contribute."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("llite", "lustre_llite"):
      return 11.0
    if kw.get("typename") == "nfs":
      return 4.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_sharedfs_bw(object())
  assert abs(value - 15.0) < 1e-9
  assert typename == "llite"


def test_job_arc_avg_ibbw_falls_back_to_ethernet():
  """avg_ibbw uses net rx/tx bytes when host_ib/opa are unavailable."""

  def fake_job_arc(self, jt, **kw):
    if kw.get("typename") in ("host_ib", "host_opa", "opa"):
      return None
    if kw.get("typename") == "net":
      return 12.0
    return None

  with patch.object(Metrics, "job_arc", fake_job_arc):
    m = Metrics()
    value, typename = m._job_arc_avg_ibbw(object())
  assert value == pytest.approx(12.0)
  assert typename == "net"


@pytest.mark.machine_unit_mock
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
      if typename in ("llite", "lustre_llite"):
        return None, {}
      if typename == "nfs":
        return schema_nfs, {"h1": stats}
      return None, {}

  value, typename, units = max_mds().compute_metric(MockU())
  assert value is not None
  assert value == pytest.approx(14.0)
  assert typename == "lustre_llite"
  assert units == "iops"


def _legacy_llite_metadata_event_names():
  """Proc opcode names corresponding to LLITE_METADATA_IOPS_EVENTS (pre-vfs_* archives)."""
  from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
      LLITE_METADATA_IOPS_EVENTS,
  )

  out = []
  for name in LLITE_METADATA_IOPS_EVENTS:
    assert name.startswith("vfs_") and name.endswith("_ops")
    out.append(name[len("vfs_"): -len("_ops")])
  return out


@pytest.mark.machine_unit_mock
def test_max_mds_uses_legacy_llite_opcode_schema():
  """Old-archive getattr/open KEYS still resolve via type-scoped dual-read."""
  from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
      LLITE_METADATA_IOPS_EVENTS,
  )

  legacy = _legacy_llite_metadata_event_names()
  assert len(legacy) == len(LLITE_METADATA_IOPS_EVENTS)
  n = len(legacy)
  schema = _Schema(legacy)
  # Cumulative: interval1 sum Δ=140 → 14 iops; interval2 sum Δ=160 → 16 iops.
  row0 = np.zeros(n, dtype=np.float64)
  row1 = row0.copy()
  row1[0] = 100.0
  row1[1] = 40.0
  row2 = row1.copy()
  row2[0] = 220.0
  row2[1] = 80.0
  stats = np.vstack([row0, row1, row2])

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename in ("llite", "lustre_llite"):
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_mds().compute_metric(MockU())
  assert value == pytest.approx(16.0)
  assert typename == "lustre_llite"
  assert units == "iops"


@pytest.mark.machine_unit_mock
def test_max_mds_uses_canonical_vfs_ops_schema():
  """New-emit vfs_*_ops KEYS resolve without legacy aliases."""
  from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
      LLITE_METADATA_IOPS_EVENTS,
  )

  events = list(LLITE_METADATA_IOPS_EVENTS)
  n = len(events)
  schema = _Schema(events)
  row0 = np.zeros(n, dtype=np.float64)
  row1 = row0.copy()
  row1[0] = 100.0
  row1[1] = 40.0
  row2 = row1.copy()
  row2[0] = 220.0
  row2[1] = 80.0
  stats = np.vstack([row0, row1, row2])

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename == "lustre_llite":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_mds().compute_metric(MockU())
  assert value == pytest.approx(16.0)
  assert typename == "lustre_llite"
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


@pytest.mark.machine_unit_mock
def test_max_fabricbw_falls_back_to_ethernet():
  """max_fabricbw uses net tx/rx byte rate when IB and OPA are absent."""
  schema = _Schema(["tx_bytes", "rx_bytes"])
  stats = np.array([[0.0, 0.0], [100.0, 50.0], [300.0, 150.0]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename in ("host_ib", "host_opa", "opa"):
        return None, {}
      if typename == "net":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_fabricbw().compute_metric(MockU())
  # max interval bytes/s = (200+100)/10 = 30, /MiB
  assert value == pytest.approx(30.0 / (1024 * 1024))
  assert typename == "net"
  assert units == "MB/s"


@pytest.mark.machine_unit_mock
def test_max_fabricbw_rejects_packet_rate_scale_as_mb_s():
  """Peak must use byte counters with MiB conversion, not ~5e9 packet-rate scale."""
  schema = _Schema(["port_xmit_data", "port_rcv_data"])
  # ~5e10 counter units over 10s without MiB conversion would look like ~5e9 "MB/s".
  stats = np.array(
      [[0.0, 0.0], [2.5e10, 2.5e10], [5.0e10, 5.0e10]], dtype=np.float64
  )

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}})()

    def get_type(self, typename):
      if typename == "host_ib":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_fabricbw().compute_metric(MockU())
  assert units == "MB/s"
  assert typename == "host_ib"
  # With MiB conversion: (5e10)/10 / (1024**2) ≈ 4768 MB/s — finite and << 5e9.
  assert value is not None
  assert value < 1e6
  assert value == pytest.approx(5.0e10 / 10.0 / (1024 * 1024))


@pytest.mark.machine_unit_mock
def test_avg_cpuusage_sums_per_host_means():
  """avg_cpuusage persists sum of per-host busy-core means (not mean-of-hosts)."""
  import pandas as pd
  from hpcperfstats.analysis.metrics.lib.metrics import Metrics

  m = Metrics()
  t0 = pd.Timestamp("2024-01-01 00:00:00")
  t1 = pd.Timestamp("2024-01-01 00:05:00")
  t2 = pd.Timestamp("2024-01-01 00:10:00")
  rows = []
  # Host a: buckets mean 1.0; host b: buckets mean 3.0 → sum 4.0 (mean would be 2.0).
  for host, arc in (("a", 1.0), ("b", 3.0)):
    for t in (t0, t1, t2):
      rows.append({"host": host, "time": t, "arc": arc})

  class FakeJt:
    _base_filter = {
        "host__in": ["a", "b"],
        "time__gte": t0,
        "time__lte": t2,
    }

  def fake_rows(*_a, **_k):
    return rows

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      fake_rows,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typ: (typ,),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics._jid_table_host_data_time_kwargs",
      lambda _base: {"time__gte": t0, "time__lte": t2},
  ):
    value = m.job_arc(
        FakeJt(),
        typename="host_cpu",
        events=["user", "system", "nice"],
        conv=1.0,
        host_aggregate="sum",
    )
  assert value == pytest.approx(4.0)


@pytest.mark.machine_unit_mock
def test_job_arc_means_samples_within_bucket_not_sum():
  """Multiple samples in one 5m bucket must mean (not sum) instantaneous totals.

  Regression: summing every sample row inflated avg_cpuusage by sample count
  (e.g. JID 858104-class ~10k cores vs ncores=40).
  """
  import pandas as pd
  from hpcperfstats.analysis.metrics.lib.metrics import Metrics

  m = Metrics()
  # Three timestamps inside the same 5m bucket after the first-bucket drop.
  # First bucket (t0) is dropped; remaining bucket has three samples of arc=2.0
  # → mean 2.0, not sum 6.0.
  t0 = pd.Timestamp("2024-01-01 00:00:00")
  t1 = pd.Timestamp("2024-01-01 00:05:00")
  t2 = pd.Timestamp("2024-01-01 00:05:10")
  t3 = pd.Timestamp("2024-01-01 00:05:20")
  rows = [
      {"host": "a", "time": t, "arc": 2.0} for t in (t0, t1, t2, t3)
  ]

  class FakeJt:
    _base_filter = {
        "host__in": ["a"],
        "time__gte": t0,
        "time__lte": t3,
    }

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      lambda *_a, **_k: rows,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typ: (typ,),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics._jid_table_host_data_time_kwargs",
      lambda _base: {"time__gte": t0, "time__lte": t3},
  ):
    value = m.job_arc(
        FakeJt(),
        typename="host_cpu",
        events=["user"],
        conv=1.0,
        host_aggregate="sum",
    )
  assert value == pytest.approx(2.0)


@pytest.mark.machine_unit_mock
def test_job_arc_keeps_single_bucket_per_host():
  """Single remaining 5m bucket must not be dropped (short-job defensive)."""
  import pandas as pd
  from hpcperfstats.analysis.metrics.lib.metrics import Metrics

  m = Metrics()
  t0 = pd.Timestamp("2024-01-01 00:00:00")
  t1 = pd.Timestamp("2024-01-01 00:01:00")
  rows = [
      {"host": "a", "time": t0, "arc": 5.0},
      {"host": "a", "time": t1, "arc": 5.0},
  ]

  class FakeJt:
    _base_filter = {
        "host__in": ["a"],
        "time__gte": t0,
        "time__lte": t1,
    }

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      lambda *_a, **_k: rows,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typ: (typ,),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics._jid_table_host_data_time_kwargs",
      lambda _base: {"time__gte": t0, "time__lte": t1},
  ):
    value = m.job_arc(
        FakeJt(),
        typename="host_cpu",
        events=["user"],
        conv=1.0,
        host_aggregate="mean",
    )
  assert value == pytest.approx(5.0)


@pytest.mark.machine_unit_mock
def test_avg_cpuusage_scales_to_allocated_ncores():
  """Node-wide /proc util scaled by ncores/nhosts (not raw host-wide busy cores)."""
  import pandas as pd
  from hpcperfstats.analysis.metrics.lib.metrics import Metrics

  m = Metrics()
  t0 = pd.Timestamp("2024-01-01 00:00:00")
  t1 = pd.Timestamp("2024-01-01 00:05:00")
  t2 = pd.Timestamp("2024-01-01 00:10:00")
  # After first-bucket drop, remaining buckets: util = busy/(busy+idle) = 100/200 = 0.5
  # cores_per_host = 2/2 = 1 → per-host mean 0.5; sum hosts = 1.0
  rows_busy = []
  rows_idle = []
  for host in ("a", "b"):
    for t in (t0, t1, t2):
      rows_busy.append({"host": host, "time": t, "arc": 100.0})
      rows_idle.append({"host": host, "time": t, "arc": 100.0})

  class FakeJt:
    _base_filter = {
        "host__in": ["a", "b"],
        "time__gte": t0,
        "time__lte": t2,
    }

  class FakeJob:
    ncores = 2
    nhosts = 2

  def fake_rows(_tkw, _hosts, _typename, events, _metric_column, **_kwargs):
    ev = set(events)
    if ev & {"idle", "iowait", "irq", "softirq"}:
      return list(rows_idle)
    return list(rows_busy)

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      fake_rows,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typ: (typ,),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.metrics._jid_table_host_data_time_kwargs",
      lambda _base: {"time__gte": t0, "time__lte": t2},
  ):
    value = m._job_avg_cpuusage_allocated(FakeJt(), FakeJob())
  assert value == pytest.approx(1.0)


@pytest.mark.machine_unit_mock
def test_gpu_activity_zero_mean_gate_accepts_zero():
  """GPU activity presence gate must accept mean 0 (idle ≠ missing samples)."""
  from pathlib import Path

  import hpcperfstats.analysis.metrics.lib.metrics as metrics_mod

  text = Path(metrics_mod.__file__).read_text()
  start = text.index('elif metric_name in (\n            "avg_tensor_active"')
  end = text.index('elif metric_name == "avg_fabric_mb_per_avg_tensor"', start)
  snippet = text[start:end]
  assert "if v is not None and float(v) > 0:" not in snippet
  assert "if v is not None:" in snippet
  from hpcperfstats.analysis.metrics.lib.metrics import NO_SIMPLE_SAMPLES_MSG

  v = 0.0
  value = float(v) if v is not None else None
  assert value == 0.0
  assert value is not None
  assert NO_SIMPLE_SAMPLES_MSG  # reason reserved for true missing samples only


@pytest.mark.machine_unit_mock
def test_build_job_metrics_display_list_hides_duplicate_avg_gpuutil():
  """When avg_gpuutil equals detail_gpu_util_mean, hide the duplicate row."""
  from hpcperfstats.analysis.metrics.lib.metrics import build_job_metrics_display_list

  class Row:
    def __init__(self, metric, value, type_="gpu", units="%"):
      self.metric = metric
      self.value = value
      self.type = type_
      self.units = units
      self.no_data_reason = None

  class Job:
    def __init__(self):
      self.metrics_data_set = type(
          "S",
          (),
          {
              "all": lambda self: [
                  Row("detail_gpu_util_mean", 2552.0),
                  Row("avg_gpuutil", 2552.0),
                  Row("avg_cpuusage", 10.0, type_="host_cpu", units="#cores"),
              ]
          },
      )()

  with patch(
      "hpcperfstats.analysis.metrics.lib.metrics.job_metrics_catalog_entries",
      return_value=[
          {"metric": "detail_gpu_util_mean", "type": "gpu", "units": "%"},
          {"metric": "avg_gpuutil", "type": "gpu", "units": "%"},
          {"metric": "avg_cpuusage", "type": "host_cpu", "units": "#cores"},
      ],
  ):
    out = build_job_metrics_display_list(Job())
  metrics = [r["metric"] for r in out]
  assert "detail_gpu_util_mean" in metrics
  assert "avg_gpuutil" not in metrics
  assert "avg_cpuusage" in metrics


@pytest.mark.machine_unit_mock
def test_job_cpu_gpu_watt_hours_integrates_and_gates():
  """Watt-hours requires CPU fragments (GPU optional); integrates W×s/3600 per host."""
  import pandas as pd
  from hpcperfstats.analysis.metrics.lib.gen import node_power_est as npe

  t0 = pd.Timestamp("2024-01-01 00:00:00")
  t1 = pd.Timestamp("2024-01-01 00:01:00")
  df = pd.DataFrame(
      {
          "host": ["h1", "h1"],
          "time": [t0, t1],
          "node_power_est_w": [100.0, 100.0],
          "dcg_cpu_power_w": [40.0, 40.0],
          "nv_power_w": [60.0, 60.0],
      }
  )

  class FakeJt:
    pass

  with patch.object(npe, "build_node_power_est_dataframe", return_value=df):
    wh = npe.job_cpu_gpu_watt_hours(FakeJt())
  # 100 W × 60 s = 6000 J = 6000/3600 Wh
  assert wh == pytest.approx(6000.0 / 3600.0)

  df_cpu_only = df.drop(columns=["nv_power_w"])
  with patch.object(npe, "build_node_power_est_dataframe", return_value=df_cpu_only):
    assert npe.job_cpu_gpu_watt_hours(FakeJt()) == pytest.approx(6000.0 / 3600.0)

  df_no_cpu = df.drop(columns=["dcg_cpu_power_w"])
  with patch.object(npe, "build_node_power_est_dataframe", return_value=df_no_cpu):
    assert npe.job_cpu_gpu_watt_hours(FakeJt()) is None


@pytest.mark.machine_unit_mock
def test_max_packetrate_falls_back_to_ethernet():
  """max_packetrate uses net tx/rx packet rate when IB and OPA are absent."""
  schema = _Schema(["tx_packets", "rx_packets"])
  stats = np.array([[0.0, 0.0], [100.0, 20.0], [220.0, 40.0]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 10.0, 20.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}, "cluster_mean_arc_by_type": {}})()

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


@pytest.mark.machine_unit_mock
def test_max_packetrate_rejects_uint64_wrap_poison():
  """Counter wrap (~2^63 packets / dt) must not become a huge peak rate."""
  schema = _Schema(["port_xmit_pkts", "port_rcv_pkts"])
  # Jump of 2**63 packets in 1s → ~9e18 #/s without sanity clamp.
  stats = np.array([[0.0, 0.0], [float(2**63), 0.0]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 1.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}, "cluster_mean_arc_by_type": {}})()

    def get_type(self, typename):
      if typename == "host_ib":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_packetrate().compute_metric(MockU())
  assert value is None
  assert typename == "host_ib"
  assert units == "#/s"


@pytest.mark.machine_unit_mock
def test_max_gpu_link_gbps_rejects_uint64_wrap_poison():
  """``2**64 / 1e9``-class GPU link peaks must become no_data, not ~1.84e10 GB/s."""
  schema = _Schema(["gpu_io_link_total_bytes"])
  stats = np.array([[0.0], [float(2**64)]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 1.0], dtype=np.float64)
    job = type("J", (), {"cluster_mean_by_type": {}, "cluster_mean_arc_by_type": {}})()

    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_gpu_link_gbps().compute_metric(MockU())
  assert value is None
  assert typename == "nvidia_gpu"
  assert units == "GB/s"


@pytest.mark.machine_unit_mock
def test_max_gpu_link_gbps_prefers_sane_arc_over_poison_value_diff():
  """When ingest arc is present and sane, prefer it over wrap poisoned value dy/dt."""
  schema = _Schema(["gpu_io_link_total_bytes"])
  # Poisoned counters, but cluster arc says 12e9 bytes/s → 12 GB/s.
  stats = np.array([[0.0], [float(2**63)]], dtype=np.float64)
  arc_cm = np.array([[12e9], [12e9]], dtype=np.float64)

  class MockU:
    t = np.array([0.0, 1.0], dtype=np.float64)
    job = type(
        "J",
        (),
        {
            "cluster_mean_by_type": {},
            "cluster_mean_arc_by_type": {"nvidia_gpu": arc_cm},
        },
    )()

    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_gpu_link_gbps().compute_metric(MockU())
  assert value == pytest.approx(12.0)
  assert typename == "nvidia_gpu"
  assert units == "GB/s"


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
def test_max_gpu_power_rejects_dcgm_fp64_blank():
  """4× DCGM_FP64_BLANK poison must not become max_gpu_power."""
  from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

  schema = _Schema(["power_usage"])
  blank_stats = np.array(
      [[DCGM_FP64_BLANK], [DCGM_FP64_BLANK], [300.0]], dtype=np.float64
  )

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": blank_stats}
      return None, {}

  value, typename, units = max_gpu_power().compute_metric(MockU())
  assert value == pytest.approx(300.0)
  assert typename == "nvidia_gpu"


@pytest.mark.machine_unit_mock
def test_max_gpu_power_all_blank_returns_none():
  from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

  schema = _Schema(["power_usage"])
  blank_stats = np.array([[DCGM_FP64_BLANK], [4 * DCGM_FP64_BLANK]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": blank_stats}
      return None, {}

  value, _typename, units = max_gpu_power().compute_metric(MockU())
  assert value is None
  assert units == "W"


@pytest.mark.machine_unit_mock
def test_avg_gpuutil_rejects_dcgm_int64_blank():
  from hpcperfstats.lib.dcgm_blank import DCGM_INT64_BLANK

  schema = _Schema(["gpu_util"])
  stats = np.array(
      [[0.0], [float(DCGM_INT64_BLANK)], [40.0], [60.0]], dtype=np.float64
  )

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = avg_gpuutil().compute_metric(MockU())
  assert typename == "nvidia_gpu"
  assert units == "%"
  # window is stats[1:-1] → blank + 40; blank dropped → mean 40
  assert value == pytest.approx(40.0)


@pytest.mark.machine_unit_mock
def test_max_gpu_clock_event_reasons_rejects_blank():
  from hpcperfstats.lib.dcgm_blank import DCGM_INT64_BLANK

  schema = _Schema(["clocks_event_reasons"])
  stats = np.array([[float(DCGM_INT64_BLANK)], [7.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename == "nvidia_gpu":
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = max_gpu_clock_event_reasons().compute_metric(MockU())
  assert value == 7.0
  assert typename == "nvidia_gpu"
  assert units == "#"


@pytest.mark.machine_unit_mock
def test_mem_hwm_all_nan_returns_none():
  schema = _Schema(["MemUsed", "Slab", "FilePages"])
  stats = np.array([[np.nan, np.nan, np.nan]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename in ("host_mem", "mem"):
        return schema, {"h1": stats}
      return None, {}

  value, typename, units = mem_hwm().compute_metric(MockU())
  assert value is None
  assert typename == "host_mem"
  assert units == "GiB"


@pytest.mark.machine_unit_mock
def test_mem_hwm_mixed_nan_uses_finite_peak():
  schema = _Schema(["MemUsed", "Slab", "FilePages"])
  nan_stats = np.array([[np.nan, np.nan, np.nan]], dtype=np.float64)
  gi = 2**30
  good_stats = np.array([[float(gi), 0.0, 0.0]], dtype=np.float64)

  class MockU:
    def get_type(self, typename):
      if typename in ("host_mem", "mem"):
        return schema, {"nan_host": nan_stats, "good_host": good_stats}
      return None, {}

  value, typename, units = mem_hwm().compute_metric(MockU())
  assert value == pytest.approx(1.0)
  assert typename == "host_mem"
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


@pytest.mark.machine_unit_mock
def test_job_metric_short_labels_cover_catalog():
  """Every catalog metric has a Job detail short label (JS mirror must stay in sync)."""
  for entry in job_metrics_catalog_entries():
    assert entry["metric"] in JOB_METRIC_SHORT_LABELS, entry["metric"]


@pytest.mark.machine_unit_mock
def test_build_job_metrics_display_list_fills_catalog_when_no_rows():
  """With no DB rows, every catalog metric gets METRIC_NOT_COMPUTED_YET."""
  job = MagicMock()
  job.metrics_data_set.all.return_value = []
  out = build_job_metrics_display_list(job)
  assert len(out) == len(job_metrics_catalog_entries())
  assert all(item["no_data_reason"] == METRIC_NOT_COMPUTED_YET for item in out)


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
def test_build_job_metrics_display_list_tiers_valued_error_then_not_computed():
  """Valued metrics, then Insufficient/error reasons, then Metric not computed."""
  from hpcperfstats.analysis.metrics.lib.metrics import (
      INSUFFICIENT_DATA_FOR_METRICS_PROCESSING,
  )

  entries = job_metrics_catalog_entries()
  valued = entries[0]
  errored = entries[1]
  row_v = MagicMock()
  row_v.metric = valued["metric"]
  row_v.type = valued["type"]
  row_v.units = valued["units"]
  row_v.value = 1.0
  row_v.no_data_reason = None
  row_e = MagicMock()
  row_e.metric = errored["metric"]
  row_e.type = errored["type"]
  row_e.units = errored["units"]
  row_e.value = None
  row_e.no_data_reason = INSUFFICIENT_DATA_FOR_METRICS_PROCESSING
  job = MagicMock()
  job.metrics_data_set.all.return_value = [row_e, row_v]
  out = build_job_metrics_display_list(job)
  assert out[0]["metric"] == valued["metric"]
  assert out[0]["value"] == 1.0
  assert out[1]["metric"] == errored["metric"]
  assert out[1]["no_data_reason"] == INSUFFICIENT_DATA_FOR_METRICS_PROCESSING
  assert out[2]["no_data_reason"] == METRIC_NOT_COMPUTED_YET


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


class _FakeHostDataQs:
  """Fake queryset accepting both the raw ``values()`` and per-sample-sum chains."""

  def __init__(self, hosts, rows, on_materialize=None):
    self._hosts = hosts
    self._rows = rows
    self._on_materialize = on_materialize

  def annotate(self, **_kwargs):
    return self

  def values(self, *_cols):
    return self

  def order_by(self, *_args):
    if self._on_materialize is not None:
      self._on_materialize(self._hosts)
    return list(self._rows)


class _FakeHostDataManager:
  """``host_data.objects`` stand-in recording filter kwargs per query."""

  def __init__(self, rows=(), on_materialize=None):
    self.filter_calls = []
    self._rows = rows
    self._on_materialize = on_materialize

  def filter(self, **kwargs):
    self.filter_calls.append(kwargs)
    return _FakeHostDataQs(
        list(kwargs.get("host__in") or []),
        self._rows,
        self._on_materialize,
    )


def _agg_row(host, time, value):
  """One row as returned by the per-(host, time) SQL sum queryset."""
  return {
      "host": host,
      jid_table.HOST_DATA_TIME_ALIAS: time,
      jid_table.HOST_DATA_SUM_VAL_ALIAS: value,
  }


@pytest.mark.django_db(databases=[])
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
  mgr = _FakeHostDataManager()
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", mgr):
    m.job_arc(jt, typename="net", events=["rx_bytes"], conv=1.0)
  kwargs = mgr.filter_calls[0]
  assert kwargs["time__in"] == [t1]
  assert "time__gte" not in kwargs
  assert "time__lte" not in kwargs


@pytest.mark.django_db(databases=[])
def test_host_data_metric_rows_batched_splits_host__in():
  """Large host lists query host_data in METRICS_HOST_QUERY_BATCH chunks."""

  n = METRICS_HOST_QUERY_BATCH + 2
  hosts = ["h{0}.x".format(i) for i in range(n)]
  chunk_sizes = []
  mgr = _FakeHostDataManager(
      on_materialize=lambda chunk: chunk_sizes.append(len(chunk)))

  tkw = {"time__gte": 1, "time__lte": 2}
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", mgr):
    rows = _host_data_metric_rows_batched(
        tkw, hosts, "net", ["rx_bytes"], "arc")
  assert rows == []
  assert chunk_sizes == [METRICS_HOST_QUERY_BATCH, 2]


@pytest.mark.django_db(databases=[])
def test_host_data_metric_rows_batched_rows_cache_reuses_fetch():
  """Same (tkw, typename, events, column) in one compute_metrics pass hits cache."""
  chunk_passes = []
  mgr = _FakeHostDataManager(
      rows=[{"host": "h1", "time": 1, "arc": 2.0}],
      on_materialize=lambda _chunk: chunk_passes.append(1),
  )

  tkw = {"time__gte": 1, "time__lte": 2}
  cache = {}
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", mgr):
    r1 = _host_data_metric_rows_batched(
        tkw, ["h1"], "net", ["rx_bytes"], "arc", rows_cache=cache)
    r2 = _host_data_metric_rows_batched(
        tkw, ["h1"], "net", ["rx_bytes"], "arc", rows_cache=cache)
  assert r1 == r2
  assert len(chunk_passes) == 1


@pytest.mark.django_db(databases=[])
def test_host_data_metric_rows_batched_normalizes_sql_sum_rows():
  """Per-sample-sum rows are relabelled to the raw shape ``{host, time, column}``."""
  mgr = _FakeHostDataManager(rows=[_agg_row("h1", 1, 7.0)])
  tkw = {"time__gte": 1, "time__lte": 2}
  with patch("hpcperfstats.site.lib.machine.models.host_data.objects", mgr):
    rows = _host_data_metric_rows_batched(
        tkw, ["h1"], "net", ["rx_bytes"], "arc", sum_per_sample=True)
  assert rows == [{"host": "h1", "time": 1, "arc": 7.0}]


@pytest.mark.django_db(databases=[])
def test_host_data_row_cache_key_separates_aggregate_variants():
  """Raw rows and per-sample sums must not share one compute_metrics memo entry."""
  tkw = {"time__gte": 1, "time__lte": 2}
  raw = _host_data_row_cache_key(tkw, "net", ["rx_bytes"], "arc")
  summed = _host_data_row_cache_key(
      tkw, "net", ["rx_bytes"], "arc", sum_per_sample=True)
  nonneg = _host_data_row_cache_key(
      tkw, "net", ["rx_bytes"], "arc", sum_per_sample=True, nonnegative_only=True)
  assert len({raw, summed, nonneg}) == 3


@pytest.mark.django_db(databases=[])
def test_job_arc_requests_sql_sum_and_nonnegative_filter(monkeypatch):
  """job_arc must push the per-sample sum (and rate sign filter) into SQL."""
  from types import SimpleNamespace

  from django.utils import timezone as django_tz

  t0 = django_tz.now()
  jt = SimpleNamespace(
      _base_filter={
          "time__gte": t0,
          "time__lte": t0,
          "host__in": ["h1.x"],
      }
  )
  seen = {}

  def fake_rows(*_a, sum_per_sample=False, nonnegative_only=False, **_k):
    seen["sum_per_sample"] = sum_per_sample
    seen["nonnegative_only"] = nonnegative_only
    return []

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics._host_data_metric_rows_batched",
      fake_rows,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typename: (typename,),
  )
  Metrics().job_arc(
      jt,
      typename="net",
      events=["rx_bytes"],
      conv=1.0,
      nonnegative_rate=True,
  )
  assert seen == {"sum_per_sample": True, "nonnegative_only": True}


@pytest.mark.django_db(databases=[])
def test_job_arc_issues_multiple_queries_when_many_hosts(monkeypatch):
  from types import SimpleNamespace

  from django.utils import timezone as django_tz

  n = METRICS_HOST_QUERY_BATCH + 1
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
  mgr = _FakeHostDataManager(on_materialize=lambda chunk: calls.append(len(chunk)))

  monkeypatch.setattr("hpcperfstats.site.lib.machine.models.host_data.objects", mgr)
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.metrics.type_probe_names",
      lambda typename: (typename,),
  )
  m = Metrics()
  m.job_arc(jt, typename="net", events=["rx_bytes"], conv=1.0)
  assert len(calls) == 2
  assert calls[0] == METRICS_HOST_QUERY_BATCH
  assert calls[1] == 1


def test_metric_type_events_feasible_skips_impossible_types():
  assert _metric_type_events_feasible({}, "amd64_pmc", ["FLOPS"]) is True
  assert _metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "amd64_pmc", ["FLOPS"]
  ) is False
  assert _metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "cpu", ["user"]
  ) is True


@pytest.mark.django_db(databases=[])
def test_job_arc_skips_orm_when_schema_rules_out_type(monkeypatch):
  """Regression: empty vendor probes must not list(qs) until 900s SIGALRM."""
  from types import SimpleNamespace

  from django.utils import timezone as django_tz

  t0 = django_tz.now()
  jt = SimpleNamespace(
      schema={"cpu": ["user", "system", "idle"]},
      _base_filter={
          "time__gte": t0,
          "time__lte": t0,
          "host__in": ["h1.x"],
      },
  )
  filter_calls = []

  class Mgr:
    def filter(self, **kwargs):
      filter_calls.append(kwargs)
      raise AssertionError("job_arc must not query host_data for impossible types")

  monkeypatch.setattr("hpcperfstats.site.lib.machine.models.host_data.objects", Mgr())
  m = Metrics()
  assert m.job_arc(jt, typename="amd64_pmc", events=["FLOPS"], conv=1e-9) is None
  assert filter_calls == []
