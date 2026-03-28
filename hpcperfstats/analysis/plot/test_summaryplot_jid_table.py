"""Unit tests for summary plot diagnostics from jid_table aggregates."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.gen.utils import INTEL_FP_ARITH_DOUBLE_EVENTS
from hpcperfstats.analysis.plot.summaryplot import (
    SummaryPlot,
    plot_and_reason_summary_from_jid_table,
)


def test_summary_plot_reports_missing_counter_reason():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = pd.DataFrame(
      [("n1.cluster", t0)],
      columns=["host", "time"],
  )
  jt.get_aggregate_df.return_value = pd.DataFrame(columns=["host", "time", "sum_val"])

  fig, reason = plot_and_reason_summary_from_jid_table(jt)
  assert fig is None
  assert "Missing summary counters in host_data" in reason
  assert "Attempted:" in reason


def test_summaryplot_plot_includes_mbw_from_first_intel_imc_with_data():
  """DRAM mbw uses INTEL_IMC_STATS_TYPES order, not SKX-only."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)
  hsw = "intel_hsw_imc"

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if val_col == "arc" and typ == "cpu" and "user" in events:
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics"):
      if list(events) == fp64:
        return pd.DataFrame(
            [("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"]
        )
      return empty
    if typ == hsw and list(events) == ["CAS_READS", "CAS_WRITES"]:
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

