"""Unit tests for roofline plot diagnostics and fallbacks."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.roofline import (
  plot_and_reason_gpu_roofline_from_jid_table,
  plot_and_reason_roofline_from_jid_table,
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
  from hpcperfstats.analysis.gen.utils import INTEL_IMC_STATS_TYPES
  from hpcperfstats.analysis.plot.roofline import plot_and_reason_roofline_from_jid_table

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  hsw = "intel_hsw_imc"

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3"):
      if len(events) > 3:
        return pd.DataFrame([("n1.cluster", t0, 3.0)], columns=["host", "time", "sum_val"])
      return empty
    if typ == hsw and list(events) == ["CAS_READS", "CAS_WRITES"]:
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
  assert hsw in INTEL_IMC_STATS_TYPES


def test_roofline_succeeds_with_cpu_counter_metrics_flops_and_imc_bw():
  """Intel roofline FLOPS can come from cpu_counter_metrics (aligned with avg_flops)."""
  from hpcperfstats.analysis.gen.utils import INTEL_FP_ARITH_ALL_EVENTS

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
  from hpcperfstats.analysis.gen.utils import INTEL_FP_ARITH_ALL_EVENTS

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
