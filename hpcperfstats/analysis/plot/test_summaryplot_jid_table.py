"""Unit tests for summary plot diagnostics from jid_table aggregates."""
import pandas as pd
import pytest
from unittest.mock import MagicMock
from bokeh.plotting import figure

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
    if typ in ("amd64_pmc", "amd64_df", "intel_rapl", "ib_ext", "llite"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics"):
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
    if typ == "cpu" and "user" in list(events):
      return pd.DataFrame([("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del df, label, y_range_end
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
    if val_col == "arc" and typ == "cpu" and "user" in list(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "intel_rapl", "ib_ext", "llite"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics"):
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

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del label
    captured_metrics.append(metric)
    if metric == "nv_mem_used_mb":
      mem_used_y_caps.append(y_range_end)
    if metric == "nv_gpu_util":
      gpu_util_y_caps.append(y_range_end)
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
    if val_col == "arc" and typ == "cpu" and "user" in list(events):
      return pd.DataFrame(
          [("n1.cluster", t0, 0.25)], columns=["host", "time", "sum_val"]
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "intel_rapl", "ib_ext", "llite"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics"):
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

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del label, y_range_end
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
    if val_col == "arc" and typ == "cpu" and "user" in list(events):
      return pd.DataFrame(
          [
              ("n1.cluster", t0, 0.25),
              ("n1.cluster", t1, 0.26),
          ],
          columns=["host", "time", "sum_val"],
      )
    if val_col != "arc":
      return empty
    if typ in ("amd64_pmc", "amd64_df", "intel_rapl", "ib_ext", "llite"):
      return empty
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics"):
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

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del label, y_range_end
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


def test_summaryplot_orders_cpu_then_gpu_then_fabricbw():
  """Summary subplot order starts with CPU, then GPU, then FabricBW."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if typ == "cpu" and val_col == "arc" and ev == ["user", "system", "nice"]:
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
    if typ == "ib_ext" and val_col == "arc" and ev == ["port_rcv_data", "port_xmit_data"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics") and val_col == "arc":
      if ev == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del df, label, y_range_end
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured_metrics.index("cpu") < captured_metrics.index("nv_gpu_util")
  assert captured_metrics.index("nv_gpu_util") < captured_metrics.index("ibbw")


def test_summaryplot_orders_cpu_mem_then_gpu_then_fabricbw_last():
  """Order: cpu, mem, gpu metrics, and FabricBW at the end."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("n1.cluster", t0)], columns=["host", "time"])
  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  fp64 = list(INTEL_FP_ARITH_DOUBLE_EVENTS)

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    del conv
    ev = list(events)
    if typ == "cpu" and val_col == "arc" and ev == ["user", "system", "nice"]:
      return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    if typ == "mem" and val_col == "value" and ev == ["MemUsed"]:
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
    if typ == "ib_ext" and val_col == "arc" and ev == ["port_rcv_data", "port_xmit_data"]:
      return pd.DataFrame([("n1.cluster", t0, 10.0)], columns=["host", "time", "sum_val"])
    if typ in ("intel_8pmc3", "intel_4pmc3", "cpu_counter_metrics") and val_col == "arc":
      if ev == fp64:
        return pd.DataFrame([("n1.cluster", t0, 1.0)], columns=["host", "time", "sum_val"])
    return empty

  jt = MagicMock()
  jt.host_list = ["n1.cluster"]
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  summary = SummaryPlot(jt)
  captured_metrics = []

  def fake_plot_metric(df, metric, label, y_range_end=None):
    del df, label, y_range_end
    captured_metrics.append(metric)
    return figure(width=100, height=60)

  summary.plot_metric = fake_plot_metric
  fig = summary.plot()
  assert fig is not None
  assert captured_metrics.index("cpu") < captured_metrics.index("mem")
  assert captured_metrics.index("mem") < captured_metrics.index("nv_gpu_util")
  assert captured_metrics.index("nv_gpu_util") < captured_metrics.index("nv_mem_used_mb")
  assert captured_metrics.index("ibbw") == len(captured_metrics) - 1
