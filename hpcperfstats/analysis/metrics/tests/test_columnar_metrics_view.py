"""metrics_view pivots a type lazily: same grids as an eager pivot of every
schema type, and a type no metric reads is never pivoted."""

from __future__ import annotations

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)

US = 1_000_000


def _table():
  rng = np.random.default_rng(5)
  rows = []
  for t in range(12):
    for host in ("h1", "h2", "h3"):
      for ev in ("user", "system"):
        for dev in ("0", "1"):
          v = float(np.float32(rng.random() * 100))
          rows.append((t * 60 * US, host, "cpu", ev, dev, 1, v, v / 2, None))
      if host != "h3":
        rows.append(
          (t * 60 * US, host, "mem", "used", None, 1, float(t), None, None)
        )
      # Another tenant's rows only: no owned rows, so no grid.
      rows.append((t * 60 * US, host, "ib", "rx", None, 0, 1.0, 1.0, None))
  rows.append((30 * US, "h1", "gpu", "util", "0", 1, 50.0, None, None))
  schema = {
    "cpu": ["user", "system", "idle"],
    "mem": ["used"],
    "ib": ["rx"],
    "gpu": ["util"],
    "absent": ["x"],
    "no_events": [],
  }
  return table_from_records(rows, jid="9", schema=schema)


def _eager(table):
  """The pre-lazy view: pivot every schema type with events up front."""
  idx = table.index
  cluster, arc, per_host = {}, {}, {}
  for typ, events in table.schema.items():
    if not events:
      continue
    piv = idx.metrics_pivot(typ, list(events))
    if piv["host_ids"].shape[0] == 0:
      continue
    cluster[typ] = piv["cluster_value"]
    arc[typ] = piv["cluster_arc"]
    for k, hid in enumerate(piv["host_ids"].tolist()):
      per_host.setdefault(table.frame.hosts[hid], {})[typ] = piv["per_host"][k]
  axis = np.unique(table.frame.time_us[idx.owned])
  return cluster, arc, per_host, np.floor_divide(axis, US).astype(np.float64)


def test_metrics_view_lazy_matches_eager(monkeypatch):
  table = _table()
  cluster, arc, per_host, times = _eager(table)
  pivoted = []
  real = type(table.index).metrics_pivot

  def counting(self, typ, events):
    pivoted.append(typ)
    return real(self, typ, events)

  monkeypatch.setattr(type(table.index), "metrics_pivot", counting)
  view = table.metrics_view()
  # Building the view pivots nothing; every type stays in the schema.
  assert pivoted == []
  assert list(view.schemas) == list(table.schema)
  assert np.array_equal(view.times, times)
  assert sorted(view.hosts) == ["h1", "h2", "h3"]
  # Key sets match the eager dicts without pivoting.
  assert list(view.cluster_mean_by_type) == list(cluster)
  assert bool(view.cluster_mean_arc_by_type) and pivoted == []
  # One read pivots one type, once, for every host.
  got = view.cluster_mean_by_type.get("mem")
  assert np.array_equal(got, cluster["mem"], equal_nan=True)
  assert np.array_equal(
    view.hosts["h2"].stats["mem"]["agg"], per_host["h2"]["mem"], equal_nan=True
  )
  assert view.hosts["h3"].stats.get("mem") is None
  assert pivoted == ["mem"]
  # Absent, foreign-only and event-less types are missing, not pivoted.
  for typ in ("absent", "ib", "no_events"):
    assert view.cluster_mean_by_type.get(typ) is None
    assert typ not in view.hosts["h1"].stats
  assert pivoted == ["mem"]
  # Every remaining grid equals the eager one.
  for typ in cluster:
    assert np.array_equal(
      view.cluster_mean_by_type[typ], cluster[typ], equal_nan=True
    )
    assert np.array_equal(
      view.cluster_mean_arc_by_type[typ], arc[typ], equal_nan=True
    )
  for host, by_type in per_host.items():
    stats = view.hosts[host].stats
    assert sorted(stats) == sorted(by_type)
    for typ, grid in by_type.items():
      assert np.array_equal(stats[typ]["agg"], grid, equal_nan=True)
  assert sorted(pivoted) == sorted(cluster)


def test_owned_time_axis_is_computed_once(monkeypatch):
  table = _table()
  calls = []
  real_unique = np.unique

  def counting_unique(*args, **kwargs):
    calls.append(1)
    return real_unique(*args, **kwargs)

  first = table.index.owned_time_axis()
  monkeypatch.setattr(np, "unique", counting_unique)
  assert table.index.owned_time_axis() is first
  table.index.metrics_pivot("cpu", ["user"])
  table.index.metrics_pivot("mem", ["used"])
  # Only the per-type host unique runs; the time axis is not recomputed.
  assert len(calls) == 2


def test_pivot_cache_is_bounded_by_bytes_and_repivots_identically():
  """Regression (hs04 OOM, 2026-11-21): job 100430 (162 hosts) held 24
  pivot grids, 19.5 GiB, for the whole metrics run. The cache now evicts
  the least recently used pivot past ``max_bytes``, keeps the newest one,
  and an evicted type pivots again to the same values."""
  from hpcperfstats.analysis.metrics.lib.columnar.kernels import TypePivots

  table = _table()
  events = {"cpu": ["user", "system", "idle"], "mem": ["used"]}
  eager = {t: table.index.metrics_pivot(t, e) for t, e in events.items()}
  pivots = TypePivots(table.index, events, max_bytes=1)
  cpu, _ = pivots.get("cpu")
  assert list(pivots._cache) == ["cpu"]  # newest kept though over the cap
  pivots.get("mem")
  assert list(pivots._cache) == ["mem"]  # cpu evicted
  again, _ = pivots.get("cpu")
  assert again is not cpu and pivots.pivots_built == 3
  for key, grid in eager["cpu"].items():
    assert np.array_equal(again[key], grid, equal_nan=True)
  # A cap that fits both keeps both, and a hit pivots nothing.
  roomy = TypePivots(table.index, events)
  roomy.get("cpu"), roomy.get("mem"), roomy.get("cpu")
  assert roomy.pivots_built == 2 and set(roomy._cache) == {"cpu", "mem"}
  assert sum(roomy._bytes.values()) == sum(
    v.nbytes for p in eager.values() for v in p.values()
  )
