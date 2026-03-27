"""Unit tests for CPI heatmap from jid_table (ORM aggregates)."""
import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.heatmap import plot_from_jid_table


def _make_jt_with_agg(rows_by_event):
  """rows_by_event: dict event_name -> list of (host, time, sum_val) for arc aggregate."""

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del typ, conv
    ev = events[0]
    keyed_rows = rows_by_event.get((val_col, ev))
    rows = keyed_rows if keyed_rows is not None else rows_by_event.get(ev, [])
    if not rows:
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    return pd.DataFrame(rows, columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.schema = {}
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


def test_plot_from_jid_table_returns_none_when_arc_missing():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt_with_agg({
      ("value", "APERF"): [("n1.cluster", t0, 210.0)],
      ("value", "INST_RETIRED"): [("n1.cluster", t0, 105.0)],
  })
  fig = plot_from_jid_table(jt)
  assert fig is None


def test_plot_from_jid_table_supports_dynamic_type_discovery():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt_with_agg({
      "APERF": [("n1.cluster", t0, 250.0)],
      "INST_RETIRED": [("n1.cluster", t0, 125.0)],
  })
  jt.schema = {
      "custom_pmc_type": ["APERF", "INST_RETIRED"],
  }

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del val_col, conv
    if typ != "custom_pmc_type":
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    ev = events[0]
    rows = [("n1.cluster", t0, 250.0)] if ev == "APERF" else [("n1.cluster", t0, 125.0)]
    return pd.DataFrame(rows, columns=["host", "time", "sum_val"])

  jt.get_aggregate_df.side_effect = get_aggregate_df
  fig = plot_from_jid_table(jt)
  assert fig is not None

def test_plot_from_jid_table_cpu_counter_metrics_typ():
  """CPI heatmap accepts cpu_counter_metrics when APERF/INST_RETIRED arc rows exist."""
  import pandas as pd
  from unittest.mock import MagicMock
  from hpcperfstats.analysis.plot.heatmap import plot_from_jid_table

  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    if typ != "cpu_counter_metrics" or val_col != "arc":
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    ev = events[0]
    if ev == "APERF":
      return pd.DataFrame([("n1.cluster", t0, 300.0)], columns=["host", "time", "sum_val"])
    if ev == "INST_RETIRED":
      return pd.DataFrame([("n1.cluster", t0, 100.0)], columns=["host", "time", "sum_val"])
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.schema = {}
  jt.get_aggregate_df.side_effect = get_aggregate_df

  # Skip intel_8pmc3/4/amd candidates (empty), then cpu_counter_metrics matches.
  assert plot_from_jid_table(jt) is not None
