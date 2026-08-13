"""Unit tests for commit-hook memray memory-leak smoke."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_commit_memory_leak_check.py"


def _load_checker():
  spec = importlib.util.spec_from_file_location(
      "run_commit_memory_leak_check",
      _SCRIPT,
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


pytest.importorskip("memray")
checker = _load_checker()


def test_evaluate_heap_growth_detects_late_increase():
  early = [10.0] * 5
  late = [50.0] * 5
  samples = [0.0] * 5 + early + late
  early_mean, late_mean, growth = checker.evaluate_heap_growth(samples, warmup=5)
  assert early_mean == 10.0
  assert late_mean == 50.0
  assert growth == 40.0


def test_clean_control_workload_passes_gate():
  thresholds = checker.WorkloadThresholds(
      max_growth_bytes=256_000,
      max_peak_bytes=2_000_000,
      max_leaked_bytes=256_000,
  )
  outcome = checker.run_workload_under_memray(
      "control_allocate_free",
      checker.workload_control_allocate_free,
      thresholds,
      iterations=15,
      warmup=3,
  )
  assert outcome.passed, outcome.detail


def test_injected_retention_fails_gate():
  retained: list[bytearray] = []

  def _leaky(_iteration: int) -> None:
    retained.append(bytearray(80_000))

  thresholds = checker.WorkloadThresholds(
      max_growth_bytes=100_000,
      max_peak_bytes=500_000,
      max_leaked_bytes=200_000,
  )
  outcome = checker.run_workload_under_memray(
      "injected_leak",
      _leaky,
      thresholds,
      iterations=20,
      warmup=3,
  )
  assert not outcome.passed, outcome.detail
  assert outcome.leaked_bytes > thresholds.max_leaked_bytes or (
      outcome.growth_bytes > thresholds.max_growth_bytes
      or outcome.peak_bytes > thresholds.max_peak_bytes
  )


def test_run_all_curated_workloads_pass():
  outcomes = checker.run_all_workloads(iterations=15, warmup=3)
  assert outcomes
  assert all(o.passed for o in outcomes), [o.detail for o in outcomes]


def test_main_exits_zero_on_clean_tree():
  assert checker.main(["--iterations", "12", "--warmup", "2"]) == checker.EXIT_OK
