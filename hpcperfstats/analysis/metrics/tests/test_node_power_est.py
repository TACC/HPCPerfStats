"""Tests for node_power_est helper (SummaryPlot merge rules on the columnar table)."""

import pandas as pd

from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
  build_node_power_est_dataframe,
  max_node_power_est_w,
  mean_node_power_est_w,
)


def test_max_node_power_est_w_from_table():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 12:01:00+00:00")
  c = 0.00001526
  table = table_from_records(
    [
      (t0, "h1", "intel_x86_rapl", "pkg_energy", None, None, 10.0, None),
      (t1, "h1", "intel_x86_rapl", "pkg_energy", None, None, 20.0, None),
    ],
    schema={
      "intel_x86_rapl": ["pkg_energy"],
      "amd_x86_rapl": ["pkg_energy"],
      "nvidia_gpu": ["power_usage", "module_power_usage"],
      "host_cpu_hw": ["dcgm_cpu_power_util_w"],
    },
  )
  frame = build_node_power_est_dataframe(table)
  assert "node_power_est_w" in frame.columns
  assert max_node_power_est_w(table) == 20.0 * c
  m = mean_node_power_est_w(table)
  assert m is not None
  assert abs(m - 15.0 * c) < 1e-18


# -- D35: per-socket host_power_socket estimate (monitor round 2) -------------

_T0 = pd.Timestamp("2026-11-05 12:00:00+00:00")
_T1 = pd.Timestamp("2026-11-05 12:01:00+00:00")


def _socket_rows(host, socket_w, module_w=0.0, n_sockets=2):
  rows = []
  for t in (_T0, _T1):
    for s in range(n_sockets):
      dev = str(s)
      rows.append(
        (
          t,
          host,
          "host_power_socket",
          "socket_power_w",
          dev,
          socket_w,
          None,
          None,
        )
      )
      rows.append(
        (
          t,
          host,
          "host_power_socket",
          "module_power_w",
          dev,
          module_w,
          None,
          None,
        )
      )
      rows.append(
        (
          t,
          host,
          "host_power_socket",
          "cpu_power_w",
          dev,
          socket_w / 2,
          None,
          None,
        )
      )
  return rows


def _gpu_rows(host, watts_per_gpu, n_gpus):
  return [
    (t, host, "nvidia_gpu", "power_usage", str(g), watts_per_gpu, None, None)
    for t in (_T0, _T1)
    for g in range(n_gpus)
  ]


def _estimates(table):
  frame = build_node_power_est_dataframe(table)
  return frame.columns["node_power_est_w"].tolist()


def test_equal_sockets_sum_by_dev_on_gpu_less_node():
  """Grace/Grace: two equal 45 W sockets give 90 W (no value dedup, Q18)."""
  table = table_from_records(_socket_rows("g1", 45.0))
  assert _estimates(table) == [90.0, 90.0]
  assert max_node_power_est_w(table) == 90.0


def test_gb200_socket_sum_plus_gpu_power():
  """GB200: module 0 -> sum(socket_power_w) + GPU power."""
  rows = _socket_rows("h1", 45.0) + _gpu_rows("h1", 160.0, 2)
  table = table_from_records(rows)
  assert _estimates(table) == [410.0, 410.0]


def test_gh200_module_power_wins():
  """GH200: module_power_w > 0 is the whole estimate (GPU not added)."""
  rows = _socket_rows("v1", 180.0, module_w=900.0, n_sockets=1)
  rows += _gpu_rows("v1", 600.0, 1)
  table = table_from_records(rows)
  assert _estimates(table) == [900.0, 900.0]


def test_history_rows_keep_old_formula():
  """Only the retired keys: per-CPU unique-watt sum + GPU, as before D35."""
  rows = []
  for t in (_T0, _T1):
    for cpu in range(8):
      rows.append(
        (
          t,
          "o1",
          "host_cpu_hw",
          "dcgm_cpu_power_util_w",
          str(cpu),
          45.0,
          None,
          None,
        )
      )
  rows += _gpu_rows("o1", 160.0, 2)
  table = table_from_records(rows)
  # Equal sockets collapse to one 45 W paint under the old unique-watt rule.
  assert _estimates(table) == [365.0, 365.0]


def test_history_module_key_still_wins():
  rows = [
    (t, "o2", "nvidia_gpu", ev, "0", val, None, None)
    for t in (_T0, _T1)
    for ev, val in (("module_power_usage", 700.0), ("power_usage", 500.0))
  ]
  table = table_from_records(rows)
  assert _estimates(table) == [700.0, 700.0]


def test_watt_hours_defined_from_socket_power_alone():
  from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
    job_cpu_gpu_watt_hours,
  )

  table = table_from_records(_socket_rows("g2", 45.0))
  # 90 W for 60 s = 1.5 Wh.
  assert abs(job_cpu_gpu_watt_hours(table) - 1.5) < 1e-9


def test_summary_series_node_power_uses_socket_sum():
  """Summary panel frame: same D35 estimate as Job Detail (sock_* columns)."""
  from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
    build_summary_series,
  )

  rows = _socket_rows("h2", 45.0) + _gpu_rows("h2", 160.0, 2)
  rows += [(t, "h2", "host_cpu", "user", "", 1.0, 1.0, 1.0) for t in (_T0, _T1)]
  series = build_summary_series(table_from_records(rows))
  assert series.frame.columns["sock_power_w"].tolist() == [90.0, 90.0]
  assert series.frame.columns["node_power_est_w"].tolist() == [410.0, 410.0]
  names = [name for name, _label, _y in series.render_specs]
  assert "node_power_est_w" in names
  assert "sock_power_w" not in names
