"""Metrics-local sliding-window feeder with sample-count idle-slot fill."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from hpcperfstats.analysis.metrics.lib.metrics_idle_slot_supplement import (
  pop_supplement_refs_from_ready_queue,
)


def resolve_metrics_pool_max_inflight(pool: Any, fallback: int = 1) -> int:
  """
  Bound outstanding apply_async work to the live pool process count.

  Args:
    pool (Any): multiprocessing.Pool or test double.
    fallback (int): Used when the pool does not expose worker count.

  Returns:
    int: Max concurrent in-flight tasks (>= 1).

  Examples:
    >>> resolve_metrics_pool_max_inflight(None, fallback=4)
    4
  """
  if pool is None:
    return max(1, int(fallback))
  workers = getattr(pool, "_processes", None)
  if workers is None:
    pool_list = getattr(pool, "_pool", None)
    try:
      workers = len(pool_list) if pool_list is not None else None
    except TypeError:
      workers = None
  if workers is None:
    return max(1, int(fallback))
  try:
    return max(1, int(workers))
  except (TypeError, ValueError):
    return max(1, int(fallback))


def original_batch_work_inflight(pending: list[dict[str, Any]]) -> bool:
  """
  True when any original-batch jid still has metrics or prewarm in flight.

  Args:
    pending (list[dict[str, Any]]): Inflight task dicts with ``is_original``.

  Returns:
    bool: Whether RC-E should still allow supplements.

  Examples:
    >>> original_batch_work_inflight([{"is_original": True}])
    True
    >>> original_batch_work_inflight([{"is_original": False}])
    False
  """
  return any(bool(item.get("is_original")) for item in pending)


def should_use_metrics_sliding_session(
  *,
  supplement_enabled: bool,
  shared_pool: Any,
) -> bool:
  """
  Gate sliding feeder: INI enable plus pool ``apply_async``.

  Args:
    supplement_enabled (bool): ``metrics_idle_slot_supplement_enabled``.
    shared_pool (Any): Metrics process pool, or None.

  Returns:
    bool: True when the sliding session path should run.

  Examples:
    >>> class _P:
    ...     def apply_async(self, *a, **k):
    ...         return None
    >>> should_use_metrics_sliding_session(
    ...     supplement_enabled=True, shared_pool=_P())
    True
    >>> should_use_metrics_sliding_session(
    ...     supplement_enabled=True, shared_pool=None)
    False
  """
  if not supplement_enabled or shared_pool is None:
    return False
  return callable(getattr(shared_pool, "apply_async", None))


def pop_idle_slot_supplements(
  ready_queue: Any,
  ready_queue_lock: Any,
  *,
  max_n: int,
  soft_max: int,
  hard_max: int,
  original_still_inflight: bool,
) -> list[Any]:
  """
  Pop supplement refs under optional lock (RC-E + sample caps).

  Args:
    ready_queue (Any): Scheduler ready queue deque/list.
    ready_queue_lock (Any): Lock guarding the queue, or None.
    max_n (int): Slots to fill.
    soft_max (int): Soft sample preference ceiling.
    hard_max (int): Hard sample reject ceiling.
    original_still_inflight (bool): RC-E gate.

  Returns:
    list[Any]: Selected supplement refs.

  Examples:
    >>> from collections import deque
    >>> from types import SimpleNamespace
    >>> q = deque([SimpleNamespace(jid="s", estimated_sample_count=5)])
    >>> [r.jid for r in pop_idle_slot_supplements(
    ...     q, None, max_n=1, soft_max=10, hard_max=80,
    ...     original_still_inflight=True)]
    ['s']
  """
  if max_n <= 0 or ready_queue is None:
    return []

  def _pop() -> list[Any]:
    """
    Pop ready-queue supplements using the shared soft/hard sample caps.

    Returns:
      list[Any]: Selected supplement refs (may be empty).

    Examples:
      >>> _pop()  # doctest: +SKIP
    """
    return pop_supplement_refs_from_ready_queue(
        ready_queue,
        max_n=max_n,
        soft_max=soft_max,
        hard_max=hard_max,
        original_batch_still_inflight=original_still_inflight,
    )

  if ready_queue_lock is None:
    return _pop()
  with ready_queue_lock:
    return _pop()


def run_metrics_sliding_session(
  *,
  primary_refs: list[Any],
  metrics_obj: Any,
  shared_pool: Any,
  unwrap_fn: Any,
  persist_fn: Any,
  prewarm_worker_fn: Any,
  inline_prewarm_fn: Any | None,
  prewarm_mode: str,
  max_inflight: int,
  poll_timeout_s: float,
  stall_timeout_s: float,
  ready_queue: Any | None = None,
  ready_queue_lock: Any | None = None,
  soft_max: int = 10000,
  hard_max: int = 80000,
  supplement_enabled: bool = True,
  shutdown_requested: Any | None = None,
  progress_callback: Any | None = None,
  abort_if_pool_dead_fn: Any | None = None,
  on_stall_reset: Any | None = None,
  on_poll_hygiene_fn: Any | None = None,
  empty_supplement_sleep_s: float = 0.05,
  on_supplements_taken: Any | None = None,
) -> list[dict[str, Any]]:
  """
  Pool-sized sliding metrics+prewarm session with sample-count idle-slot fill.

  Does not call ingest ``imap_sliding_window_watch_pool``. Primary refs are
  drained first; when the primary iterator is empty and original-batch work
  remains in flight, idle slots fill from ``ready_queue`` under soft/hard
  sample caps (RC-E stops when no originals remain in flight).

  Args:
    primary_refs (list[Any]): Original compute-batch candidate refs.
    metrics_obj (Any): Metrics manager instance passed to ``unwrap_fn``.
    shared_pool (Any): Live metrics process pool.
    unwrap_fn (Any): Picklable worker ``(metrics_obj, job) -> payload``.
    persist_fn (Any): Parent persist ``payload -> outcome dict``.
    prewarm_worker_fn (Any): Picklable ``jid -> {jid, ok, ...}``.
    inline_prewarm_fn (Any | None): Parent prewarm when mode is inline.
    prewarm_mode (str): ``inline`` or ``pipeline_required``.
    max_inflight (int): Concurrent apply_async budget.
    poll_timeout_s (float): AsyncResult wait slice.
    stall_timeout_s (float): No-progress abort budget.
    ready_queue (Any | None): Optional ready queue for supplements.
    ready_queue_lock (Any | None): Lock for ready_queue.
    soft_max (int): Supplement soft sample cap.
    hard_max (int): Supplement hard sample cap.
    supplement_enabled (bool): When False, never pull ready_queue.
    shutdown_requested (Any | None): ``[bool]`` flag list, or None.
    progress_callback (Any | None): Mid-session heartbeat callback.
    abort_if_pool_dead_fn (Any | None): Optional pool-death checker.
    on_stall_reset (Any | None): Called after stall soft-fail.
    on_poll_hygiene_fn (Any | None): Optional zero-arg callback for throttled
        ``[main]`` zombie reap each poll loop.
    empty_supplement_sleep_s (float): Sleep when waiting with empty fill.
    on_supplements_taken (Any | None): Optional ``(n: int) -> None`` after
        each non-empty ready-queue supplement pop (scheduler dequeue counters).

  Returns:
    list[dict[str, Any]]: Per-ref results with keys ``ref``, ``ok``,
    ``base_outcome``, ``metrics_s``, ``prewarm_s``.

  Raises:
    Exception: Re-raised when ``abort_if_pool_dead_fn`` reports pool death.

  Examples:
    >>> run_metrics_sliding_session(  # doctest: +SKIP
    ...     primary_refs=[], metrics_obj=None, shared_pool=None,
    ...     unwrap_fn=None, persist_fn=None, prewarm_worker_fn=None,
    ...     inline_prewarm_fn=None, prewarm_mode="inline",
    ...     max_inflight=1, poll_timeout_s=0.1, stall_timeout_s=1.0)
  """
  results: list[dict[str, Any]] = []
  if not primary_refs:
    return results

  primary: deque[Any] = deque(primary_refs)
  pending: list[dict[str, Any]] = []
  cap = max(1, int(max_inflight))
  poll_s = max(0.0, float(poll_timeout_s))
  stall_s = max(0.0, float(stall_timeout_s))
  last_progress_at = time.monotonic()
  completed_total = 0
  session_total = len(primary_refs)

  def _shutting_down() -> bool:
    """
    Return True when the shared shutdown flag is set.

    Returns:
      bool: Whether the session should stop submitting work.

    Examples:
      >>> _shutting_down()  # doctest: +SKIP
    """
    if shutdown_requested is None:
      return False
    try:
      return bool(shutdown_requested[0])
    except (TypeError, IndexError):
      return bool(shutdown_requested)

  def _emit(phase: str) -> None:
    """
    Invoke ``progress_callback`` for the current phase, swallowing errors.

    Args:
      phase (str): Progress phase label (for example ``metrics``).

    Returns:
      None

    Examples:
      >>> _emit("metrics")  # doctest: +SKIP
    """
    if not callable(progress_callback):
      return
    try:
      progress_callback(
          phase=phase,
          completed=completed_total,
          total=max(session_total, completed_total),
      )
    except Exception:
      pass

  def _submit_metrics(ref: Any, *, is_original: bool) -> None:
    """
    Queue one metrics ``apply_async`` and track it in ``pending``.

    Args:
      ref (Any): Candidate job ref with a ``jid`` attribute.
      is_original (bool): True when ``ref`` came from ``primary_refs``.

    Returns:
      None

    Examples:
      >>> _submit_metrics(None, is_original=True)  # doctest: +SKIP
    """
    async_result = shared_pool.apply_async(unwrap_fn, ((metrics_obj, ref),))
    pending.append({
        "async_result": async_result,
        "ref": ref,
        "phase": "metrics",
        "is_original": bool(is_original),
        "t0": time.monotonic(),
        "metrics_s": 0.0,
        "base_outcome": None,
    })

  def _submit_prewarm(item: dict[str, Any]) -> None:
    """
    Run inline prewarm or queue a pool prewarm for a finished metrics item.

    Args:
      item (dict[str, Any]): Pending entry with ``ref`` and ``base_outcome``.

    Returns:
      None

    Examples:
      >>> _submit_prewarm({})  # doctest: +SKIP
    """
    ref = item["ref"]
    if prewarm_mode == "inline" or not callable(prewarm_worker_fn):
      t_pw = time.monotonic()
      ok = True
      if callable(inline_prewarm_fn):
        try:
          inline_prewarm_fn(ref.jid)
        except Exception:
          ok = False
      prewarm_s = max(0.0, time.monotonic() - t_pw)
      results.append({
          "ref": ref,
          "ok": bool(item["base_outcome"].get("ok")),
          "prewarm_ok": ok,
          "base_outcome": item["base_outcome"],
          "metrics_s": float(item["metrics_s"]),
          "prewarm_s": prewarm_s,
      })
      return
    async_result = shared_pool.apply_async(prewarm_worker_fn, (ref.jid,))
    item["async_result"] = async_result
    item["phase"] = "prewarm"
    item["t_prewarm0"] = time.monotonic()
    pending.append(item)

  def _finalize_failed(
    ref: Any,
    base: dict[str, Any],
    metrics_s: float,
  ) -> None:
    """
    Append a failed metrics outcome without attempting prewarm.

    Args:
      ref (Any): Candidate job ref.
      base (dict[str, Any]): Persist/outcome dict for the failed job.
      metrics_s (float): Metrics wall seconds to record.

    Returns:
      None

    Examples:
      >>> _finalize_failed(None, {}, 0.0)  # doctest: +SKIP
    """
    results.append({
        "ref": ref,
        "ok": False,
        "base_outcome": base,
        "metrics_s": float(metrics_s),
        "prewarm_s": 0.0,
    })

  while (primary or pending) and not _shutting_down():
    if callable(on_poll_hygiene_fn):
      try:
        on_poll_hygiene_fn()
      except Exception:
        pass
    if callable(abort_if_pool_dead_fn):
      try:
        abort_if_pool_dead_fn(
            shared_pool,
            context="metrics sliding session",
        )
      except Exception:
        raise

    filled = False
    while len(pending) < cap and primary:
      ref = primary.popleft()
      _submit_metrics(ref, is_original=True)
      filled = True
      session_total = max(session_total, completed_total + len(pending) + len(primary))

    while (
        supplement_enabled
        and len(pending) < cap
        and not primary
        and original_batch_work_inflight(pending)
    ):
      slots = cap - len(pending)
      taken = pop_idle_slot_supplements(
          ready_queue,
          ready_queue_lock,
          max_n=slots,
          soft_max=soft_max,
          hard_max=hard_max,
          original_still_inflight=True,
      )
      if not taken:
        break
      if callable(on_supplements_taken):
        try:
          on_supplements_taken(len(taken))
        except Exception:
          pass
      for ref in taken:
        _submit_metrics(ref, is_original=False)
        filled = True
        session_total += 1

    if not pending:
      break

    if not filled and not primary and not original_batch_work_inflight(pending):
      # Only supplement work left: drain it (already submitted); no new fill.
      pass

    ready_item = None
    ready_idx = -1
    wait_deadline = time.monotonic() + poll_s
    while ready_item is None and time.monotonic() <= wait_deadline:
      for idx, item in enumerate(pending):
        async_result = item["async_result"]
        if async_result.ready():
          ready_item = item
          ready_idx = idx
          break
      if ready_item is None:
        time.sleep(min(0.01, max(0.001, poll_s if poll_s > 0 else 0.01)))

    if ready_item is None:
      stalled_for = time.monotonic() - last_progress_at
      if stalled_for >= max(0.0, float(stall_s)):
        for item in list(pending):
          ref = item["ref"]
          base = item.get("base_outcome") or {
              "jid": ref.jid,
              "ok": False,
              "status": "sliding_session_stall",
              "error_type": "MetricsSlidingStall",
              "error_message": "no progress for {:.1f}s".format(stalled_for),
              "persist_s": 0.0,
          }
          if item["phase"] == "metrics":
            base = {
                "jid": ref.jid,
                "ok": False,
                "status": "sliding_session_stall",
                "error_type": "MetricsSlidingStall",
                "error_message": "no progress for {:.1f}s".format(stalled_for),
                "persist_s": 0.0,
            }
          _finalize_failed(ref, base, float(item.get("metrics_s") or 0.0))
          completed_total += 1
        pending.clear()
        if callable(on_stall_reset):
          on_stall_reset()
        break
      if not filled and empty_supplement_sleep_s > 0:
        time.sleep(float(empty_supplement_sleep_s))
      continue

    pending.pop(ready_idx)
    last_progress_at = time.monotonic()
    async_result = ready_item["async_result"]
    ref = ready_item["ref"]

    if ready_item["phase"] == "metrics":
      _emit("persist")
      try:
        payload = async_result.get(timeout=0)
      except Exception as exc:
        base = {
            "jid": ref.jid,
            "ok": False,
            "status": "sliding_worker_exception",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "persist_s": 0.0,
        }
        metrics_s = max(0.0, time.monotonic() - float(ready_item["t0"]))
        _finalize_failed(ref, base, metrics_s)
        completed_total += 1
        _emit("metrics")
        continue
      metrics_s = max(0.0, time.monotonic() - float(ready_item["t0"]))
      try:
        base_outcome = persist_fn(payload)
      except Exception as exc:
        base_outcome = {
            "jid": ref.jid,
            "ok": False,
            "status": "sliding_persist_exception",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "persist_s": 0.0,
        }
      ready_item["base_outcome"] = base_outcome
      ready_item["metrics_s"] = metrics_s
      if base_outcome.get("ok"):
        _emit("prewarm")
        _submit_prewarm(ready_item)
        if ready_item["phase"] == "metrics":
          # inline path already finalized inside _submit_prewarm
          completed_total += 1
          _emit("metrics")
      else:
        _finalize_failed(ref, base_outcome, metrics_s)
        completed_total += 1
        _emit("metrics")
      continue

    # prewarm phase
    prewarm_ok = True
    try:
      prewarm_result = async_result.get(timeout=0)
      if isinstance(prewarm_result, dict):
        prewarm_ok = bool(prewarm_result.get("ok"))
    except Exception:
      prewarm_ok = False
    prewarm_s = max(
        0.0,
        time.monotonic() - float(ready_item.get("t_prewarm0", last_progress_at)),
    )
    base = ready_item["base_outcome"]
    results.append({
        "ref": ref,
        "ok": bool(base.get("ok")),
        "prewarm_ok": prewarm_ok,
        "base_outcome": base,
        "metrics_s": float(ready_item["metrics_s"]),
        "prewarm_s": prewarm_s,
    })
    completed_total += 1
    _emit("prewarm")

  # Soft-fail anything left (shutdown)
  for item in pending:
    ref = item["ref"]
    base = item.get("base_outcome") or {
        "jid": ref.jid,
        "ok": False,
        "status": "sliding_session_interrupted",
        "error_type": "Shutdown",
        "error_message": "sliding session interrupted",
        "persist_s": 0.0,
    }
    results.append({
        "ref": ref,
        "ok": False,
        "base_outcome": base,
        "metrics_s": float(item.get("metrics_s") or 0.0),
        "prewarm_s": 0.0,
    })

  return results
