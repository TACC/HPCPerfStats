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
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  host_series,
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.utils import utils
from hpcperfstats.dbload.lib.monitor_naming.load_map import (
  load_monitor_rename_map,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  schema_needs_legacy_hardware_decode,
)

MIB = 1024 * 1024
US = 1_000_000

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
  def __init__(self, type_map):
    self._map = type_map

  def get_type(self, typename):
    return self._map.get(typename, (None, {}))


def _ib(events, rows, t=(0.0, 10.0)):
  """Fake utils with one host_ib host and no arc (per-host value path)."""
  schema = metrics._Schema(list(events))
  rows = np.asarray(rows, dtype=np.float64)
  stats = host_series(rows, t, arc=np.full_like(rows, np.nan))
  return _StubU({"host_ib": (schema, {"h1": stats})})


def _ib_view(rows, arcs):
  """utils over a real metrics view: one host_ib host, 10 s apart."""
  recs = [
    (k * 10 * US, "h1", "host_ib", ev, None, 1, v, a, None)
    for k, (row, arc) in enumerate(zip(rows, arcs, strict=True))
    for ev, v, a in zip(_DATA, row, arc, strict=True)
  ]
  return utils(
    table_from_records(recs, schema={"host_ib": list(_DATA)}).metrics_view()
  )


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


_DATA = ("port_xmit_data", "port_rcv_data")
# 2**20 words each way in 10 s: 8 MiB / 10 s = 0.8 MB/s once words are bytes.
_DATA_ROWS = [[0.0, 0.0], [2.0**20, 2.0**20]]
_DATA_MB_S = (2 * 2.0**20 * 4) / (10.0 * MIB)


def test_max_fabricbw_per_host_value_scales_words():
  u = _ib(_DATA, _DATA_ROWS)
  value, typename, units = metrics.max_fabricbw().compute_metric(u)
  assert typename == "host_ib" and units == "MB/s"
  assert value == pytest.approx(_DATA_MB_S)


def test_max_fabricbw_cluster_value_mean_scales_words():
  u = _ib_view(_DATA_ROWS, [[None, None], [None, None]])
  value, _, _ = metrics.max_fabricbw().compute_metric(u)
  assert value == pytest.approx(_DATA_MB_S)


def test_max_fabricbw_cluster_arc_is_already_bytes():
  rate = 4 * 2.0**20 / 10.0
  u = _ib_view([[0.0, 0.0], [1.0, 1.0]], [[None, None], [rate, rate]])
  value, _, _ = metrics.max_fabricbw().compute_metric(u)
  assert value == pytest.approx(_DATA_MB_S)
