"""Unit tests for roofline plot diagnostics and fallbacks."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.roofline import plot_and_reason_roofline_from_jid_table


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


def test_roofline_supports_value_fallback_for_amd_counters():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      [("n1.cluster", t0)],
      {
          ("amd64_pmc", "value"): [("n1.cluster", t0, 1000.0)],
          ("amd64_df", "value"): [("n1.cluster", t0, 100.0)],
      },
  )
  fig, reason = plot_and_reason_roofline_from_jid_table(jt)
  assert fig is not None
  assert reason is None

