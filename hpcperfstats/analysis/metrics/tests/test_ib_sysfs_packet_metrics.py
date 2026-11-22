"""IB packet metrics read the sysfs ``port_*_packets`` keys before MAD ``port_*_pkts``.

On Horizon the MAD PortCountersExtended keys read about 4x low (fabric
management clears PortCounters), so a host_ib row carrying both families must
use the kernel's sysfs counters. Rows from before the monitor change carry only
``port_*_pkts`` and still compute. ``port_*_data`` ``value`` is the raw
counter in 4-byte words (``U=4B`` is applied only to ``delta``).
"""

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.dbload.lib.monitor_naming.load_map import (
  load_monitor_rename_map,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  schema_needs_legacy_hardware_decode,
)

MIB = 1024 * 1024

RETIRED_HOST_IB_KEYS = (
  "port_select",
  "counter_select",
  "port_xmit_pkts",
  "port_rcv_pkts",
  "port_unicast_xmit_pkts",
  "port_unicast_rcv_pkts",
  "port_multicast_xmit_pkts",
  "port_multicast_rcv_pkts",
)


class _StubU:
  def __init__(self, type_map, t):
    self._map = type_map
    self.t = np.asarray(t, dtype=np.float64)
    self.job = type(
      "J", (), {"cluster_mean_by_type": {}, "cluster_mean_arc_by_type": {}}
    )()

  def get_type(self, typename):
    return self._map.get(typename, (None, {}))


def _ib(events, rows, t=(0.0, 10.0)):
  schema = metrics._Schema(list(events))
  stats = np.asarray(rows, dtype=np.float64)
  return _StubU({"host_ib": (schema, {"h1": stats})}, t)


_BOTH = (
  "port_xmit_data",
  "port_rcv_data",
  "port_xmit_pkts",
  "port_rcv_pkts",
  "port_xmit_packets",
  "port_rcv_packets",
)
# 2**20 words each way (8 MiB total); sysfs 2048 packets total, MAD 8x low.
_BOTH_ROWS = [
  [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  [2.0**20, 2.0**20, 128.0, 128.0, 1024.0, 1024.0],
]


def test_avg_packetsize_prefers_sysfs_packets_and_scales_words():
  value, typename, units = metrics.avg_packetsize().compute_metric(
    _ib(_BOTH, _BOTH_ROWS)
  )
  assert typename == "host_ib" and units == "MB"
  assert value == pytest.approx((2 * 2.0**20 * 4) / (2048 * MIB))


def test_max_packetrate_prefers_sysfs_packets():
  value, typename, units = metrics.max_packetrate().compute_metric(
    _ib(_BOTH, _BOTH_ROWS)
  )
  assert typename == "host_ib" and units == "#/s"
  assert value == pytest.approx(2048.0 / 10.0)


_OLD = ("port_xmit_data", "port_rcv_data", "port_xmit_pkts", "port_rcv_pkts")
_OLD_ROWS = [[0.0, 0.0, 0.0, 0.0], [2.0**20, 2.0**20, 512.0, 512.0]]


def test_avg_packetsize_old_rows_with_only_mad_pkts():
  value, typename, _ = metrics.avg_packetsize().compute_metric(
    _ib(_OLD, _OLD_ROWS)
  )
  assert typename == "host_ib"
  assert value == pytest.approx((2 * 2.0**20 * 4) / (1024 * MIB))


def test_max_packetrate_old_rows_with_only_mad_pkts():
  value, typename, _ = metrics.max_packetrate().compute_metric(
    _ib(_OLD, _OLD_ROWS)
  )
  assert typename == "host_ib"
  assert value == pytest.approx(1024.0 / 10.0)


def test_retired_host_ib_keys_listed_without_tripping_legacy_decode():
  """Event retirements stay out of the flat ``removed_legacy`` decode gate."""
  data = load_monitor_rename_map()
  assert set(data["removed_legacy_events"]["host_ib"]) == set(
    RETIRED_HOST_IB_KEYS
  )
  assert not set(RETIRED_HOST_IB_KEYS) & set(data["removed_legacy"])
  assert not schema_needs_legacy_hardware_decode(
    "host_ib", [f"{k},E" for k in (*RETIRED_HOST_IB_KEYS, *_BOTH)]
  )
