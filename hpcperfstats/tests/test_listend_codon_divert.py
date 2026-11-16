"""listend ``ingest_monitor_sample_text`` runs the Codon line batch when enabled.

The Codon kernel source (``feed_collapse.feed_delta_collapse_arc``) stands in
for the baked pyext so the comparison with the CPython path runs on the host.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from hpcperfstats.dbload.lib import sync_timedb_codon_bridge as bridge
from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState
from hpcperfstats.dbload.lib.sync_timedb_sample_ingest import (
  ingest_monitor_sample_text,
)
from hpcperfstats.tests.test_codon_feed_delta_collapse_arc import (
  _load_feed_collapse,
)

_SCHEMA = {
  "cpu": ["user,W=64,U=1", "sys,W=64"],
  "host_mem_job": ["MemUsed,U=1"],
}
_FINISHED_KEYS = (
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "delta",
  "arc",
)


def _messages() -> list[str]:
  return [
    f"%jobs jids=9,10\n{t}.0 9 hosta\ncpu 0 {100 + 7 * k} {5 + k}\n"
    f"host_mem_job 0 {50 + k}\n"
    for k, t in enumerate((1710000000, 1710000010, 1710000020))
  ]


def _kernel_collapse(mod: Any, calls: list[list[str]]) -> Any:
  def _collapse(
    lines: list[str], carry: Any = None, exclude: Any = None, start_idx: int = 0
  ) -> tuple[dict[str, list], list]:
    calls.append(list(lines))
    cols, _proc, raw_out, arc_out, _marks, _n = mod.feed_delta_collapse_arc(
      lines, dict(carry.raw), dict(carry.arc), start_idx, exclude or []
    )
    carry.raw, carry.arc = raw_out, arc_out
    return dict(zip(_FINISHED_KEYS, cols, strict=True)), []

  return _collapse


def _num(v: Any) -> float:
  return math.nan if v is None else float(v)


def _rows(frames: list[Any]) -> dict[tuple, tuple[float, float]]:
  out: dict[tuple, tuple[float, float]] = {}
  for df in frames:
    for _, r in df.iterrows():
      t = (
        r["time"].timestamp() if hasattr(r["time"], "timestamp") else r["time"]
      )
      key = (r["host"], r["type"], str(r["dev"] or ""), r["event"], float(t))
      out[key] = (_num(r["value"]), _num(r["delta"]))
  return out


def _ingest_all(carry: DeltaCarryState) -> list[Any]:
  schema_fast = {t: list(v) for t, v in _SCHEMA.items()}
  return [
    ingest_monitor_sample_text(
      m, schema=_SCHEMA, schema_fast=schema_fast, carry=carry
    )[0]
    for m in _messages()
  ]


def test_listend_codon_divert_matches_cpython(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(bridge, "codon_ingest_parse_enabled", lambda: False)
  want = _rows(_ingest_all(DeltaCarryState()))
  assert any(k[2].startswith("j:") for k in want)

  calls: list[list[str]] = []
  monkeypatch.setattr(bridge, "codon_ingest_parse_enabled", lambda: True)
  monkeypatch.setattr(
    bridge,
    "try_codon_parse_collapse",
    _kernel_collapse(_load_feed_collapse(), calls),
  )
  got = _rows(_ingest_all(DeltaCarryState()))

  assert len(calls) == len(_messages())
  assert calls[0][:2] == [
    "!cpu user,W=64,U=1 sys,W=64",
    "!host_mem_job MemUsed,U=1",
  ]
  assert set(got) == set(want)
  for key, (gv, gd) in got.items():
    wv, wd = want[key]
    assert gv == wv
    assert (math.isnan(gd) and math.isnan(wd)) or math.isclose(gd, wd), key


def test_listend_pool_start_sizes_codon_slots_to_parse_threads(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Every listend parse thread gets its own Codon slot (default is one)."""
  import threading

  from hpcperfstats.dbload.lib import django_bootstrap, listend_db_ingest as ldi

  started = threading.Event()

  def fake_worker_main(worker_idx, work_queue, stop_event, *args, **kwargs):
    del worker_idx, work_queue, args, kwargs
    started.set()
    stop_event.wait(5.0)

  monkeypatch.setattr(django_bootstrap, "ensure_django", lambda: None)
  monkeypatch.setattr(ldi, "_worker_main", fake_worker_main)
  bridge._configure_slot_pool(1)
  pool = ldi.ListendDbIngestPool(
    pool_processes=5, queue_max_gb=0.001, batch_samples=1, enabled=True
  )
  try:
    pool.start()
    assert started.wait(10.0)
    assert bridge.codon_context_pool_size() == 5
  finally:
    pool.stop(join_timeout=5.0)
    bridge._configure_slot_pool(1)
