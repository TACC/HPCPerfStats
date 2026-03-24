"""Unit tests for CPI heatmap from jid_table (ORM aggregates)."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.heatmap import plot_from_jid_table


def _make_jt_with_agg(rows_by_event):
  """rows_by_event: dict event_name -> list of (host, time, sum_val) for arc aggregate."""

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del typ, val_col, conv
    ev = events[0]
    rows = rows_by_event.get(ev, [])
    if not rows:
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    return pd.DataFrame(rows, columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_aggregate_df.side_effect = get_aggregate_df
  return jt


def test_plot_from_jid_table_none_without_hosts():
  jt = MagicMock()
  jt.host_list = []
  assert plot_from_jid_table(jt) is None


def test_plot_from_jid_table_uses_aperf_inst_retired():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt_with_agg({
      "APERF": [("n1.cluster", t0, 200.0)],
      "INST_RETIRED": [("n1.cluster", t0, 100.0)],
  })
  fig = plot_from_jid_table(jt)
  assert fig is not None
  assert fig.title.text is not None


def test_plot_from_jid_table_falls_back_to_mperf_when_aperf_missing():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt_with_agg({
      "MPERF": [("n1.cluster", t0, 180.0)],
      "INST_RETIRED": [("n1.cluster", t0, 90.0)],
  })
  fig = plot_from_jid_table(jt)
  assert fig is not None
