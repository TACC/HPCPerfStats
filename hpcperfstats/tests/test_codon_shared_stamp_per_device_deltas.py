"""Shared-node ``j:`` stamps keep counter deltas per hardware device.

Regression for job 79282 (hpcperfstats04, 2026-11-03): every sample carried
``%jobs``, so ingest replaced each core's ``dev`` with ``j:<jid>`` before the
delta step. All cores then shared one delta key and were diffed against each
other, so ``delta``/``arc`` came out as a large constant (IB 1.9 TB/s, NFS
90 PB). Deltas must stay per device; collapse still stores ``dev=j:<jid>``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  DeltaCarryState,
  IncrementalStatsParser,
  build_stats_dataframes,
  compute_deltas_and_arc_chunk,
)

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)

# Four cores with very different absolute counts, each +100 per 60 s sample.
_BASES = (1000.0, 50000.0, 7000.0, 90000.0)
_SAMPLES = 4


def _corpus(shared: bool) -> list[str]:
  """Stats lines for one host; ``shared`` prefixes each sample with ``%jobs``.

  Args:
    shared (bool): Emit a ``%jobs jids=42`` mark before every sample.

  Returns:
    list[str]: Monitor stats lines.
  """
  lines = ["!cpu user,E,W=64,U=cs"]
  for k in range(_SAMPLES):
    if shared:
      lines.append("%jobs jids=42")
    lines.append(f"{1700000000 + 60 * k}.0 42 hostA")
    lines.extend(f"cpu {c} {b + 100 * k}" for c, b in enumerate(_BASES))
  return lines


def _load_feed_collapse():
  """Import the Codon compile unit as plain CPython (host test path).

  Returns:
    module: ``feed_collapse`` module object.
  """
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_shared_delta", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _cpython_rows(lines: list[str]) -> list[tuple[str, float, float]]:
  """Run the CPython feed -> delta -> collapse -> arc path.

  Args:
    lines (list[str]): Stats lines.

  Returns:
    list[tuple[str, float, float]]: ``(dev, delta, arc)`` per output row in
    time order.
  """
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, procs = parser.finish()
  stats_df, _proc_df = build_stats_dataframes(stats, procs)
  out = compute_deltas_and_arc_chunk(stats_df, carry=DeltaCarryState())
  out = out.sort_values("time")
  return list(
    zip(
      out["dev"].tolist(),
      out["delta"].tolist(),
      out["arc"].tolist(),
      strict=True,
    )
  )


def _codon_rows(lines: list[str]) -> list[tuple[str, float, float]]:
  """Run the Codon port ``feed_delta_collapse_arc`` under CPython.

  Args:
    lines (list[str]): Stats lines.

  Returns:
    list[tuple[str, float, float]]: ``(dev, delta, arc)`` per output row in
    time order.
  """
  mod = _load_feed_collapse()
  finished, *_rest = mod.feed_delta_collapse_arc(lines, {}, {})
  times, _h, _j, _t, devs, _e, _u, _v, deltas, arcs = finished
  order = sorted(range(len(times)), key=lambda i: times[i])
  return [(devs[i], deltas[i], arcs[i]) for i in order]


@pytest.mark.parametrize("runner", [_cpython_rows, _codon_rows])
@pytest.mark.parametrize("shared", [False, True])
def test_shared_stamp_keeps_per_device_deltas(runner, shared) -> None:
  """Node sum of per-core deltas is 4 x 100 per sample, stamped or not."""
  rows = runner(_corpus(shared))
  assert len(rows) == _SAMPLES
  want_dev = "j:42" if shared else ""
  assert all(dev == want_dev for dev, _d, _a in rows)
  for _dev, delta, arc in rows[1:]:
    assert delta == pytest.approx(100.0 * len(_BASES))
    assert arc == pytest.approx(100.0 * len(_BASES) / 60.0)
