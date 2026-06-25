"""Unit tests for roofline plot diagnostics and fallbacks."""
import pandas as pd
import pytest
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.lib.plot.roofline import (
  ROOFLINE_NOMINAL_PEAKS_INVALID_REASON,
  _build_roofline_figure,
  plot_and_reason_gpu_roofline_from_jid_table,
  plot_and_reason_roofline_from_jid_table,
  plot_gpu_roofline_from_jid_table,
)


def _make_jt(base_rows, agg_map):
  """Create mock jid_table with host/time base rows and aggregate map keyed by (typ, val_col)."""

  def get_host_time_df():
    return pd.DataFrame(base_rows, columns=["host", "time"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del events, conv
    rows = agg_map.get((typ, val_col), [])
    if not rows:
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    return pd.DataFrame(rows, columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.side_effect = get_host_time_df
  jt.get_aggregate_df.side_effect = get_aggregate_df
  return jt


def test_roofline_reports_missing_counter_reason():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})
  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is None
  assert "Missing roofline counters in host_data" in reason
  assert "Attempted:" in reason


def test_roofline_reports_missing_reason_when_only_value_counters_exist():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      [("n1.cluster", t0)],
      {
          ("amd64_pmc", "value"): [("n1.cluster", t0, 1000.0)],
          ("amd64_df", "value"): [("n1.cluster", t0, 100.0)],
      },
  )
  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is None
  assert reason is not None
  assert "Missing roofline counters in host_data" in reason

def test_roofline_intel_succeeds_with_non_skx_imc_bandwidth():
  """Intel roofline uses first IMC type in INTEL_IMC_STATS_TYPES with CAS data (e.g. HSW)."""
  import pandas as pd
  from unittest.mock import MagicMock
  from hpcperfstats.analysis.metrics.lib.plot.roofline import plot_and_reason_roofline_from_jid_table
  from hpcperfstats.dbload.lib.monitor_naming.resolve import imc_types_probe_order

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  hsw_canon = "intel_x86_uncore_imc_hsw"
  hsw_legacy = "intel_hsw_imc"
  cas_pairs = (
      ("dram_cas_reads", "dram_cas_writes"),
      ("CAS_READS", "CAS_WRITES"),
  )
  core_pmcs = (
      "intel_x86_pmc_gpr8",
      "intel_8pmc3",
      "intel_4pmc3",
  )

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "amd_x86_pmc", "amd_x86_uncore_df"):
      return empty
    if typ in core_pmcs:
      if len(events) > 3:
        return pd.DataFrame([("n1.cluster", t0, 3.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ in (hsw_canon, hsw_legacy):
      ev = list(events)
      if any(ev == list(rw) for rw in cas_pairs):
        return pd.DataFrame([("n1.cluster", t0, 0.5)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = pd.DataFrame(
      [("n1.cluster", t0)], columns=["host", "time"]
  )
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None
  assert hsw_canon in imc_types_probe_order()


def test_roofline_succeeds_with_cpu_counter_metrics_flops_and_imc_bw():
  """Intel roofline FLOPS can come from cpu_counter_metrics (aligned with avg_flops)."""
  from hpcperfstats.dbload.lib.monitor_naming.canonical import INTEL_FP_ARITH_ALL_EVENTS

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  hsw = "intel_hsw_imc"
  fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3"):
      return empty
    if typ == "cpu_counter_metrics" and list(events) == fp_events:
      return pd.DataFrame(
          [("n1.cluster", t0, 4.0)], columns=["host", "time", "sum_val"]
      )
    if typ == hsw and list(events) == ["CAS_READS", "CAS_WRITES"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.5)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_roofline_succeeds_with_arm_dcgm_approx_metrics():
  """ARM fallback uses cpu_counter_metrics ARM_EST_FLOPS + ARM_DRAM_BW_BYTES."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ == "cpu_counter_metrics" and list(events) == ["ARM_EST_FLOPS"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 6.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "cpu_counter_metrics" and list(events) == ["ARM_DRAM_BW_BYTES"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 2.0)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_roofline_uses_arm_imc_cas_bandwidth_when_present():
  """ARM path accepts arm_imc CAS counters for bandwidth."""
  from hpcperfstats.dbload.lib.monitor_naming.canonical import INTEL_FP_ARITH_ALL_EVENTS

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ == "cpu_counter_metrics" and list(events) == fp_events:
      return pd.DataFrame(
          [("n1.cluster", t0, 5.0)], columns=["host", "time", "sum_val"]
      )
    if typ == "arm_imc" and list(events) == ["CAS_READS", "CAS_WRITES"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.75)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_gpu_roofline_succeeds_with_nvidia_arc_counters():
  """GPU roofline uses arc gpu_flops + arc gpu_io_link_total_bytes (non-zero BW for scatter)."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 20.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 5.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig, reason = plot_and_reason_gpu_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_gpu_roofline_reports_missing_reason_when_bw_missing():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig, reason = plot_and_reason_gpu_roofline_from_jid_table(jt)
  assert fig is None
  assert "Missing strict GPU roofline counters in host_data" in reason
  assert "gpu_io_link_total_bytes" in reason


def test_gpu_roofline_succeeds_for_nvidia_when_flops_and_link_arc_present():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 40.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig, reason = plot_and_reason_gpu_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_gpu_roofline_uses_inferred_peaks_when_explicit_args_missing(monkeypatch):
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 40.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.plot.roofline.infer_gpu_roofline_peak_flops_and_bw_gbps",
      lambda _jt: (321.0, 12.5),
  )
  fig = plot_gpu_roofline_from_jid_table(jt)
  assert fig is not None
  roof_renderer = fig.renderers[0]
  assert max(roof_renderer.data_source.data["perf"]) == 321.0


def test_gpu_roofline_explicit_peak_args_override_inferred_peaks(monkeypatch):
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 40.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 8.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.plot.roofline.infer_gpu_roofline_peak_flops_and_bw_gbps",
      lambda _jt: (321.0, 12.5),
  )
  fig = plot_gpu_roofline_from_jid_table(jt, peak_flops_gf=111.0, peak_bw_gb=7.0)
  assert fig is not None
  roof_renderer = fig.renderers[0]
  assert max(roof_renderer.data_source.data["perf"]) == pytest.approx(111.0)


def test_gpu_roofline_no_traceback_when_hw_peak_bw_is_zero():
  """Zero roofline_hw_peak BW must not raise in GPU roofline prewarm path."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})
  jt.schema = {"roofline_hw_peak": [], "nvidia_gpu": []}

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    evl = list(events)
    if typ == "roofline_hw_peak" and val_col == "value":
      if evl == ["gpu_peak_fp64_flops_per_s"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 4_000_000_000_000.0)],
            columns=["host", "time", "sum_val"],
        )
      if evl == ["gpu_peak_io_link_bw_bytes_per_s"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 0.0)], columns=["host", "time", "sum_val"]
        )
      if evl == ["gpu_peak_mem_bw_bytes_per_s"]:
        return pd.DataFrame(
            [("n1.cluster", t0, 0.0)], columns=["host", "time", "sum_val"]
        )
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 20.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 5.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig, reason = plot_and_reason_gpu_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None


def test_plot_and_reason_gpu_rejects_explicit_zero_peak_bw():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt([("n1.cluster", t0)], {})

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_flops"]:
      return pd.DataFrame([("n1.cluster", t0, 20.0)], columns=["host", "time", "sum_val"])
    if typ == "nvidia_gpu" and val_col == "arc" and list(events) == ["gpu_io_link_total_bytes"]:
      return pd.DataFrame([("n1.cluster", t0, 5.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig, reason = plot_and_reason_gpu_roofline_from_jid_table(
      jt, peak_flops_gf=100.0, peak_bw_gb=0.0
  )
  assert fig is None
  assert reason == ROOFLINE_NOMINAL_PEAKS_INVALID_REASON


def test_plot_and_reason_cpu_rejects_explicit_zero_peak_bw():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  from hpcperfstats.dbload.lib.monitor_naming.canonical import INTEL_FP_ARITH_ALL_EVENTS

  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  hsw = "intel_hsw_imc"
  fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3"):
      return empty
    if typ == "cpu_counter_metrics" and list(events) == fp_events:
      return pd.DataFrame(
          [("n1.cluster", t0, 4.0)], columns=["host", "time", "sum_val"]
      )
    if typ == hsw and list(events) == ["CAS_READS", "CAS_WRITES"]:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.5)], columns=["host", "time", "sum_val"]
      )
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  fig, reason = plot_and_reason_roofline_from_jid_table(
      jt, peak_flops_gf=500.0, peak_bw_gb=0.0
  )
  assert fig is None
  assert reason == ROOFLINE_NOMINAL_PEAKS_INVALID_REASON


def test_build_roofline_figure_returns_none_for_nonpositive_peaks():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  df = pd.DataFrame(
      {
          "host": ["n1.cluster"],
          "time": [t0],
          "flops_gf": [10.0],
          "bw_gb": [2.0],
      }
  )
  assert _build_roofline_figure(
      df,
      peak_flops_gf=100.0,
      peak_bw_gb=0.0,
      title="t",
      help_plot_key="jobDetailPlot_roofline_cpu",
  ) is None
  assert _build_roofline_figure(
      df,
      peak_flops_gf=float("nan"),
      peak_bw_gb=10.0,
      title="t",
      help_plot_key="jobDetailPlot_roofline_cpu",
  ) is None
