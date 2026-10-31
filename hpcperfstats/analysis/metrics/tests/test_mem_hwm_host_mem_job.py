"""mem_hwm prefers host_mem_job over NUMA host_mem when present."""

from unittest.mock import MagicMock

import numpy as np

from hpcperfstats.analysis.metrics.lib.metrics import mem_hwm
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  HOST_MEM_JOB_TYPE,
  HOST_MEM_TYPE,
)


class _SchemaCol:
  def __init__(self, index: int) -> None:
    self.index = index


def test_mem_hwm_prefers_host_mem_job(monkeypatch):
  schema = {"mem_peak": _SchemaCol(0), "mem_used": _SchemaCol(1)}
  stats = {"n0": np.array([[2048.0, 1024.0]], dtype=float)}

  def fake_resolve(u, names):
    names = tuple(names)
    if HOST_MEM_JOB_TYPE in names:
      return schema, stats, HOST_MEM_JOB_TYPE
    raise AssertionError("must not fall through to NUMA when job mem present")

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.metrics.resolve_get_type",
    fake_resolve,
  )
  value, typ, units = mem_hwm().compute_metric(MagicMock())
  assert typ == HOST_MEM_JOB_TYPE
  assert units == "GiB"
  # 2048 KB / 1024**2 = 1/512 GiB
  assert abs(value - (2048.0 / (1024.0**2))) < 1e-12


def test_mem_hwm_falls_back_to_numa(monkeypatch):
  numa_schema = {
    "MemUsed": _SchemaCol(0),
    "Slab": _SchemaCol(1),
    "FilePages": _SchemaCol(2),
  }
  # 3072 - 512 - 512 = 2048 KB
  numa_stats = {"n0": np.array([[3072.0, 512.0, 512.0]], dtype=float)}

  def fake_resolve(u, names):
    names = tuple(names)
    if HOST_MEM_JOB_TYPE in names:
      return None, None, None
    if HOST_MEM_TYPE in names or "mem" in names:
      return numa_schema, numa_stats, HOST_MEM_TYPE
    return None, None, None

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.metrics.resolve_get_type",
    fake_resolve,
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.metrics._schema_has_events_for_type",
    lambda schema, typ, *evs: True,
  )
  value, typ, units = mem_hwm().compute_metric(MagicMock())
  assert typ == HOST_MEM_TYPE
  assert units == "GiB"
  assert abs(value - (2048.0 / (1024.0**2))) < 1e-12
