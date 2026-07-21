"""Unit tests for summary plot diagnostics from jid_table aggregates."""
import threading

import pandas as pd
import pytest
from unittest.mock import MagicMock
from bokeh.models import CategoricalColorMapper
from bokeh.models.plots import GridPlot
from bokeh.palettes import d3
from bokeh.plotting import figure

from hpcperfstats.dbload.lib.monitor_naming.canonical import INTEL_FP_ARITH_DOUBLE_EVENTS
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
    LLITE_METADATA_IOPS_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import (
    SummaryPlot,
    _cycled_d3_category20_palette,
    compute_summary_aggregate_prefetch_pool_size,
    plot_and_reason_summary_from_jid_table,
)

# Canonical monitor typenames (dual-read: legacy aliases still accepted in mocks).
_HOST_CPU_TYPES = ("host_cpu", "cpu")
_HOST_MEM_TYPES = ("host_mem", "mem")
_IB_FABRIC_TYPES = ("host_ib", "host_ib_ext", "ib_ext")
_INTEL_CORE_TYPES = (
    "intel_x86_pmc_gpr8",
    "intel_8pmc3",
    "intel_x86_pmc_gpr4",
    "intel_4pmc3",
    "cpu_counter_metrics",
    "host_cpu_hw",
)
_INTEL_RAPL_TYPES = ("intel_x86_rapl", "intel_rapl")
_AMD_RAPL_TYPES = ("amd_x86_rapl", "amd64_rapl")
_PKG_ENERGY_EVENT_NAMES = frozenset(
    {"pkg_energy", "MSR_PKG_ENERGY_STATUS", "MSR_PKG_ENERGY_STAT"}
)
_DCGM_CPU_POWER_EVENT_NAMES = frozenset(
    {"dcgm_cpu_power_util_w", "DCGM_CPU_POWER_UTIL_W"}
)


def _events_include_pkg_energy(events):
  return bool(_PKG_ENERGY_EVENT_NAMES.intersection(events))


def _events_include_dcgm_cpu_power(events):
  return bool(_DCGM_CPU_POWER_EVENT_NAMES.intersection(events))


def _is_cpu_type(typ):
  return typ in _HOST_CPU_TYPES


def _cas_events_match(events):
  """True when aggregate events include a dram CAS R+W pair (canonical or legacy)."""
  s = set(events)
  return (
      {"dram_cas_reads", "dram_cas_writes"}.issubset(s)
      or {"CAS_READS", "CAS_WRITES"}.issubset(s)
  )


def _hbm_cas_events_match(events):
  """True when aggregate events include SPR hbm_cas R+W (probed list may add aliases)."""
  return {"hbm_cas_reads", "hbm_cas_writes"}.issubset(set(events))


def test_compute_summary_aggregate_prefetch_pool_size_caps_at_two(monkeypatch):
  """Nested summary prefetch must not use full parallel_db_prefetch_max (API stacking)."""
  import hpcperfstats.analysis.metrics.lib.plot.summaryplot as sp

  monkeypatch.setattr(sp.cfg, "get_parallel_db_prefetch_max", lambda: 99)
  assert compute_summary_aggregate_prefetch_pool_size(50) == 2
  assert compute_summary_aggregate_prefetch_pool_size(1) == 1
  monkeypatch.setattr(sp.cfg, "get_parallel_db_prefetch_max", lambda: 1)
  assert compute_summary_aggregate_prefetch_pool_size(50) == 1


def test_summary_plot_reports_missing_counter_reason():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.schema = {"host_cpu": ["user"]}
  jt.get_host_time_df.return_value = pd.DataFrame(
      [("n1.cluster", t0)],
      columns=["host", "time"],
  )
  jt.get_aggregate_df.return_value = pd.DataFrame(columns=["host", "time", "sum_val"])

  fig, reason = plot_and_reason_summary_from_jid_table(jt)
  assert fig is None
  assert "Missing summary counters in host_data" in reason
  assert "host_data metric types present:" in reason
  assert "Detail:" in reason


def test_summaryplot_schema_skips_aggregates_for_absent_types():
  """When jt.schema is a real dict, do not query types that cannot exist for the job."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  lock = threading.Lock()
  types_seen = []

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv, events, val_col
    with lock:
      types_seen.append(typ)
    if _is_cpu_type(typ):
      return pd.DataFrame(
          [("n1.cluster", t0, 0.5)], columns=["host", "time", "sum_val"]
      )
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.schema = {"host_cpu": ["user", "system", "nice"]}
  jt.get_host_time_df.return_value = pd.DataFrame(
      [("n1.cluster", t0)], columns=["host", "time"]
  )
  jt.get_aggregate_df.side_effect = get_aggregate_df

  SummaryPlot(jt).plot()
  assert "amd64_pmc" not in types_seen
  assert any(t in types_seen for t in _HOST_CPU_TYPES)


def test_summaryplot_plot_includes_mbw_from_first_intel_imc_with_data():
  """DRAM mbw uses INTEL_IMC_STATS_TYPES order, not SKX-only."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)
  hsw_types = ("intel_x86_uncore_imc_hsw", "intel_hsw_imc")

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "arc" and _is_cpu_type(typ) and "user" in events:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      if list(events) == fp64:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    if typ in hsw_types and _cas_events_match(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig = SummaryPlot(jt).plot()
  assert fig is not None


def test_summaryplot_mbw_from_spr_hbm_cas_only():
  """SPR HBM-only CAS yields summary mbw when dram_cas aggregates are empty."""
  from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)
  spr = "intel_x86_uncore_imc_spr"

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "arc" and _is_cpu_type(typ) and "user" in events:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      if list(events) == fp64:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    if typ == spr and _hbm_cas_events_match(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 7.0)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig = SummaryPlot(jt).plot()
  assert fig is not None


def test_summaryplot_mbw_sums_spr_dram_and_hbm_cas():
  """When SPR has both dram and hbm CAS, summary mbw uses the summed series."""
  from hpcperfstats.analysis.metrics.lib.plot.summaryplot import (
      _merge_intel_imc_cas_mbw,
  )

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  spr = "intel_x86_uncore_imc_spr"

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv, val_col
    if typ != spr:
      return empty
    if _cas_events_match(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 1.5)], columns=["host", "time", "sum_val"]
      )
    if _hbm_cas_events_match(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 2.5)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_aggregate_df.side_effect = get_aggregate_df
  out = _merge_intel_imc_cas_mbw(base.copy(), jt)
  assert "mbw" in out.columns
  assert abs(float(out["mbw"].iloc[0]) - 4.0) < 1e-12


def test_summaryplot_skips_freq_plot_when_ghz_never_exceeds_500():
  """Freq subplot is omitted unless at least one point is > 500 GHz."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_INTEL_RAPL_TYPES, *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      event_list = list(events)
      if event_list == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
      if event_list == ["MPERF"]:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
      if event_list == ["APERF"]:
        # freq = 2.7 * APERF / MPERF = 486 (never over 500)
        return pd.DataFrame([("n1.cluster", t0, 180.0)], columns=["host", "time", "sum_val"])
      if event_list == ["INST_RETIRED"]:
        return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
      return empty
    if _is_cpu_type(typ) and "user" in list(events):
      return pd.DataFrame([("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "freq" not in captured_metrics


def test_summaryplot_includes_nvidia_gpu_util_and_mem_used_mb_columns():
  """Summary grid includes nv_gpu_util and nv_mem_used_mb when aggregates exist."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "value" and typ == "nvidia_gpu":
      ev = list(events)
      if ev == ["gpu_util"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 72.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_used_mb"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 4096.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_total_mb"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 16384.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["gpu_count"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    if val_col == "arc" and _is_cpu_type(typ) and "user" in list(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_INTEL_RAPL_TYPES, *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      event_list = list(events)
      if event_list == fp64:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["MPERF"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["APERF"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 180.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["INST_RETIRED"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []
  mem_used_y_caps = []
  gpu_util_y_caps = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label
    captured_metrics.append(metric)
    if metric == "nv_mem_used_mb":
      mem_used_y_caps.append(y_range_end)
    if metric == "nv_gpu_util":
      gpu_util_y_caps.append(y_range_end)
    del x_range
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "nv_gpu_util" in captured_metrics
  assert "nv_mem_used_mb" in captured_metrics
  assert "nv_mem_total_mb" not in captured_metrics
  assert "nv_gpu_count" not in captured_metrics
  assert len(mem_used_y_caps) == 1
  assert len(gpu_util_y_caps) == 1
  # Mock aggregate side effects ignore conversion factors and return raw values.
  assert mem_used_y_caps[0] == pytest.approx(1.1 * 16384.0)
  assert gpu_util_y_caps[0] == pytest.approx(200.0)


def test_summaryplot_nv_gpu_util_falls_back_to_utilization_event():
  """When gpu_util has no rows, summary plot uses nvidia_gpu utilization (legacy)."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "value" and typ == "nvidia_gpu":
      ev = list(events)
      if ev == ["gpu_util"]:
        return empty
      if ev == ["utilization"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 55.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_used_mb"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 4096.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_total_mb"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 16384.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    if val_col == "arc" and _is_cpu_type(typ) and "user" in list(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_INTEL_RAPL_TYPES, *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      event_list = list(events)
      if event_list == fp64:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["MPERF"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["APERF"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 180.0)], columns=["host", "time", "sum_val"]
        )
      if event_list == ["INST_RETIRED"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "nv_gpu_util" in captured_metrics


def test_summaryplot_keeps_nvidia_columns_when_merge_has_nan_gaps():
  """Dense host_time_df + sparse GPU rows used to drop nv_* columns entirely."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 12:01:00+00:00")
  base = pd.DataFrame(
      [("n1.cluster", t0), ("n1.cluster", t1)],
      columns=["host", "time"],
  )
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "value" and typ == "nvidia_gpu":
      ev = list(events)
      if ev == ["gpu_util"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 72.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_used_mb"]:
        return pd.DataFrame(
            [("n1.cluster", t1, 8192.0)], columns=["host", "time", "sum_val"]
        )
      if ev == ["mem_total_mb"]:
        return pd.DataFrame(
            [
                ("n1.cluster", t0, 16384.0),
                ("n1.cluster", t1, 16384.0),
            ],
            columns=["host", "time", "sum_val"],
        )
      return empty
    if val_col == "arc" and _is_cpu_type(typ) and "user" in list(events):
      return pd.DataFrame(
          [
              ("n1.cluster", t0, 0.25),
              ("n1.cluster", t1, 0.26),
          ],
          columns=["host", "time", "sum_val"],
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_INTEL_RAPL_TYPES, *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES:
      event_list = list(events)
      two = [("n1.cluster", t0, 1.0), ("n1.cluster", t1, 1.0)]
      if event_list == fp64:
        return pd.DataFrame(two, columns=["host", "time", "sum_val"])
      if event_list == ["MPERF"]:
        return pd.DataFrame(two, columns=["host", "time", "sum_val"])
      if event_list == ["APERF"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 180.0), ("n1.cluster", t1, 181.0)],
            columns=["host", "time", "sum_val"],
        )
      if event_list == ["INST_RETIRED"]:
        return pd.DataFrame(two, columns=["host", "time", "sum_val"])
      return empty
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "nv_gpu_util" in captured_metrics
  assert "nv_mem_used_mb" in captured_metrics
  assert "nv_mem_total_mb" not in captured_metrics


def test_summaryplot_plot_metric_caps_time_tick_count_to_five():
  """Summary plot x-axis should target at most five datetime tick labels."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  df = pd.DataFrame(
      [("n1.cluster", t0, 1.0)],
      columns=["host", "time", "cpu"],
  )
  jt = MagicMock()
  jt.jid = 123
  jt.host_list = ["n1.cluster"]
  summary = SummaryPlot(jt)
  summary.hc = {"n1.cluster": "#1f77b4"}

  fig = summary.plot_metric(df, "cpu", "CPU Usage [#cores]")
  assert fig.xaxis[0].ticker.desired_num_ticks == 5


def test_cycled_d3_category20_palette_length():
  assert _cycled_d3_category20_palette(0) == []
  assert len(_cycled_d3_category20_palette(5)) == 5
  assert len(_cycled_d3_category20_palette(25)) == 25
  base20 = _cycled_d3_category20_palette(20)
  c25 = _cycled_d3_category20_palette(25)
  assert c25[:20] == base20
  assert c25[20:25] == base20[:5]


def _scatter_categorical_color_mapper(plot):
  for r in plot.renderers:
    glyph = getattr(r, "glyph", None)
    if glyph is None:
      continue
    fc = getattr(glyph, "fill_color", None)
    tr = getattr(fc, "transform", None) if fc is not None else None
    if isinstance(tr, CategoricalColorMapper):
      return tr
  raise AssertionError("expected scatter CategoricalColorMapper")


def test_summaryplot_plot_metric_palette_matches_factor_count_for_many_hosts():
  """Avoid Bokeh W-1008: factor_cmap palette length must match host factors."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  hosts = [f"h{i}.stampede3.tacc.utexas.edu" for i in range(40)]
  rows = [(h, t0, float(i)) for i, h in enumerate(hosts)]
  df = pd.DataFrame(rows, columns=["host", "time", "cpu"])
  jt = MagicMock()
  jt.host_list = hosts
  summary = SummaryPlot(jt)
  base = d3["Category20"][20]
  summary.hc = {h: base[i % 20] for i, h in enumerate(hosts)}
  fig = summary.plot_metric(df, "cpu", "CPU Usage [#cores]")
  mapper = _scatter_categorical_color_mapper(fig)
  assert len(mapper.factors) == 40
  assert len(mapper.palette) == 40


def test_summaryplot_uses_job_window_for_x_range():
  """Summary plots should use job start/end as explicit x-axis bounds."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 12:01:00+00:00")
  job_start = pd.Timestamp("2024-06-01 11:55:00+00:00")
  job_end = pd.Timestamp("2024-06-01 12:10:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0), ("n1.cluster", t1)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if _is_cpu_type(typ) and val_col == "arc" and list(events) == ["user", "system", "nice"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0), ("n1.cluster", t1, 1.2)],
          columns=["host", "time", "sum_val"],
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.start_time = job_start
  jt.end_time = job_end
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  seen_x_ranges = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, metric, label, y_range_end
    seen_x_ranges.append(x_range)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert seen_x_ranges
  assert pd.Timestamp(seen_x_ranges[0].start).tz_convert("UTC") == job_start
  assert pd.Timestamp(seen_x_ranges[0].end).tz_convert("UTC") == job_end
  assert str(pd.Timestamp(seen_x_ranges[0].start).tz) in ("UTC", "+00:00")


def test_summaryplot_plot_metric_keeps_utc_epoch_for_data_and_x_range():
  """Summary metric plots pass UTC instants to Bokeh for both data and x_range."""
  from bokeh.models import HoverTool, Range1d
  from bokeh.util.serialization import convert_datetime_type

  t0 = pd.Timestamp("2024-06-01 10:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 10:05:00+00:00")
  job_start = pd.Timestamp("2024-06-01 09:55:00+00:00")
  job_end = pd.Timestamp("2024-06-01 10:10:00+00:00")
  x_range = Range1d(job_start, job_end)

  class _Jt:
    jid = 1
    host_list = ["h1"]

  sp = SummaryPlot(_Jt())
  sp.hc = {"h1": "#111111"}
  df = pd.DataFrame({
      "time": [t0, t1],
      "host": ["h1", "h1"],
      "cpu": [1.0, 2.0],
  })

  plot = sp.plot_metric(df, "cpu", "CPU Usage [#cores]", x_range=x_range)
  assert convert_datetime_type(plot.x_range.start) == convert_datetime_type(job_start)
  assert convert_datetime_type(plot.x_range.end) == convert_datetime_type(job_end)

  scatter = [
      r
      for r in plot.renderers
      if getattr(r, "glyph", None) is not None
      and getattr(r.glyph, "y", None) == "cpu"
  ][0]
  data_times = scatter.data_source.data["time"]
  assert convert_datetime_type(data_times[0]) == convert_datetime_type(t0)
  assert convert_datetime_type(data_times[1]) == convert_datetime_type(t1)

  hover = [
      tool
      for tool in plot.tools
      if isinstance(tool, HoverTool)
      and isinstance(tool.tooltips, str)
      and "@cpu_plain" in tool.tooltips
  ][0]
  assert hover.formatters == {}
  assert "@_hover_time" in hover.tooltips


def test_summaryplot_orders_cpu_then_gpu_then_ibbw():
  """Summary subplot order: CPU block before GPU block before InfiniBand bandwidth (ibbw)."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "value":
      if ev == ["gpu_util"]:
        return pd.DataFrame([("n1.cluster", t0, 50.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_used_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 2048.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_total_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 8192.0)], columns=["host", "time", "sum_val"])
      if ev == ["gpu_count"]:
        return pd.DataFrame([("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in _IB_FABRIC_TYPES and val_col == "arc" and ev == ["port_rcv_data", "port_xmit_data"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    if typ in _INTEL_CORE_TYPES and val_col == "arc":
      if ev == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured_metrics.index("cpu") < captured_metrics.index("nv_gpu_util")
  assert captured_metrics.index("nv_gpu_util") < captured_metrics.index("ibbw")


def test_summaryplot_orders_buckets_cpu_memory_compute_gpu_subblocks_network():
  """Enforce bucket order: CPU usage → CPU memory → CPU compute → GPU usage → GPU memory → GPU tensor → GPU other → ibbw."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ in _HOST_MEM_TYPES and val_col == "value" and ev in (["mem_used"], ["MemUsed"]):
      return pd.DataFrame([("n1.cluster", t0, 1024.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "value":
      if ev == ["gpu_util"]:
        return pd.DataFrame([("n1.cluster", t0, 50.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_used_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 2048.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_total_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 8192.0)], columns=["host", "time", "sum_val"])
      if ev == ["gpu_count"]:
        return pd.DataFrame([("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_imma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_hmma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_dfma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 4.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_active"]:
        return pd.DataFrame([("n1.cluster", t0, 12.0)], columns=["host", "time", "sum_val"])
      if ev == ["power_usage"]:
        return pd.DataFrame([("n1.cluster", t0, 180.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in _IB_FABRIC_TYPES and val_col == "arc" and ev == ["port_rcv_data", "port_xmit_data"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    if typ in _INTEL_CORE_TYPES and val_col == "arc":
      if ev == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured_metrics.index("cpu") < captured_metrics.index("mem")
  assert captured_metrics.index("mem") < captured_metrics.index("flops64b")
  assert captured_metrics.index("flops64b") < captured_metrics.index("nv_gpu_util")
  assert captured_metrics.index("nv_gpu_util") < captured_metrics.index("nv_mem_used_mb")
  assert captured_metrics.index("nv_mem_used_mb") < captured_metrics.index("nv_tensor_imma_active")
  assert captured_metrics.index("nv_tensor_imma_active") < captured_metrics.index(
      "nv_tensor_hmma_active"
  )
  assert captured_metrics.index("nv_tensor_hmma_active") < captured_metrics.index(
      "nv_tensor_dfma_active"
  )
  assert captured_metrics.index("nv_tensor_dfma_active") < captured_metrics.index("nv_power_w")
  assert "nv_tensor_active" not in captured_metrics
  assert captured_metrics.index("nv_power_w") < captured_metrics.index("ibbw")


def test_summaryplot_prefers_tensor_splits_over_lumped_pipe():
  """When IMMA/HMMA/DFMA aggregates exist, plot splits and skip lumped nv_tensor_active."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "value":
      if ev == ["tensor_imma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_hmma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_dfma_active"]:
        return pd.DataFrame([("n1.cluster", t0, 4.0)], columns=["host", "time", "sum_val"])
      if ev == ["tensor_active"]:
        return pd.DataFrame([("n1.cluster", t0, 12.0)], columns=["host", "time", "sum_val"])
      return empty
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "nv_tensor_imma_active" in captured_metrics
  assert "nv_tensor_hmma_active" in captured_metrics
  assert "nv_tensor_dfma_active" in captured_metrics
  assert "nv_tensor_active" not in captured_metrics


def test_summaryplot_lumped_tensor_fallback_when_splits_absent():
  """AMD / stacks without split PROF fields still plot lumped nv_tensor_active."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "value":
      if ev == ["tensor_active"]:
        return pd.DataFrame([("n1.cluster", t0, 12.0)], columns=["host", "time", "sum_val"])
      return empty
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "nv_tensor_active" in captured_metrics
  assert "nv_tensor_imma_active" not in captured_metrics
  assert "nv_tensor_hmma_active" not in captured_metrics
  assert "nv_tensor_dfma_active" not in captured_metrics


def test_summaryplot_orders_lustre_nfs_before_network():
  """Lustre plots, then NFS plots, then network (ibbw); no merged shared_fs metrics."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)
  llite_meta_events = list(LLITE_METADATA_IOPS_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ in _HOST_MEM_TYPES and val_col == "value" and ev in (["mem_used"], ["MemUsed"]):
      return pd.DataFrame([("n1.cluster", t0, 1024.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "value":
      if ev == ["gpu_util"]:
        return pd.DataFrame([("n1.cluster", t0, 50.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_used_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 2048.0)], columns=["host", "time", "sum_val"])
      if ev == ["mem_total_mb"]:
        return pd.DataFrame([("n1.cluster", t0, 8192.0)], columns=["host", "time", "sum_val"])
      if ev == ["gpu_count"]:
        return pd.DataFrame([("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in ("lustre_llite", "llite") and val_col == "arc":
      if "vfs_read_bytes" in ev or ev == ["read_bytes"]:
        return pd.DataFrame([("n1.cluster", t0, 1024.0)], columns=["host", "time", "sum_val"])
      if "vfs_write_bytes" in ev or ev == ["write_bytes"]:
        return pd.DataFrame([("n1.cluster", t0, 2048.0)], columns=["host", "time", "sum_val"])
      if set(llite_meta_events).issubset(set(ev)) or ev == llite_meta_events:
        return pd.DataFrame([("n1.cluster", t0, 64.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in ("host_nfs", "nfs") and val_col == "arc":
      evset = set(ev)
      if evset.intersection({"normal_read", "direct_read", "server_read"}):
        return pd.DataFrame([("n1.cluster", t0, 512.0)], columns=["host", "time", "sum_val"])
      if evset.intersection({"normal_write", "direct_write", "server_write"}):
        return pd.DataFrame([("n1.cluster", t0, 256.0)], columns=["host", "time", "sum_val"])
      if evset.intersection({"read_ops", "write_ops", "READ_ops", "WRITE_ops"}):
        return pd.DataFrame([("n1.cluster", t0, 128.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in _IB_FABRIC_TYPES and val_col == "arc" and ev == ["port_rcv_data", "port_xmit_data"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    if typ in _INTEL_CORE_TYPES and val_col == "arc":
      if ev == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del df, label, y_range_end, x_range
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured_metrics.index("cpu") < captured_metrics.index("mem")
  assert captured_metrics.index("mem") < captured_metrics.index("nv_gpu_util")
  assert captured_metrics.index("nv_gpu_util") < captured_metrics.index("nv_mem_used_mb")
  assert captured_metrics.index("lustre_read_mb_s") < captured_metrics.index("lustre_write_mb_s")
  assert captured_metrics.index("lustre_write_mb_s") < captured_metrics.index("liops")
  assert captured_metrics.index("liops") < captured_metrics.index("nfs_read_mb_s")
  assert captured_metrics.index("nfs_read_mb_s") < captured_metrics.index("nfs_write_mb_s")
  assert captured_metrics.index("nfs_write_mb_s") < captured_metrics.index("nfs_iops")
  assert captured_metrics.index("nfs_iops") < captured_metrics.index("ibbw")
  idx_ibbw = captured_metrics.index("ibbw")
  for name in ("opa_wait_cong", "opa_ecn"):
    if name in captured_metrics:
      assert idx_ibbw < captured_metrics.index(name)
  assert isinstance(fig, GridPlot)
  assert len(captured_metrics) > 2
  assert max(child[2] for child in fig.children) <= 1


def test_summaryplot_lustre_and_nfs_read_write_use_per_host_time_series():
  """Lustre and NFS read/write are separate columns with per-host time series."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 12:01:00+00:00")
  base = pd.DataFrame(
      [("n1.cluster", t0), ("n2.cluster", t0), ("n1.cluster", t1), ("n2.cluster", t1)],
      columns=["host", "time"],
  )
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0), ("n2.cluster", t0, 1.1), ("n1.cluster", t1, 1.2), ("n2.cluster", t1, 1.3)],
          columns=["host", "time", "sum_val"],
      )
    if typ in ("lustre_llite", "llite") and val_col == "arc":
      if "vfs_read_bytes" in ev or ev == ["read_bytes"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 10.0), ("n2.cluster", t1, 20.0)],
            columns=["host", "time", "sum_val"],
        )
      if "vfs_write_bytes" in ev or ev == ["write_bytes"]:
        return pd.DataFrame(
            [("n1.cluster", t1, 30.0), ("n2.cluster", t0, 40.0)],
            columns=["host", "time", "sum_val"],
        )
      return empty
    if typ in ("host_nfs", "nfs") and val_col == "arc":
      if ev == ["normal_read", "direct_read", "server_read"]:
        return pd.DataFrame(
            [("n2.cluster", t0, 5.0), ("n1.cluster", t1, 7.0)],
            columns=["host", "time", "sum_val"],
        )
      if ev == ["normal_write", "direct_write", "server_write"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 3.0), ("n2.cluster", t1, 9.0)],
            columns=["host", "time", "sum_val"],
        )
      return empty
    if typ in _INTEL_CORE_TYPES and val_col == "arc" and ev == fp64:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster", "n2.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_series = {}

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    if metric in (
        "lustre_read_mb_s",
        "lustre_write_mb_s",
        "nfs_read_mb_s",
        "nfs_write_mb_s",
    ):
      captured_series[metric] = df[["host", "time", metric]].copy()
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  for key in (
      "lustre_read_mb_s",
      "lustre_write_mb_s",
      "nfs_read_mb_s",
      "nfs_write_mb_s",
  ):
    assert key in captured_series
    assert set(captured_series[key]["host"].unique()) == {"n1.cluster", "n2.cluster"}
    assert captured_series[key]["time"].nunique() == 2


def test_summaryplot_liops_and_nfs_iops_are_separate_per_host():
  """Lustre metadata IOPS (liops) and NFS ops (nfs_iops) are not merged."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0), ("n2.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)
  llite_meta = list(LLITE_METADATA_IOPS_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if _is_cpu_type(typ) and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0), ("n2.cluster", t0, 1.1)],
          columns=["host", "time", "sum_val"],
      )
    if typ in ("lustre_llite", "llite") and val_col == "arc":
      if set(llite_meta).issubset(set(ev)) or any(e.startswith("vfs_") and e.endswith("_ops") for e in ev):
        return pd.DataFrame(
            [("n1.cluster", t0, 70.0), ("n2.cluster", t0, 40.0)],
            columns=["host", "time", "sum_val"],
        )
      return empty
    if typ in ("host_nfs", "nfs") and val_col == "arc" and set(ev).intersection(
        {"read_ops", "write_ops", "READ_ops", "WRITE_ops"}
    ):
      return pd.DataFrame(
          [("n1.cluster", t0, 30.0), ("n2.cluster", t0, 10.0)],
          columns=["host", "time", "sum_val"],
      )
    if typ in _INTEL_CORE_TYPES and val_col == "arc" and ev == fp64:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster", "n2.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured = {}

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    if metric in ("liops", "nfs_iops"):
      captured[metric] = df[["host", "time", metric]].copy()
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert "liops" in captured and "nfs_iops" in captured
  liops_by_host = {row["host"]: row["liops"] for _, row in captured["liops"].iterrows()}
  nfs_by_host = {row["host"]: row["nfs_iops"] for _, row in captured["nfs_iops"].iterrows()}
  assert liops_by_host["n1.cluster"] == 70.0
  assert liops_by_host["n2.cluster"] == 40.0
  assert nfs_by_host["n1.cluster"] == 30.0
  assert nfs_by_host["n2.cluster"] == 10.0


def test_summaryplot_node_power_est_w_intel_plus_gpu():
  """node_power_est_w merges Intel PKG watts and summed GPU power when no module reading."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if typ in _INTEL_RAPL_TYPES and val_col == "arc" and _events_include_pkg_energy(ev):
      return pd.DataFrame(
          [("n1.cluster", t0, 100.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "nvidia_gpu" and val_col == "value" and ev == ["power_usage"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 250.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "nvidia_gpu" and val_col == "value" and ev == ["module_power_usage"]:
      return empty
    if typ in ("host_cpu_hw", "cpu_counter_metrics") and val_col == "value" and _events_include_dcgm_cpu_power(ev):
      return empty
    if typ in _AMD_RAPL_TYPES and val_col == "arc" and _events_include_pkg_energy(ev):
      return empty
    if val_col == "arc" and _is_cpu_type(typ) and "user" in ev:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES and ev == fp64:
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["MPERF"], ["mperf"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["APERF"], ["aperf"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 200.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["INST_RETIRED"], ["instr_retired"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  captured = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    if metric == "node_power_est_w":
      captured.append(float(df[metric].iloc[0]))
    return figure(width=100, height=60)

  summary = SummaryPlot(jt)
  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured
  # MagicMock aggregate returns raw sum_val; real jid_table applies conv in get_aggregate_df.
  assert captured[0] == 350.0


def test_summaryplot_node_power_est_w_prefers_module_branch():
  """When module_power_usage > 0, node estimate uses module only (not CPU+GPU sum)."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if typ in _INTEL_RAPL_TYPES and val_col == "arc" and _events_include_pkg_energy(ev):
      return pd.DataFrame(
          [("n1.cluster", t0, 50.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "nvidia_gpu" and val_col == "value" and ev == ["power_usage"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 400.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "nvidia_gpu" and val_col == "value" and ev == ["module_power_usage"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 900.0)], columns=["host", "time", "sum_val"]
      )
    if typ in ("host_cpu_hw", "cpu_counter_metrics") and val_col == "value" and _events_include_dcgm_cpu_power(ev):
      return pd.DataFrame(
          [("n1.cluster", t0, 120.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _AMD_RAPL_TYPES and val_col == "arc" and _events_include_pkg_energy(ev):
      return empty
    if val_col == "arc" and _is_cpu_type(typ) and "user" in ev:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df", *_IB_FABRIC_TYPES, "lustre_llite", "llite"):
      return empty
    if typ in _INTEL_CORE_TYPES and ev == fp64:
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["MPERF"], ["mperf"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["APERF"], ["aperf"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 200.0)], columns=["host", "time", "sum_val"]
      )
    if typ in _INTEL_CORE_TYPES and ev in (["INST_RETIRED"], ["instr_retired"]):
      return pd.DataFrame(
          [("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  captured = []

  def fake_plot_metric(df, metric, label, y_range_end=None, x_range=None):
    del label, y_range_end, x_range
    if metric == "node_power_est_w":
      captured.append(float(df[metric].iloc[0]))
    return figure(width=100, height=60)

  summary = SummaryPlot(jt)
  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured == [900.0]


def test_plot_hardware_error_rates_figure_returns_when_ib_errors_present():
  from hpcperfstats.analysis.metrics.lib.plot.summaryplot import plot_hardware_error_rates_figure

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.schema = {"host_ib": ["port_rcv_errors"]}

  def _agg(typ, val_col, events, conv=1.0):
    del val_col, conv
    if typ in ("host_ib", "ib") and list(events) == ["port_rcv_errors"]:
      return pd.DataFrame([("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = _agg
  fig = plot_hardware_error_rates_figure(jt, None)
  assert fig is not None
