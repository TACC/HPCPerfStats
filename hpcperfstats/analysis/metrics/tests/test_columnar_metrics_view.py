"""metrics_view pivots a type lazily: same per-host series as an eager pivot
of every schema type, a type no metric reads is never pivoted, and the pivot
is linear in the frame's rows."""

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
  """Pivot every schema type with events up front: per host, the
  ``HostSeries`` fields of its slice of the type's pivot."""
  idx = table.index
  pivots, per_host = {}, {}
  for typ, events in table.schema.items():
    if not events:
      continue
    piv = idx.metrics_pivot(typ, list(events))
    if piv["host_ids"].shape[0] == 0:
      continue
    pivots[typ] = piv
    off = piv["host_offsets"]
    for k, hid in enumerate(piv["host_ids"].tolist()):
      sl = slice(off[k], off[k + 1])
      per_host.setdefault(table.frame.hosts[hid], {})[typ] = {
        f: piv[col][sl]
        for f, col in (
          ("t_s", "times_s"),
          ("t_us", "times_us"),
          ("values", "value"),
          ("arc", "arc"),
        )
      }
  return pivots, per_host


def test_metrics_view_lazy_matches_eager(monkeypatch):
  table = _table()
  pivots, per_host = _eager(table)
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
  assert sorted(view.hosts) == ["h1", "h2", "h3"]
  assert view.pivots.types == list(pivots)
  # One read pivots one type, once, for every host.
  got = view.hosts["h2"].stats["mem"]["agg"]
  assert np.array_equal(got.values, per_host["h2"]["mem"]["values"])
  assert view.hosts["h3"].stats.get("mem") is None
  assert pivoted == ["mem"]
  # Absent, foreign-only and event-less types are missing, not pivoted.
  for typ in ("absent", "ib", "no_events"):
    assert view.pivots.get(typ) is None
    assert typ not in view.hosts["h1"].stats
  assert pivoted == ["mem"]
  # Every host's series equals the eager slice, on the host's own times
  # (the gpu type only has h1's one sample).
  for host, by_type in per_host.items():
    stats = view.hosts[host].stats
    assert sorted(stats) == sorted(by_type)
    for typ, fields in by_type.items():
      series = stats[typ]["agg"]
      for name, arr in fields.items():
        assert np.array_equal(getattr(series, name), arr, equal_nan=True)
  assert view.hosts["h1"].stats["gpu"]["agg"].t_us.tolist() == [30 * US]
  assert view.hosts["h1"].stats["cpu"]["agg"].shape == (12, 3)
  assert sorted(pivoted) == sorted(pivots)


def _offset_table(n_hosts, n_samples=40, spread_s=50.0):
  """``n_hosts`` hosts at 60 s with offsets spread over ``spread_s``."""
  rows = []
  for h in range(n_hosts):
    off = spread_s * h / max(n_hosts - 1, 1)
    for k in range(n_samples):
      t = round((60 * k + off) * US)
      for ev in ("user", "system"):
        rows.append((t, f"h{h:03d}", "cpu", ev, None, 1, 1.0 * k, 1.0, None))
  return table_from_records(
    rows, jid=f"off{n_hosts}", schema={"cpu": ["user", "system"]}
  )


def test_pivot_bytes_linear_in_rows():
  """Regression (hs04 job 100430, 162 hosts: 0.81 GiB per grid): the pivot
  was hosts x the union of every host's offset sample times, so doubling
  the hosts at a fixed offset spread quadrupled it. Per-host axes keep one
  row per (host, sample) pair: doubling the hosts doubles the bytes."""

  def nbytes(table):
    """Bytes of the per-pair arrays (host ids and offsets are per host)."""
    piv = table.index.metrics_pivot("cpu", ["user", "system"])
    return sum(piv[k].nbytes for k in ("times_us", "times_s", "value", "arc"))

  assert nbytes(_offset_table(16)) == 2 * nbytes(_offset_table(8))
  pairs = 16 * 40
  piv = _offset_table(16).index.metrics_pivot("cpu", ["user", "system"])
  assert piv["value"].shape == (pairs, 2) and piv["times_us"].shape == (pairs,)


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
