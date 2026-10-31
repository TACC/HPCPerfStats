"""
Worker processes for the update_metrics Bokeh prewarm.

On the free-threaded pipeline interpreter the Bokeh detail/plot prewarm does
not scale across threads: after the type-lock fix it still serialises on the
``sys.modules`` dict and the ``_abc`` cache sets (per-object critical
sections), and the stop-the-world cyclic GC scans every thread's heap. A
worker *process* has its own interpreter, so each prewarm runs at serial
speed. Measured on hpcperfstats-dev (108 jobs): 16 threads 7.3 jobs/min,
16 processes 25.8, 28 processes 40.4 (near-linear).

Metrics compute stays on the metrics thread pool. The thread that finished a
job's metrics pops its ``ColumnarJobTable`` from the handoff and ships it
(pickled NumPy arrays, one memcpy) to the child, which parks it in its own
handoff so ``persist_job_detail_artifacts_for_jid`` reuses the decoded frame
exactly as the in-thread path did. Artifact-only refs have no handoff entry
and fall back to ``open_job_table`` in the child, as they do in-thread.

Sizing: ``[PIPELINE] metrics_prewarm_processes`` (``auto`` = one per metrics
thread; ``0`` keeps prewarm in-thread). Each child holds one PostgreSQL
connection. A child that exceeds ``metrics_run_stall_timeout_s`` fails that
job and the pool is rebuilt (hung workers are killed; other in-flight
prewarms in that pool fail into failure-memory backoff).

Attributes:
  PREWARM_POOL_KIND: Pool label used in child process titles.
  UPDATE_METRICS_SCRIPT: Script name used in child process titles.
  _POOL: Process-wide pool singleton (lazily created).
  _POOL_LOCK: Guards ``_POOL``.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import threading
import time
from collections.abc import Callable
from concurrent.futures import (
  CancelledError,
  ProcessPoolExecutor,
  TimeoutError as FutureTimeoutError,
)
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from django.db import DatabaseError

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.process_title import (
  apply_pool_worker_process_title,
)

PREWARM_POOL_KIND = "prewarm-pool"
UPDATE_METRICS_SCRIPT = "update_metrics.py"

_POOL: PrewarmProcessPool | None = None
_POOL_LOCK = threading.Lock()


class PrewarmChildError(RuntimeError):
  """A prewarm worker process failed, stalled, or died for one job."""


def _child_initializer(script_name: str, pool_kind: str) -> None:
  """
  Worker start: title, Bokeh shim, BLAS caps, Django, no inherited sockets.

  Args:
    script_name (str): Daemon script basename for the process title.
    pool_kind (str): Pool label for the process title.

  Returns:
    None

  Examples:
    >>> _child_initializer(
    ...   "update_metrics.py", "prewarm-pool"
    ... )  # doctest: +SKIP
  """
  apply_pool_worker_process_title(script_name, pool_kind)
  import hpcperfstats.analysis.metrics.lib.plot.bokeh_validation_patch  # noqa: F401
  from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env

  configure_blas_thread_env()
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()
  from django.db import connections

  connections.close_all()


def run_prewarm_in_child(jid: str, table: Any) -> dict[str, Any]:
  """
  Child task: persist job-detail then job-plot artifacts for one jid.

  Args:
    jid (str): Job id.
    table (Any): The job's ``ColumnarJobTable`` from the parent's handoff, or
      None (the persisters then open the table from PostgreSQL).

  Returns:
    dict[str, Any]: ``{"ok": True, "detail_s", "plots_s"}`` or
    ``{"ok": False, "db_error": bool, "error": str}``; database errors are
    flagged so the parent keeps its transient-error retry.

  Examples:
    >>> run_prewarm_in_child("1", None)  # doctest: +SKIP
  """
  from django.db import InterfaceError, close_old_connections

  from hpcperfstats.analysis.metrics.lib.columnar.job_table import HANDOFF
  from hpcperfstats.site.lib.machine.job_detail_artifacts import (
    persist_job_detail_artifacts_for_jid,
  )
  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    persist_job_plot_artifacts_for_jid,
  )

  close_old_connections()
  if table is not None:
    HANDOFF.put(table)
  shared: dict[str, Any] = {}
  try:
    t0 = time.monotonic()
    persist_job_detail_artifacts_for_jid(jid, context=shared)
    t1 = time.monotonic()
    persist_job_plot_artifacts_for_jid(jid, context=shared)
    return {"ok": True, "detail_s": t1 - t0, "plots_s": time.monotonic() - t1}
  except (DatabaseError, InterfaceError) as exc:
    return {
      "ok": False,
      "db_error": True,
      "error": f"{type(exc).__name__}: {exc}",
    }
  except Exception as exc:
    return {
      "ok": False,
      "db_error": False,
      "error": f"{type(exc).__name__}: {exc}",
    }
  finally:
    HANDOFF.discard(jid)


class PrewarmProcessPool:
  """
  Forkserver process pool that runs ``run_prewarm_in_child`` with a stall cap.

  Attributes:
    processes: Worker count.
    max_tasks_per_child: Tasks before a worker is recycled (None = never).
    stall_timeout_s: Seconds a job may take in a child before the pool is
      rebuilt (0 = wait forever).
    generation: Incremented on every rebuild (telemetry).
    _executor: Live executor or None.
    _lock: Guards executor replacement.
    _log: Line logger.
  """

  def __init__(
    self,
    processes: int,
    *,
    max_tasks_per_child: int | None,
    stall_timeout_s: float,
    log_fn: Callable[[str], Any] = print,
  ) -> None:
    """
    Create the pool wrapper; workers start on first use.

    Args:
      processes (int): Worker count (at least 1).
      max_tasks_per_child (int | None): Recycle cadence; None disables.
      stall_timeout_s (float): Per-job cap; 0 waits forever.
      log_fn (Callable[[str], Any]): Line logger.

    Returns:
      None

    Examples:
      >>> PrewarmProcessPool(2, max_tasks_per_child=None, stall_timeout_s=0.0)
      <...PrewarmProcessPool object at ...>  # doctest: +SKIP
    """
    self.processes = max(1, int(processes))
    self.max_tasks_per_child = max_tasks_per_child
    self.stall_timeout_s = float(stall_timeout_s)
    self.generation = 0
    self._executor: ProcessPoolExecutor | None = None
    self._lock = threading.Lock()
    self._log = log_fn

  def _ensure(self) -> ProcessPoolExecutor:
    """
    Return the live executor, creating it when absent.

    Returns:
      ProcessPoolExecutor: Executor with forkserver workers.

    Examples:
      >>> PrewarmProcessPool(
      ...   1, max_tasks_per_child=None, stall_timeout_s=0.0
      ... )._ensure()  # doctest: +SKIP
    """
    with self._lock:
      if self._executor is None:
        self._executor = ProcessPoolExecutor(
          max_workers=self.processes,
          mp_context=multiprocessing.get_context("forkserver"),
          initializer=_child_initializer,
          initargs=(UPDATE_METRICS_SCRIPT, PREWARM_POOL_KIND),
          max_tasks_per_child=self.max_tasks_per_child,
        )
        self._log(
          f"prewarm process pool: started processes={self.processes} "
          f"max_tasks_per_child={self.max_tasks_per_child} "
          f"stall_timeout_s={self.stall_timeout_s:.0f} generation={self.generation}"
        )
      return self._executor

  def _rebuild(self, reason: str, jid: str) -> None:
    """
    Kill the current workers and drop the executor; the next run recreates it.

    Args:
      reason (str): ``stall`` or ``broken`` (log token).
      jid (str): Job that triggered the rebuild.

    Returns:
      None

    Examples:
      >>> PrewarmProcessPool(
      ...   1, max_tasks_per_child=None, stall_timeout_s=0.0
      ... )._rebuild("stall", "1")  # doctest: +SKIP
    """
    with self._lock:
      executor, self._executor = self._executor, None
      self.generation += 1
    self._log(
      f"prewarm process pool: rebuild reason={reason} jid={jid} "
      f"generation={self.generation}"
    )
    _shutdown_executor(executor)

  def run(self, jid: Any, table: Any) -> dict[str, Any]:
    """
    Run one job's prewarm in a worker and return its timings.

    Args:
      jid (Any): Job id.
      table (Any): Handoff table to ship, or None.

    Returns:
      dict[str, Any]: ``{"ok": True, "detail_s", "plots_s"}``.

    Raises:
      DatabaseError: The child hit a database error (callers retry).
      PrewarmChildError: The child failed, stalled past the cap, or died.

    Examples:
      >>> PrewarmProcessPool(
      ...   1, max_tasks_per_child=None, stall_timeout_s=0.0
      ... ).run("1", None)  # doctest: +SKIP
    """
    jid = str(jid)
    future = self._ensure().submit(run_prewarm_in_child, jid, table)
    del table  # the child owns the frame now; do not hold it in two places
    try:
      result = future.result(timeout=self.stall_timeout_s or None)
    except FutureTimeoutError:
      self._rebuild("stall", jid)
      raise PrewarmChildError(
        f"prewarm child exceeded {self.stall_timeout_s:.0f}s jid={jid}"
      ) from None
    except (BrokenProcessPool, CancelledError) as exc:
      self._rebuild("broken", jid)
      raise PrewarmChildError(f"prewarm pool broken jid={jid}: {exc}") from exc
    if result.get("ok"):
      return result
    if result.get("db_error"):
      raise DatabaseError(str(result.get("error")))
    raise PrewarmChildError(str(result.get("error")))

  def close(self) -> None:
    """
    Stop the workers without waiting for in-flight jobs (shutdown path).

    Returns:
      None

    Examples:
      >>> PrewarmProcessPool(
      ...   1, max_tasks_per_child=None, stall_timeout_s=0.0
      ... ).close()
    """
    with self._lock:
      executor, self._executor = self._executor, None
    _shutdown_executor(executor)


def _shutdown_executor(executor: ProcessPoolExecutor | None) -> None:
  """
  Kill an executor's workers and release it; tolerates None.

  Args:
    executor (ProcessPoolExecutor | None): Executor to tear down.

  Returns:
    None

  Examples:
    >>> _shutdown_executor(None)
  """
  if executor is None:
    return
  for proc in list((getattr(executor, "_processes", None) or {}).values()):
    with contextlib.suppress(Exception):  # already-dead workers
      proc.kill()
  executor.shutdown(wait=False, cancel_futures=True)


def get_prewarm_process_pool(
  log_fn: Callable[[str], Any] = print,
) -> PrewarmProcessPool | None:
  """
  Return the process-wide prewarm pool, or None when prewarm stays in-thread.

  Args:
    log_fn (Callable[[str], Any]): Line logger used when the pool is created.

  Returns:
    PrewarmProcessPool | None: Pool sized by ``metrics_prewarm_processes``;
    None when that resolves to 0.

  Examples:
    >>> get_prewarm_process_pool()  # doctest: +SKIP
  """
  global _POOL
  processes = int(cfg.get_metrics_prewarm_processes())
  if processes <= 0:
    return None
  with _POOL_LOCK:
    if _POOL is None:
      recycle = int(cfg.get_metrics_prewarm_max_tasks_per_child())
      _POOL = PrewarmProcessPool(
        processes,
        max_tasks_per_child=recycle or None,
        stall_timeout_s=float(cfg.get_metrics_run_stall_timeout_s()),
        log_fn=log_fn,
      )
    return _POOL


def shutdown_prewarm_process_pool() -> None:
  """
  Kill prewarm workers at daemon exit; no-op when the pool never started.

  Returns:
    None

  Examples:
    >>> shutdown_prewarm_process_pool()
  """
  global _POOL
  with _POOL_LOCK:
    pool, _POOL = _POOL, None
  if pool is not None:
    pool.close()
