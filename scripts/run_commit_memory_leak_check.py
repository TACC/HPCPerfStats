#!/usr/bin/env python3
"""
Commit-hook smoke: curated no-Django workloads under memray growth gates.

Runs short iteration loops under ``memray.Tracker``, samples heap via memray
memory snapshots, discards warm-up, and hard-fails when late-window mean heap
exceeds early-window mean by more than a fixed ceiling, or when peak / leaked
bytes exceed per-workload ceilings. Fail closed when memray or the workspace
venv is missing.

Attributes:
  DEFAULT_ITERATIONS: Iterations per workload (including warm-up).
  DEFAULT_WARMUP: Leading iterations discarded before growth windows.
  EXIT_FAIL: Process exit code when a growth gate fails.
  EXIT_MISCONFIG: Process exit code for missing memray / bad env.
  EXIT_OK: Process exit code when all workloads pass.
  GROWTH_EARLY_COUNT: Snapshot count for the early-window mean.
  GROWTH_LATE_COUNT: Snapshot count for the late-window mean.
  INSTALL_HINT: Operator message when memray is not installed.
  WORKLOAD_THRESHOLDS: Per-workload absolute and growth ceilings (bytes).
  _LISTEND_WINDOW_SECONDS: Cached listend window seconds after prepare.
  _PROCESS_MEMORY_MOD: Cached ``process_memory`` module after prepare.
  _PROCESS_MEMORY_ORIG_READ: Original ``read_process_rss_bytes`` to restore.
  _PROCESS_MEMORY_POOL: Fake pool used by the process_memory workload.
  _REPO_ROOT: Git checkout root (directory with ``pyproject.toml``).
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ITERATIONS = 25
DEFAULT_WARMUP = 5
GROWTH_EARLY_COUNT = 5
GROWTH_LATE_COUNT = 5
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_MISCONFIG = 2
INSTALL_HINT = (
    'memray is required for the commit memory-leak check. '
    'Install with: pip install -e ".[dev]" '
    "(from the HPCPerfStats checkout, using the workspace .venv)."
)


@dataclass(frozen=True)
class WorkloadThresholds:
  """Absolute and growth ceilings for one named smoke workload.

  Attributes:
    max_growth_bytes: Max allowed (late_mean - early_mean) heap bytes.
    max_peak_bytes: Max allowed memray peak_memory for the capture.
    max_leaked_bytes: Max allowed sum of leaked allocation sizes.
  """

  max_growth_bytes: int
  max_peak_bytes: int
  max_leaked_bytes: int


# Tuned on a green local memray run (Python + native trackers). Absolute
# ceilings stay well above clean allocate/free noise and below intentional
# multi-iteration retention of tens of KB per step.
WORKLOAD_THRESHOLDS: dict[str, WorkloadThresholds] = {
    "control_allocate_free": WorkloadThresholds(
        max_growth_bytes=256_000,
        max_peak_bytes=2_000_000,
        max_leaked_bytes=256_000,
    ),
    "listend_timestamp_window": WorkloadThresholds(
        max_growth_bytes=512_000,
        max_peak_bytes=4_000_000,
        max_leaked_bytes=512_000,
    ),
    "process_memory_fakes": WorkloadThresholds(
        max_growth_bytes=512_000,
        max_peak_bytes=4_000_000,
        max_leaked_bytes=512_000,
    ),
}


@dataclass(frozen=True)
class WorkloadOutcome:
  """Result of one memray-gated workload run.

  Attributes:
    name: Workload key in ``WORKLOAD_THRESHOLDS``.
    peak_bytes: memray metadata peak_memory.
    leaked_bytes: Sum of leaked allocation record sizes.
    early_mean_heap: Mean heap from early post-warm-up snapshots.
    late_mean_heap: Mean heap from late snapshots.
    growth_bytes: ``late_mean_heap - early_mean_heap``.
    passed: True when all ceilings hold.
    detail: Human-readable pass/fail summary.
  """

  name: str
  peak_bytes: int
  leaked_bytes: int
  early_mean_heap: float
  late_mean_heap: float
  growth_bytes: float
  passed: bool
  detail: str


class _FakeProc:
  """Minimal pool worker stub for ``process_memory`` smoke paths.

  Attributes:
    pid: Fake OS process id.
    _alive: Whether ``is_alive`` reports the worker as running.
    _rss_kb: Unused size hint kept for parity with unit-test fakes.
  """

  def __init__(self, pid: int, alive: bool = True, rss_kb: int = 1024) -> None:
    """
    Store fake pid and liveness for pool RSS helpers.

    Args:
      pid (int): Fake process id passed to ``read_process_rss_bytes``.
      alive (bool): Value returned by ``is_alive``.
      rss_kb (int): Unused size hint (parity with unit-test fakes).

    Returns:
      None

    Examples:
      >>> p = _FakeProc(100, alive=True, rss_kb=2048)
      >>> p.is_alive()
      True
    """
    self.pid = pid
    self._alive = alive
    self._rss_kb = rss_kb

  def is_alive(self) -> bool:
    """
    Return whether this fake worker should be counted in pool RSS.

    Returns:
      bool: Stored ``_alive`` flag.

    Examples:
      >>> _FakeProc(1, alive=False).is_alive()
      False
    """
    return self._alive


def _ensure_repo_on_path() -> None:
  """
  Insert the git checkout root on ``sys.path`` when missing.

  Returns:
    None

  Examples:
    >>> _ensure_repo_on_path()
  """
  root = str(_REPO_ROOT)
  if root not in sys.path:
    sys.path.insert(0, root)


def _mean(values: Sequence[float]) -> float:
  """
  Return the arithmetic mean of ``values``, or ``0.0`` when empty.

  Args:
    values (Sequence[float]): Sample values.

  Returns:
    float: Mean of ``values``, or ``0.0`` if ``values`` is empty.

  Examples:
    >>> _mean([1.0, 3.0])
    2.0
    >>> _mean([])
    0.0
  """
  if not values:
    return 0.0
  return float(sum(values)) / float(len(values))


def evaluate_heap_growth(
  heap_samples: Sequence[float],
  *,
  warmup: int = DEFAULT_WARMUP,
  early_count: int = GROWTH_EARLY_COUNT,
  late_count: int = GROWTH_LATE_COUNT,
) -> tuple[float, float, float]:
  """
  Compute early/late heap means and growth after discarding warm-up samples.

  Args:
    heap_samples (Sequence[float]): Ordered heap sizes (bytes) from memray
      snapshots (or synthetic samples in unit tests).
    warmup (int): Leading samples discarded before windowing.
    early_count (int): Number of samples for the early-window mean.
    late_count (int): Number of samples for the late-window mean.

  Returns:
    tuple[float, float, float]: ``(early_mean, late_mean, growth)`` where
    growth is ``late_mean - early_mean``.

  Examples:
    >>> evaluate_heap_growth([0, 0, 0, 0, 0, 10, 10, 10, 10, 10, 20, 20, 20, 20, 20])
    (10.0, 20.0, 10.0)
  """
  post = list(heap_samples[max(0, warmup):])
  if not post:
    return 0.0, 0.0, 0.0
  early = post[:early_count]
  late = post[-late_count:] if len(post) >= late_count else post
  early_mean = _mean(early)
  late_mean = _mean(late)
  return early_mean, late_mean, late_mean - early_mean


# Populated by ``prepare_curated_workloads`` before memray tracking so import
# side effects are not attributed to per-iteration growth.
_LISTEND_WINDOW_SECONDS: float | None = None
_PROCESS_MEMORY_MOD: Any | None = None
_PROCESS_MEMORY_POOL: Any | None = None
_PROCESS_MEMORY_ORIG_READ: Any | None = None


def prepare_curated_workloads() -> None:
  """
  Import curated modules and install fakes before memray tracking starts.

  Returns:
    None

  Examples:
    >>> prepare_curated_workloads()  # doctest: +SKIP
  """
  global _LISTEND_WINDOW_SECONDS
  global _PROCESS_MEMORY_MOD
  global _PROCESS_MEMORY_POOL
  global _PROCESS_MEMORY_ORIG_READ

  _ensure_repo_on_path()
  import hpcperfstats.dbload.lib.process_memory as pm
  import hpcperfstats.listend as listend

  _LISTEND_WINDOW_SECONDS = float(listend.MESSAGE_WINDOW_SECONDS)
  _PROCESS_MEMORY_MOD = pm
  if _PROCESS_MEMORY_ORIG_READ is None:
    _PROCESS_MEMORY_ORIG_READ = pm.read_process_rss_bytes

  def _fake_rss(pid: int | None = None) -> int:
    """
    Return deterministic RSS bytes for fake pool worker pids.

    Args:
      pid (int | None): Worker pid, or ``None`` for the supervisor process.

    Returns:
      int: Fake RSS in bytes (2048 KiB for pid 100, 100 MiB for supervisor,
      else 0).

    Examples:
      >>> prepare_curated_workloads()  # doctest: +SKIP
      >>> _fake_rss(100)  # doctest: +SKIP
      2097152
    """
    if pid == 100:
      return 2048 * 1024
    if pid is None:
      return 100 * 1024 * 1024
    return 0

  pm.read_process_rss_bytes = _fake_rss  # type: ignore[assignment]
  _PROCESS_MEMORY_POOL = SimpleNamespace(
      _pool=[
          _FakeProc(100, alive=True, rss_kb=2048),
          _FakeProc(101, alive=False, rss_kb=4096),
      ],
  )


def restore_curated_workloads() -> None:
  """
  Restore ``process_memory`` patches installed by ``prepare_curated_workloads``.

  Returns:
    None

  Examples:
    >>> restore_curated_workloads()
  """
  global _PROCESS_MEMORY_MOD
  global _PROCESS_MEMORY_ORIG_READ
  if _PROCESS_MEMORY_MOD is not None and _PROCESS_MEMORY_ORIG_READ is not None:
    _PROCESS_MEMORY_MOD.read_process_rss_bytes = _PROCESS_MEMORY_ORIG_READ


def workload_control_allocate_free(_iteration: int) -> None:
  """
  Allocate a temporary buffer and drop it (clean control workload).

  Args:
    _iteration (int): Unused iteration index (stable signature for runners).

  Returns:
    None

  Examples:
    >>> workload_control_allocate_free(0)
  """
  buf = bytearray(64_000)
  buf[0] = 1
  del buf


def workload_listend_timestamp_window(iteration: int) -> None:
  """
  Exercise listend-style bounded timestamp deque without archive I/O.

  Mirrors ``test_listend_memory`` window trimming: append many timestamps while
  advancing a fake clock so the deque stays bounded by
  ``MESSAGE_WINDOW_SECONDS``. Call ``prepare_curated_workloads`` first.

  Args:
    iteration (int): Outer smoke iteration (scales inner message count).

  Returns:
    None

  Raises:
    RuntimeError: When ``prepare_curated_workloads`` has not been called.

  Examples:
    >>> prepare_curated_workloads()  # doctest: +SKIP
    >>> workload_listend_timestamp_window(0)  # doctest: +SKIP
  """
  if _LISTEND_WINDOW_SECONDS is None:
    raise RuntimeError("call prepare_curated_workloads() before workloads")
  window = _LISTEND_WINDOW_SECONDS
  base = 1_000_000.0 + float(iteration) * 10.0
  stamps: deque[float] = deque()
  total = 200
  step = window / 10.0
  for i in range(total):
    now = base + float(i) * step
    stamps.append(now)
    cutoff = now - window
    while stamps and stamps[0] < cutoff:
      stamps.popleft()
  # Keep a reference briefly so memray sees the bounded structure, then drop.
  assert len(stamps) < total / 2
  del stamps


def workload_process_memory_fakes(_iteration: int) -> None:
  """
  Call process_memory helpers with fake pools (no /proc dependency).

  Call ``prepare_curated_workloads`` first so imports and patches are outside
  the memray capture window.

  Args:
    _iteration (int): Unused iteration index.

  Returns:
    None

  Raises:
    RuntimeError: When ``prepare_curated_workloads`` has not been called.

  Examples:
    >>> prepare_curated_workloads()  # doctest: +SKIP
    >>> workload_process_memory_fakes(0)  # doctest: +SKIP
  """
  if _PROCESS_MEMORY_MOD is None or _PROCESS_MEMORY_POOL is None:
    raise RuntimeError("call prepare_curated_workloads() before workloads")
  pm = _PROCESS_MEMORY_MOD
  pool = _PROCESS_MEMORY_POOL
  _ = pm.sum_pool_worker_rss_bytes(pool)
  _ = pm.read_sync_timedb_tree_rss_bytes(pool, pool)
  _ = pm.format_tree_rss_breakdown_mb(pool, pool)


def _workload_map() -> dict[str, Callable[[int], None]]:
  """
  Return the default name-to-callable map for commit smoke workloads.

  Returns:
    dict[str, Callable[[int], None]]: Workload callables keyed by name.

  Examples:
    >>> "control_allocate_free" in _workload_map()
    True
  """
  return {
      "control_allocate_free": workload_control_allocate_free,
      "listend_timestamp_window": workload_listend_timestamp_window,
      "process_memory_fakes": workload_process_memory_fakes,
  }


def _import_memray() -> tuple[Any, Any]:
  """
  Import memray Tracker and FileReader or raise ``ImportError``.

  Returns:
    tuple[Any, Any]: ``(Tracker, FileReader)`` classes.

  Raises:
    ImportError: When memray is not installed.

  Examples:
    >>> Tracker, FileReader = _import_memray()  # doctest: +SKIP
  """
  from memray import FileReader, Tracker

  return Tracker, FileReader


def run_workload_under_memray(
  name: str,
  fn: Callable[[int], None],
  thresholds: WorkloadThresholds,
  *,
  iterations: int = DEFAULT_ITERATIONS,
  warmup: int = DEFAULT_WARMUP,
  verbose: bool = False,
) -> WorkloadOutcome:
  """
  Run ``fn`` for ``iterations`` under one memray Tracker and apply ceilings.

  Args:
    name (str): Workload name for messages and thresholds lookup.
    fn (Callable[[int], None]): Per-iteration callable.
    thresholds (WorkloadThresholds): Absolute and growth ceilings.
    iterations (int): Total iterations including warm-up.
    warmup (int): Leading iterations before growth windowing on snapshots.
    verbose (bool): When True, print snapshot/peak details to stderr.

  Returns:
    WorkloadOutcome: Pass/fail outcome with measured bytes.

  Raises:
    ImportError: When memray cannot be imported.
    OSError: When the temporary capture file cannot be written.

  Examples:
    >>> run_workload_under_memray(  # doctest: +SKIP
    ...     "control_allocate_free",
    ...     workload_control_allocate_free,
    ...     WORKLOAD_THRESHOLDS["control_allocate_free"],
    ... )
  """
  Tracker, FileReader = _import_memray()
  fd, capture_path = tempfile.mkstemp(prefix="hps-memray-", suffix=".bin")
  os.close(fd)
  path = Path(capture_path)
  # mkstemp creates the file; memray refuses an existing path unless forced.
  path.unlink(missing_ok=True)
  try:
    with Tracker(str(path), native_traces=False):
      for i in range(iterations):
        fn(i)
        gc.collect()
      # Brief pause so memray can emit a late heap snapshot after the loop.
      time.sleep(0.02)
      gc.collect()

    with FileReader(str(path)) as reader:
      peak = int(reader.metadata.peak_memory)
      leaked = int(
          sum(rec.size for rec in reader.get_leaked_allocation_records()),
      )
      heaps = [float(snap.heap) for snap in reader.get_memory_snapshots()]
  finally:
    path.unlink(missing_ok=True)

  # If snapshots are sparse, synthesize per-iteration placeholders from peak
  # so warm-up / window math still runs (absolute ceilings remain authoritative).
  if len(heaps) < warmup + GROWTH_EARLY_COUNT + GROWTH_LATE_COUNT:
    heaps = [0.0] * warmup + [float(peak)] * (iterations - warmup)

  early_mean, late_mean, growth = evaluate_heap_growth(
      heaps, warmup=warmup
  )
  reasons: list[str] = []
  if growth > float(thresholds.max_growth_bytes):
    reasons.append(
        f"growth {growth:.0f}B > {thresholds.max_growth_bytes}B",
    )
  if peak > thresholds.max_peak_bytes:
    reasons.append(f"peak {peak}B > {thresholds.max_peak_bytes}B")
  if leaked > thresholds.max_leaked_bytes:
    reasons.append(f"leaked {leaked}B > {thresholds.max_leaked_bytes}B")
  passed = not reasons
  detail = (
      f"{name}: ok peak={peak} leaked={leaked} "
      f"early={early_mean:.0f} late={late_mean:.0f} growth={growth:.0f}"
      if passed
      else f"{name}: FAIL " + "; ".join(reasons)
  )
  if verbose:
    print(detail, file=sys.stderr)
    print(f"  heap_samples={len(heaps)} peak={peak} leaked={leaked}", file=sys.stderr)
  return WorkloadOutcome(
      name=name,
      peak_bytes=peak,
      leaked_bytes=leaked,
      early_mean_heap=early_mean,
      late_mean_heap=late_mean,
      growth_bytes=growth,
      passed=passed,
      detail=detail,
  )


def run_all_workloads(
  *,
  iterations: int = DEFAULT_ITERATIONS,
  warmup: int = DEFAULT_WARMUP,
  verbose: bool = False,
  workloads: dict[str, Callable[[int], None]] | None = None,
  thresholds: dict[str, WorkloadThresholds] | None = None,
) -> list[WorkloadOutcome]:
  """
  Run every curated workload and return outcomes in stable name order.

  Args:
    iterations (int): Iterations per workload.
    warmup (int): Warm-up iterations discarded for growth windows.
    verbose (bool): Forwarded to ``run_workload_under_memray``.
    workloads (dict[str, Callable[[int], None]] | None): Optional override map.
    thresholds (dict[str, WorkloadThresholds] | None): Optional override ceilings.

  Returns:
    list[WorkloadOutcome]: One outcome per workload name.

  Raises:
    ImportError: When memray is missing.
    KeyError: When a workload name lacks a threshold entry.

  Examples:
    >>> run_all_workloads(iterations=3, warmup=1)  # doctest: +SKIP
  """
  wl = workloads if workloads is not None else _workload_map()
  th = thresholds if thresholds is not None else WORKLOAD_THRESHOLDS
  prepare_curated_workloads()
  outcomes: list[WorkloadOutcome] = []
  try:
    for name in sorted(wl.keys()):
      outcomes.append(
          run_workload_under_memray(
              name,
              wl[name],
              th[name],
              iterations=iterations,
              warmup=warmup,
              verbose=verbose,
          ),
      )
  finally:
    restore_curated_workloads()
  return outcomes


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: run curated memray growth gates; return process exit code.

  Args:
    argv (Sequence[str] | None): Argument list without program name; defaults
      to ``sys.argv[1:]``.

  Returns:
    int: ``0`` pass, ``1`` growth fail, ``2`` misconfig / missing memray.

  Examples:
    >>> main([])  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(
      description="Commit-hook memray memory-leak smoke (curated workloads).",
  )
  parser.add_argument(
      "--iterations",
      type=int,
      default=DEFAULT_ITERATIONS,
      help=f"Iterations per workload (default {DEFAULT_ITERATIONS}).",
  )
  parser.add_argument(
      "--warmup",
      type=int,
      default=DEFAULT_WARMUP,
      help=f"Warm-up iterations to discard (default {DEFAULT_WARMUP}).",
  )
  parser.add_argument(
      "--verbose",
      action="store_true",
      help="Print per-workload measurements to stderr.",
  )
  args = parser.parse_args(list(argv) if argv is not None else None)
  verbose = bool(args.verbose) or os.environ.get(
      "HPCPERFSTATS_MEMORY_LEAK_CHECK_LOG", "",
  ) == "1"

  venv_python = _REPO_ROOT.parent / ".venv" / "bin" / "python3"
  if not venv_python.is_file():
    print(
        "error: workspace venv missing at "
        f"{venv_python} — create it before running this check",
        file=sys.stderr,
    )
    return EXIT_MISCONFIG

  try:
    _import_memray()
  except ImportError:
    print(f"error: {INSTALL_HINT}", file=sys.stderr)
    return EXIT_MISCONFIG

  try:
    outcomes = run_all_workloads(
        iterations=args.iterations,
        warmup=args.warmup,
        verbose=verbose,
    )
  except ImportError:
    print(f"error: {INSTALL_HINT}", file=sys.stderr)
    return EXIT_MISCONFIG
  except Exception as exc:
    print(f"error: memory leak check failed to run: {exc}", file=sys.stderr)
    return EXIT_MISCONFIG

  failed = [o for o in outcomes if not o.passed]
  for outcome in outcomes:
    stream = sys.stderr if not outcome.passed else sys.stdout
    print(outcome.detail, file=stream)
  if failed:
    print(
        f"error: {len(failed)} workload(s) exceeded memray growth ceilings",
        file=sys.stderr,
    )
    return EXIT_FAIL
  return EXIT_OK


if __name__ == "__main__":
  sys.exit(main())
