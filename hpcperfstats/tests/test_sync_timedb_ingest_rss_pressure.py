"""Regression tests for the ingest RSS-threshold hold-and-release gate."""

from __future__ import annotations

import gc
import inspect
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_worker_memory as wm,
)

_MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_log_state():
  qo._RSS_PRESSURE_STATE["last"].clear()
  qo._RSS_PRESSURE_STATE["suppressed"].clear()
  yield
  qo._RSS_PRESSURE_STATE["last"].clear()
  qo._RSS_PRESSURE_STATE["suppressed"].clear()


def test_fill_block_keys_include_skip_rss_pressure():
  assert "skip_rss_pressure" in qo._FILL_BLOCK_KEYS
  assert qo._empty_ingest_fill_stats()["skip_rss_pressure"] == 0
  assert (
    qo._dominant_ingest_fill_block({"skip_rss_pressure": 3, "claim_none": 1})
    == "skip_rss_pressure"
  )


def test_ingest_rss_over_limit_decision():
  assert wm.ingest_rss_over_limit(60 * _MIB, 50) is True
  assert wm.ingest_rss_over_limit(50 * _MIB, 50) is True
  assert wm.ingest_rss_over_limit(49 * _MIB, 50) is False
  # Disabled roof (pct 0 / unknown memory.max) or unreadable RSS fails open.
  assert wm.ingest_rss_over_limit(10**12, 0) is False
  assert wm.ingest_rss_over_limit(0, 50) is False
  assert wm.ingest_rss_over_limit(None, 50) is False


def _arm(monkeypatch, *, rss_mib, limit_mib):
  """Patch RSS/limit readers; return (log lines, log_fn, release calls)."""
  rss = {"mib": rss_mib}
  monkeypatch.setattr(qo, "read_process_rss_bytes", lambda: rss["mib"] * _MIB)
  monkeypatch.setattr(
    qo,
    "effective_process_tree_rss_limit_mib",
    lambda: limit_mib,
  )
  releases: list[int] = []

  def _release() -> None:
    releases.append(1)
    rss["mib"] = rss_mib // 2

  monkeypatch.setattr(wm, "release_heap_under_pressure", _release)
  lines: list[str] = []
  return lines, lambda msg, **_k: lines.append(msg), releases


def test_over_roof_with_ingest_inflight_holds_fill_without_release(
  monkeypatch,
):
  """Work in flight: stop admitting, let it drain, do not collect yet."""
  lines, log_fn, releases = _arm(monkeypatch, rss_mib=900, limit_mib=500)
  assert qo._rss_pressure_hold({"/a": 1}, log_fn=log_fn) is True
  assert releases == []
  assert len(lines) == 1
  assert "rss_hold" in lines[0]
  assert "rss_mib=900" in lines[0]
  assert "limit_mib=500" in lines[0]
  assert "inflight=1" in lines[0]
  # Per-tick polls inside the interval stay quiet (anti-log-spam).
  for _ in range(5):
    assert qo._rss_pressure_hold({"/a": 1}, log_fn=log_fn) is True
  assert len(lines) == 1


def test_over_roof_idle_releases_once_then_admits(monkeypatch):
  """Drained and still over the roof: collect once, then resume admitting."""
  lines, log_fn, releases = _arm(monkeypatch, rss_mib=900, limit_mib=500)
  assert qo._rss_pressure_hold({"/a": 1}, log_fn=log_fn) is True
  assert qo._rss_pressure_hold({}, log_fn=log_fn) is False
  assert releases == [1]
  release_lines = [x for x in lines if "rss_release" in x]
  assert len(release_lines) == 1
  assert "rss_mib_before=900" in release_lines[0]
  assert "rss_mib_after=450" in release_lines[0]
  assert "limit_mib=500" in release_lines[0]


def test_idle_over_roof_never_deadlocks_fill(monkeypatch):
  """Release cannot lower RSS (retained heap): idle must still admit."""
  _lines, log_fn, releases = _arm(monkeypatch, rss_mib=900, limit_mib=500)
  monkeypatch.setattr(
    wm,
    "release_heap_under_pressure",
    lambda: releases.append(1),
  )
  assert qo._rss_pressure_hold({}, log_fn=log_fn) is False
  assert qo._rss_pressure_hold({}, log_fn=log_fn) is False
  assert releases == [1, 1]


@pytest.mark.parametrize(
  ("rss_mib", "limit_mib", "inflight"),
  [
    (100, 500, {"/a": 1}),  # under the roof
    (900, 0, {"/a": 1}),  # roof disabled (pct 0 or unknown memory.max)
    (900, 0, {}),
  ],
)
def test_no_hold_or_release_under_roof_or_disabled(
  monkeypatch, rss_mib, limit_mib, inflight
):
  lines, log_fn, releases = _arm(
    monkeypatch,
    rss_mib=rss_mib,
    limit_mib=limit_mib,
  )
  assert qo._rss_pressure_hold(inflight, log_fn=log_fn) is False
  assert releases == []
  assert lines == []


def test_pressure_log_rate_limited_with_suppressed_n(monkeypatch):
  """Repeated release cycles log once per interval, then report suppressed_n."""
  lines, log_fn, _ = _arm(monkeypatch, rss_mib=900, limit_mib=500)
  # Retained heap: the collect cannot lower RSS, so every idle tick releases.
  monkeypatch.setattr(wm, "release_heap_under_pressure", lambda: None)
  for _ in range(4):
    qo._rss_pressure_hold({}, log_fn=log_fn)
  assert len(lines) == 1
  assert "suppressed_n" not in lines[0]
  monkeypatch.setattr(qo, "_RSS_PRESSURE_LOG_INTERVAL_S", 0.0)
  qo._rss_pressure_hold({}, log_fn=log_fn)
  assert len(lines) == 2
  assert "suppressed_n=3" in lines[1]
  qo._rss_pressure_hold({}, log_fn=log_fn)
  assert len(lines) == 3
  assert "suppressed_n" not in lines[2]


def test_release_heap_under_pressure_collects_and_trims_regardless_of_ini(
  monkeypatch,
):
  """Unlike per-file release, pressure release ignores the INI trim flag."""
  calls: list[str] = []
  monkeypatch.setattr(gc, "collect", lambda *a: calls.append("gc") or 0)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_malloc_trim_after_file",
    lambda: False,
  )
  libc = SimpleNamespace(malloc_trim=lambda n: calls.append("trim") or 1)
  monkeypatch.setattr(wm, "_libc_handle", lambda: libc)
  wm.release_heap_under_pressure()
  assert calls == ["gc", "trim"]


def test_per_file_release_still_follows_ini_flag(monkeypatch):
  """Refactor guard: the shared collect+trim keeps the INI gate per file."""
  calls: list[str] = []
  monkeypatch.setattr(gc, "collect", lambda *a: calls.append("gc") or 0)
  libc = SimpleNamespace(malloc_trim=lambda n: calls.append("trim") or 1)
  monkeypatch.setattr(wm, "_libc_handle", lambda: libc)
  flag = {"on": False}
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_malloc_trim_after_file",
    lambda: flag["on"],
  )
  wm.release_spawn_pool_worker_memory()
  assert calls == []
  flag["on"] = True
  wm.release_spawn_pool_worker_memory()
  assert calls == ["gc", "trim"]


def test_ingest_coordinator_loop_holds_fill_under_rss_pressure():
  """Source contract: the ingest coordinator owns the gate (not MainThread)."""
  src = inspect.getsource(qo._ingest_coordinator_loop)
  assert "_rss_pressure_hold(" in src
