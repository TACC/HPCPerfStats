"""Tests for Grace DCGM unique-watt CPU power aggregation."""

from __future__ import annotations

import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.lib.gen.dcgm_cpu_power_agg import (
    sum_unique_watt_values_per_host_time,
)
from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
    build_node_power_est_dataframe,
    max_node_power_est_w,
)


def test_sum_unique_watt_identical_replicas_collapse_to_one():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  rows = [
      ("h1", t0, f"cpu{i}", 45.0) for i in range(72)
  ]
  raw = pd.DataFrame(rows, columns=["host", "time", "dev", "sum_val"])
  out = sum_unique_watt_values_per_host_time(raw)
  assert len(out) == 1
  assert float(out.iloc[0]["sum_val"]) == 45.0


def test_sum_unique_watt_two_distinct_socket_paints_sum():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  rows = [("h1", t0, "cpu0", 40.0), ("h1", t0, "cpu1", 40.0)]
  rows += [("h1", t0, "cpu36", 50.0), ("h1", t0, "cpu37", 50.0)]
  raw = pd.DataFrame(rows, columns=["host", "time", "dev", "sum_val"])
  out = sum_unique_watt_values_per_host_time(raw)
  assert len(out) == 1
  assert float(out.iloc[0]["sum_val"]) == 90.0


def test_sum_unique_watt_float_noise_rounds_before_unique():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  raw = pd.DataFrame(
      [
          ("h1", t0, 45.0001),
          ("h1", t0, 45.0002),
          ("h1", t0, 45.0004),
      ],
      columns=["host", "time", "sum_val"],
  )
  out = sum_unique_watt_values_per_host_time(raw)
  assert float(out.iloc[0]["sum_val"]) == 45.0


def _jt_with_dcgm_cpu_replicas(
    *,
    watts_by_dev: list[float],
    module_power: float | None = None,
    gpu_power: float | None = None,
) -> MagicMock:
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("h1", t0)], columns=["host", "time"])

  def get_aggregate_df(
      typ,
      val_col,
      events,
      conv=1.0,
      *,
      group_by_dev=False,
  ):
    ev = set(events)
    if typ == "host_cpu_hw" and "dcgm_cpu_power_util_w" in ev:
      if group_by_dev:
        rows = [
            ("h1", t0, f"cpu{i}", float(w) * float(conv))
            for i, w in enumerate(watts_by_dev)
        ]
        return pd.DataFrame(
            rows, columns=["host", "time", "dev", "sum_val"],
        )
      return pd.DataFrame(
          [("h1", t0, sum(watts_by_dev) * float(conv))],
          columns=["host", "time", "sum_val"],
      )
    if typ == "nvidia_gpu" and "module_power_usage" in ev:
      if module_power is None:
        return pd.DataFrame(columns=["host", "time", "sum_val"])
      return pd.DataFrame(
          [("h1", t0, float(module_power) * float(conv))],
          columns=["host", "time", "sum_val"],
      )
    if typ == "nvidia_gpu" and "power_usage" in ev:
      if gpu_power is None:
        return pd.DataFrame(columns=["host", "time", "sum_val"])
      return pd.DataFrame(
          [("h1", t0, float(gpu_power) * float(conv))],
          columns=["host", "time", "sum_val"],
      )
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  schema = {
      "intel_x86_rapl": ["pkg_energy"],
      "amd_x86_rapl": ["pkg_energy"],
      "nvidia_gpu": ["power_usage", "module_power_usage"],
      "host_cpu_hw": ["dcgm_cpu_power_util_w"],
  }
  jt = MagicMock()
  jt.schema = schema
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df
  return jt


def test_node_power_est_identical_cpu_replicas_not_n_times_socket():
  jt = _jt_with_dcgm_cpu_replicas(watts_by_dev=[45.0] * 72)
  df = build_node_power_est_dataframe(jt)
  assert float(df.iloc[0]["dcg_cpu_power_w"]) == 45.0
  assert float(df.iloc[0]["node_power_est_w"]) == 45.0
  assert max_node_power_est_w(jt) == 45.0


def test_node_power_est_two_socket_unique_watts_sum():
  watts = [40.0] * 36 + [50.0] * 36
  jt = _jt_with_dcgm_cpu_replicas(watts_by_dev=watts)
  df = build_node_power_est_dataframe(jt)
  assert float(df.iloc[0]["dcg_cpu_power_w"]) == 90.0
  assert float(df.iloc[0]["node_power_est_w"]) == 90.0


def test_node_power_est_module_power_preferred_over_cpu_replicas():
  jt = _jt_with_dcgm_cpu_replicas(
      watts_by_dev=[45.0] * 72,
      module_power=700.0,
      gpu_power=200.0,
  )
  df = build_node_power_est_dataframe(jt)
  assert float(df.iloc[0]["dcg_cpu_power_w"]) == 45.0
  assert float(df.iloc[0]["node_power_est_w"]) == 700.0
