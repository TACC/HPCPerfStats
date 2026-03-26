"""Unit tests for summary plot diagnostics from jid_table aggregates."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.summaryplot import plot_and_reason_summary_from_jid_table


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

