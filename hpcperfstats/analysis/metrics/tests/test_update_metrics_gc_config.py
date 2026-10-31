"""
Regression tests for update_metrics cyclic-GC configuration and telemetry.

The free-threaded collector is stop-the-world and scans the whole heap; with a
wide metrics pool its pauses cost ~8% of wall time at 8 threads. The scheduler
exposes ``[PIPELINE] metrics_gc_threshold`` and counts collections and pause
seconds on the ``metrics progress`` heartbeat so operators can see it.
"""

from __future__ import annotations

import gc

import pytest

um = pytest.importorskip("hpcperfstats.analysis.metrics.update_metrics")


@pytest.fixture
def _reset_gc_stats():
  saved = dict(um._GC_STATS)
  saved_threshold = gc.get_threshold()
  yield
  um._GC_STATS.clear()
  um._GC_STATS.update(saved)
  gc.set_threshold(*saved_threshold)
  while um._gc_phase_callback in gc.callbacks:
    gc.callbacks.remove(um._gc_phase_callback)


def test_gc_phase_callback_counts_and_times(_reset_gc_stats, monkeypatch):
  um._GC_STATS.update({"collections": 0, "pause_s": 0.0, "_start": 0.0})
  clock = iter([100.0, 100.25, 200.0, 200.5])
  monkeypatch.setattr(um.time, "monotonic", lambda: next(clock))
  um._gc_phase_callback("start", {})
  um._gc_phase_callback("stop", {"collected": 3})
  um._gc_phase_callback("start", {})
  um._gc_phase_callback("stop", {"collected": 0})
  assert um._GC_STATS["collections"] == 2
  assert um._GC_STATS["pause_s"] == pytest.approx(0.75)


def test_apply_gc_config_zero_keeps_interpreter_threshold(
  _reset_gc_stats, monkeypatch
):
  monkeypatch.setattr(um.cfg, "get_metrics_gc_threshold", lambda: 0)
  calls = []
  monkeypatch.setattr(um.gc, "set_threshold", lambda *a: calls.append(a))
  assert um._apply_metrics_gc_config() == 0
  assert calls == []
  assert gc.callbacks.count(um._gc_phase_callback) == 1
  # Registering twice must not duplicate the callback.
  um._apply_metrics_gc_config()
  assert gc.callbacks.count(um._gc_phase_callback) == 1


def test_apply_gc_config_positive_sets_threshold(_reset_gc_stats, monkeypatch):
  monkeypatch.setattr(um.cfg, "get_metrics_gc_threshold", lambda: 150000)
  calls = []
  monkeypatch.setattr(um.gc, "set_threshold", lambda *a: calls.append(a))
  assert um._apply_metrics_gc_config() == 150000
  assert calls == [(150000,)]


def test_main_applies_gc_config_after_slot_budget_log():
  text = open(um.__file__, encoding="utf-8").read()
  body = text[text.find("def main(") :]
  assert body.find("_apply_metrics_gc_config()") > body.find(
    "log_pg_slot_budget_if_needed("
  )
  assert body.find("_apply_metrics_gc_config()") < body.find(
    "_newest_first_metrics_dates("
  )


def test_progress_line_carries_gc_counters():
  text = open(um.__file__, encoding="utf-8").read()
  assert "gc_collections_total={} gc_pause_s_total={:.1f}" in text
  assert '"gc_collections_total": int(_GC_STATS["collections"])' in text
