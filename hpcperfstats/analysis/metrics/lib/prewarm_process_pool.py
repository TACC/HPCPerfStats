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
(pickled NumPy arrays, one memcpy) to a worker, which parks it in its own
handoff so ``persist_job_detail_artifacts_for_jid`` reuses the decoded frame
exactly as the in-thread path did. Artifact-only refs have no handoff entry
and fall back to ``open_job_table`` in the worker, as they do in-thread.

The pool is a free-list of dedicated worker processes (one forkserver
``Process`` plus a ``Pipe`` each), not a shared task queue: a metrics thread
checks a worker out, runs one job on it, and checks it back in. A worker that
exceeds ``metrics_run_stall_timeout_s`` or dies is killed and replaced **on
its own**; no other in-flight prewarm is affected. (A
``ProcessPoolExecutor`` cannot do this: it does not expose which worker runs
which future, and any worker death marks the whole executor broken.)

Sizing: ``[PIPELINE] metrics_prewarm_processes`` (``auto`` = one per metrics
thread; ``0`` keeps prewarm in-thread). Each worker holds one PostgreSQL
connection and is recycled after ``metrics_prewarm_max_tasks_per_child``
jobs for RSS hygiene.

Attributes:
  PREWARM_POOL_KIND: Pool label used in worker process titles.
  UPDATE_METRICS_SCRIPT: Script name used in worker process titles.
  _KILL_JOIN_S: Seconds to wait for a killed worker to exit.
  _POOL: Process-wide pool singleton (lazily created).
  _POOL_LOCK: Guards ``_POOL``.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from django.db import DatabaseError

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.process_title import (
  apply_pool_worker_process_title,
)

PREWARM_POOL_KIND = "prewarm-pool"
UPDATE_METRICS_SCRIPT = "update_metrics.py"
_KILL_JOIN_S = 5.0

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
  Worker task: persist job-detail then job-plot artifacts for one jid.

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
  except Exception as exc:  # reported to the parent as a job failure
    return {
      "ok": False,
      "db_error": False,
      "error": f"{type(exc).__name__}: {exc}",
    }
  finally:
    HANDOFF.discard(jid)


def _worker_main(conn: Any, script_name: str, pool_kind: str) -> None:
  """
  Worker process loop: initialise, then serve ``(jid, table)`` requests.

  A ``None`` message or a closed pipe ends the loop.

  Args:
    conn (Any): Child end of the ``multiprocessing.Pipe``.
    script_name (str): Daemon script basename for the process title.
    pool_kind (str): Pool label for the process title.

  Returns:
    None

  Examples:
    >>> _worker_main(
    ...   conn, "update_metrics.py", "prewarm-pool"
    ... )  # doctest: +SKIP
  """
  _child_initializer(script_name, pool_kind)
  while True:
    try:
      message = conn.recv()
    except EOFError, OSError:
      return
    if message is None:
      return
    jid, table = message
    conn.send(run_prewarm_in_child(str(jid), table))
    del table


class PrewarmWorker:
  """
  One dedicated prewarm worker process behind a pipe.

  Attributes:
    tasks: Jobs run on the current process.
    generation: Processes started so far for this slot (telemetry).
    _ctx: Multiprocessing context (forkserver).
    _proc: Live process or None.
    _conn: Parent end of the pipe or None.
  """

  def __init__(self, ctx: Any) -> None:
    """
    Create the slot; the process starts on first use.

    Args:
      ctx (Any): Multiprocessing context used for ``Process`` and ``Pipe``.

    Returns:
      None

    Examples:
      >>> PrewarmWorker(
      ...   multiprocessing.get_context("forkserver")
      ... )  # doctest: +SKIP
    """
    self._ctx = ctx
    self._proc: Any = None
    self._conn: Any = None
    self.tasks = 0
    self.generation = 0

  def _start(self) -> None:
    """
    Start a fresh worker process for this slot.

    Returns:
      None

    Examples:
      >>> PrewarmWorker(ctx)._start()  # doctest: +SKIP
    """
    parent_conn, child_conn = self._ctx.Pipe(duplex=True)
    proc = self._ctx.Process(
      target=_worker_main,
      args=(child_conn, UPDATE_METRICS_SCRIPT, PREWARM_POOL_KIND),
      daemon=True,
    )
    proc.start()
    child_conn.close()
    self._proc, self._conn = proc, parent_conn
    self.tasks = 0
    self.generation += 1

  def kill(self) -> None:
    """
    Kill this slot's process (if any) and drop the pipe.

    Returns:
      None

    Examples:
      >>> PrewarmWorker(ctx).kill()  # doctest: +SKIP
    """
    proc, conn = self._proc, self._conn
    self._proc = self._conn = None
    if conn is not None:
      with contextlib.suppress(Exception):
        conn.close()
    if proc is not None:
      with contextlib.suppress(Exception):
        proc.kill()
      with contextlib.suppress(Exception):
        proc.join(_KILL_JOIN_S)

  def stop(self) -> None:
    """
    Ask the process to exit after its current job, then kill it.

    Returns:
      None

    Examples:
      >>> PrewarmWorker(ctx).stop()  # doctest: +SKIP
    """
    if self._conn is not None:
      with contextlib.suppress(Exception):
        self._conn.send(None)
    self.kill()

  def run(
    self, jid: str, table: Any, timeout_s: float, max_tasks: int | None
  ) -> dict[str, Any]:
    """
    Run one job on this slot's process and return the child result dict.

    Args:
      jid (str): Job id.
      table (Any): Handoff table to ship, or None.
      timeout_s (float): Seconds to wait for the result; 0 waits forever.
      max_tasks (int | None): Recycle the process after this many jobs.

    Returns:
      dict[str, Any]: The child's result dict (see ``run_prewarm_in_child``).

    Raises:
      PrewarmChildError: The process stalled past ``timeout_s`` or died; the
        process has been killed and the slot will start a fresh one.

    Examples:
      >>> PrewarmWorker(ctx).run("1", None, 900.0, 100)  # doctest: +SKIP
    """
    if self._proc is not None and max_tasks and self.tasks >= max_tasks:
      self.stop()
    if self._proc is None or not self._proc.is_alive():
      self.kill()
      self._start()
    try:
      self._conn.send((jid, table))
      del table
      # The child always answers with a dict, so None marks a timeout.
      result = self._conn.recv() if self._conn.poll(timeout_s or None) else None
    except (EOFError, OSError) as exc:  # BrokenPipeError is an OSError
      self.kill()
      raise PrewarmChildError(f"prewarm worker died jid={jid}: {exc}") from exc
    if result is None:
      self.kill()
      raise PrewarmChildError(
        f"prewarm worker exceeded {timeout_s:.0f}s jid={jid}; killed"
      )
    self.tasks += 1
    return result


class PrewarmProcessPool:
  """
  Bounded free-list of ``PrewarmWorker`` slots with per-worker stall handling.

  Attributes:
    processes: Worker count (free-list size).
    max_tasks_per_child: Jobs before a worker process is recycled (None =
      never).
    stall_timeout_s: Seconds a job may take in a worker before that worker is
      killed (0 = wait forever).
    rebuilds: Workers killed for stall or death so far (telemetry).
    _free: Idle slots; a thread blocks here when every worker is busy.
    _slots: Every slot, for shutdown.
    _log: Line logger.
  """

  def __init__(
    self,
    processes: int,
    *,
    max_tasks_per_child: int | None,
    stall_timeout_s: float,
    log_fn: Callable[[str], Any] = print,
    worker_factory: Callable[[], Any] | None = None,
  ) -> None:
    """
    Create the slots; worker processes start on first use.

    Args:
      processes (int): Worker count (at least 1).
      max_tasks_per_child (int | None): Recycle cadence; None disables.
      stall_timeout_s (float): Per-job cap; 0 waits forever.
      log_fn (Callable[[str], Any]): Line logger.
      worker_factory (Callable[[], Any] | None): Slot constructor (tests
        inject fakes); default builds forkserver ``PrewarmWorker`` slots.

    Returns:
      None

    Examples:
      >>> PrewarmProcessPool(2, max_tasks_per_child=None, stall_timeout_s=0.0)
      <...PrewarmProcessPool object at ...>  # doctest: +SKIP
    """
    self.processes = max(1, int(processes))
    self.max_tasks_per_child = max_tasks_per_child
    self.stall_timeout_s = float(stall_timeout_s)
    self.rebuilds = 0
    self._log = log_fn
    if worker_factory is None:
      ctx = multiprocessing.get_context("forkserver")

      def worker_factory() -> PrewarmWorker:
        """
        Build one forkserver worker slot.

        Returns:
          PrewarmWorker: Idle slot.

        Examples:
          >>> worker_factory()  # doctest: +SKIP
        """
        return PrewarmWorker(ctx)

    self._slots = [worker_factory() for _ in range(self.processes)]
    self._free: queue.Queue[Any] = queue.Queue()
    for slot in self._slots:
      self._free.put(slot)
    self._log(
      f"prewarm process pool: ready processes={self.processes} "
      f"max_tasks_per_child={self.max_tasks_per_child} "
      f"stall_timeout_s={self.stall_timeout_s:.0f}"
    )

  def run(self, jid: Any, table: Any) -> dict[str, Any]:
    """
    Run one job's prewarm on a free worker and return its timings.

    Blocks while every worker is busy. A stall or worker death kills only
    that worker; the slot is returned to the free list and starts a new
    process on its next job.

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
    slot = self._free.get()
    try:
      try:
        result = slot.run(
          jid, table, self.stall_timeout_s, self.max_tasks_per_child
        )
      except PrewarmChildError as exc:
        self.rebuilds += 1
        self._log(
          f"prewarm process pool: worker replaced reason={exc} "
          f"rebuilds={self.rebuilds}"
        )
        raise PrewarmChildError(str(exc)) from exc
    finally:
      del table
      self._free.put(slot)
    if result.get("ok"):
      return result
    if result.get("db_error"):
      raise DatabaseError(str(result.get("error")))
    raise PrewarmChildError(str(result.get("error")))

  def close(self) -> None:
    """
    Stop every worker without waiting for in-flight jobs (shutdown path).

    Returns:
      None

    Examples:
      >>> PrewarmProcessPool(
      ...   1,
      ...   max_tasks_per_child=None,
      ...   stall_timeout_s=0.0,
      ...   worker_factory=object,
      ... ).close()  # doctest: +SKIP
    """
    for slot in self._slots:
      with contextlib.suppress(Exception):
        slot.stop()


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
  Stop prewarm workers at daemon exit; no-op when the pool never started.

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
