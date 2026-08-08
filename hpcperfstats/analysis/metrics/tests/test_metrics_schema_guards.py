"""Regression: complex metrics must not KeyError on partial host_data schemas."""

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib import metrics


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
  assert value is None and typename == "host_mem" and units == "GiB"


def test_mem_hwm_snake_case_host_mem_kb_to_gib():
  """Canonical host_mem events (KB) dual-read to finite GiB (Summary scale)."""
  schema = metrics._Schema(["mem_used", "slab", "file_pages"])
  # Peak used−slab−file_pages = 2_097_152 KB → 2.0 GiB
  stats = np.array(
      [
          [3_000_000.0, 500_000.0, 402_848.0],
          [3_500_000.0, 500_000.0, 902_848.0],
      ],
      dtype=np.float64,
  )
  u = _StubUtils({"host_mem": (schema, {"n1": stats})})
  value, typename, units = metrics.mem_hwm().compute_metric(u)
  assert typename == "host_mem" and units == "GiB"
  assert value == pytest.approx(2.0)


def test_mem_hwm_legacy_pascal_case_mem_type_kb_to_gib():
  """Legacy mem + PascalCase events still dual-read; same KB→GiB scale."""
  schema = metrics._Schema(["MemUsed", "Slab", "FilePages"])
  stats = np.array([[2_097_152.0, 0.0, 0.0]], dtype=np.float64)
  u = _StubUtils({"mem": (schema, {"n1": stats})})
  value, typename, units = metrics.mem_hwm().compute_metric(u)
  assert typename == "host_mem" and units == "GiB"
  assert value == pytest.approx(2.0)


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


def test_hashable_metric_events_signature_nested_lists():
  sig = metrics._hashable_metric_events_signature(["user", ["system", "nice"]])
  assert sig == ("user", "system,nice")
  hash(sig)


def test_flatten_event_names_nested_once():
  assert metrics._flatten_event_names_for_host_data_query(
      [["rd_sectors", "wr_sectors"], "irq"]) == ["rd_sectors", "wr_sectors", "irq"]
