"""Regression: complex metrics must not KeyError on partial host_data schemas."""

import numpy as np

from hpcperfstats.analysis.metrics import metrics


class _StubUtils:
  def __init__(self, type_map):
    self._map = type_map

  def get_type(self, typename):
    return self._map.get(typename, (None, {}))


def test_schema_contains_partial_columns():
  s = metrics._Schema(["MemTotal", "MemFree"])
  assert "MemTotal" in s
  assert "MemUsed" not in s


def test_mem_hwm_no_keyerror_when_mem_events_incomplete():
  schema = metrics._Schema(["MemTotal", "MemFree"])
  stats = np.zeros((4, len(schema.events)), dtype=np.float64)
  u = _StubUtils({"mem": (schema, {"n1": stats})})
  value, typename, units = metrics.mem_hwm().compute_metric(u)
  assert value is None and typename == "mem" and units == "GiB"


def test_avg_ethbw_no_keyerror_when_net_partial():
  schema = metrics._Schema(["rx_packets"])
  stats = np.zeros((4, 1), dtype=np.float64)
  u = _StubUtils({"net": (schema, {"n1": stats})})
  value, typename, units = metrics.avg_ethbw().compute_metric(u)
  assert value is None and typename == "net" and units == "MB/s"


def test_avg_packetsize_falls_through_partial_ib_ext():
  ib_partial = metrics._Schema(["port_xmit_pkts"])
  net_schema = metrics._Schema(
      ["tx_packets", "rx_packets", "tx_bytes", "rx_bytes"])
  stats_ib = np.zeros((4, 1), dtype=np.float64)
  stats_net = np.array([
      [1.0, 1.0, 100.0, 200.0],
      [2.0, 2.0, 220.0, 440.0],
      [3.0, 3.0, 340.0, 680.0],
      [8.0, 8.0, 880.0, 1760.0],
  ], dtype=np.float64)
  u = _StubUtils({
      "ib_ext": (ib_partial, {"n1": stats_ib}),
      "opa": (None, {}),
      "net": (net_schema, {"n1": stats_net}),
  })
  value, typename, units = metrics.avg_packetsize().compute_metric(u)
  assert typename == "net" and units == "MB"
  assert isinstance(value, float) and value > 0
