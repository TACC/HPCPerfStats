"""
Metrics-local sliding-window feeder with sample-count idle-slot fill.

Attributes:
  METRICS_ROWS_PER_HOST_MINUTE: Floor for the frame rows one host-minute
    of a job holds: the p90 measured on the largest recent production jobs
    (hpcperfstats04; the single-host golden corpus runs lower).
    ``MemoryCostModel`` learns a higher value from finished jobs, never a
    lower one.
  METRICS_PEAK_BYTES_PER_ROW: Peak process bytes per frame row while a job
    is in flight: the consuming fetch/build plus metrics compute peak
    measured on the golden jobs with float64 value columns (frames above a
    few thousand rows; tiny jobs are dominated by fixed overhead).
  ADMISSION_TELEMETRY_INTERVAL_S: Seconds between ``telemetry_fn`` calls.
  LEARN_WINDOW: Finished jobs whose rows per host-minute the cost model
    keeps.
  LEARN_QUANTILE: Quantile of that window the cost model costs refs at.
"""

from __future__ import annotations

import contextlib
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from hpcperfstats.analysis.metrics.lib.metrics_idle_slot_supplement import (
  pop_supplement_refs_from_ready_queue,
)

METRICS_ROWS_PER_HOST_MINUTE = 312
METRICS_PEAK_BYTES_PER_ROW = 140
ADMISSION_TELEMETRY_INTERVAL_S = 60.0
LEARN_WINDOW = 200
LEARN_QUANTILE = 0.9


def resolve_metrics_pool_max_inflight(pool: Any, fallback: int = 1) -> int:
  """
  Bound outstanding apply_async work to the live pool thread count.

  Args:
    pool (Any): Titled thread pool or test double.
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
  except TypeError, ValueError:
    return max(1, int(fallback))


def is_backfill_ref(ref: Any) -> bool:
  """
  True when a candidate ref was listed by the update_metrics backfill lane.

  Args:
    ref (Any): Scheduler candidate ref (``lane`` attribute optional).

  Returns:
    bool: ``ref.lane == "backfill"``.

  Examples:
    >>> from types import SimpleNamespace
    >>> is_backfill_ref(SimpleNamespace(jid="1", lane="backfill"))
    True
    >>> is_backfill_ref(SimpleNamespace(jid="1"))
    False
  """
  return getattr(ref, "lane", None) == "backfill"


def _estimated_samples(ref: Any) -> int:
  """
  A ref's ``estimated_sample_count`` (host-minutes), at least 1.

  Args:
    ref (Any): Candidate ref (``estimated_sample_count`` optional).

  Returns:
    int: Host-minutes the scheduler estimated for the job.

  Examples:
    >>> from types import SimpleNamespace
    >>> _estimated_samples(SimpleNamespace(estimated_sample_count=0))
    1
  """
  try:
    return max(1, int(getattr(ref, "estimated_sample_count", 1) or 1))
  except TypeError, ValueError:
    return 1


class MemoryCostModel:
  """
  Peak-memory cost of a metrics job, learned from the jobs that finished.

  A ref is costed at ``estimated_sample_count x rows per host-minute x
  METRICS_PEAK_BYTES_PER_ROW``. Rows per host-minute is the
  ``LEARN_QUANTILE`` of the last ``LEARN_WINDOW`` finished jobs with a known
  runtime (their frame rows over their estimated host-minutes), never below
  ``METRICS_ROWS_PER_HOST_MINUTE``, so a denser job mix raises the cost and
  a sparse one cannot lower it under the production floor. Once a job's
  frame exists its cost is its measured rows x ``METRICS_PEAK_BYTES_PER_ROW``.
  ``artifact_only`` refs are costed like a compute: the prewarm child
  re-reads the whole frame inside the same memory cgroup.

  Attributes:
    rows_floor: Lowest rows per host-minute used.
    bytes_per_row: Peak bytes per frame row.
    _ratios: Rows per host-minute of recent finished jobs.
  """

  def __init__(
    self,
    rows_floor: float = METRICS_ROWS_PER_HOST_MINUTE,
    bytes_per_row: float = METRICS_PEAK_BYTES_PER_ROW,
  ) -> None:
    """
    Create a model with no observations yet.

    Args:
      rows_floor (float): Lowest rows per host-minute used.
      bytes_per_row (float): Peak bytes per frame row.

    Returns:
      None

    Examples:
      >>> MemoryCostModel().rows_per_host_minute() == (
      ...   METRICS_ROWS_PER_HOST_MINUTE
      ... )
      True
    """
    self.rows_floor = float(rows_floor)
    self.bytes_per_row = float(bytes_per_row)
    self._ratios: deque[float] = deque(maxlen=LEARN_WINDOW)

  def rows_per_host_minute(self) -> float:
    """
    Rows per host-minute refs are costed at.

    Returns:
      float: ``LEARN_QUANTILE`` of the observed ratios, at least the floor.

    Examples:
      >>> m = MemoryCostModel(rows_floor=10)
      >>> m.observe(
      ...   type("R", (), {"runtime_s": 60.0, "estimated_sample_count": 1})(),
      ...   50,
      ... )
      >>> m.rows_per_host_minute()
      50.0
    """
    if not self._ratios:
      return self.rows_floor
    ordered = sorted(self._ratios)
    learned = ordered[min(len(ordered) - 1, int(LEARN_QUANTILE * len(ordered)))]
    return max(self.rows_floor, learned)

  def cost(self, ref: Any) -> int:
    """
    Estimated peak bytes of ``ref`` before its frame exists.

    Args:
      ref (Any): Candidate ref (``estimated_sample_count`` optional).

    Returns:
      int: Host-minutes x rows per host-minute x bytes per row.

    Examples:
      >>> from types import SimpleNamespace
      >>> MemoryCostModel(rows_floor=2, bytes_per_row=10).cost(
      ...   SimpleNamespace(estimated_sample_count=3)
      ... )
      60
    """
    return int(
      _estimated_samples(ref) * self.rows_per_host_minute() * self.bytes_per_row
    )

  def measured_cost(self, rows: int) -> int:
    """
    Peak bytes of a job whose frame has ``rows`` rows.

    Args:
      rows (int): Frame rows.

    Returns:
      int: ``rows x bytes_per_row``.

    Examples:
      >>> MemoryCostModel(bytes_per_row=10).measured_cost(5)
      50
    """
    return int(rows * self.bytes_per_row)

  def observe(self, ref: Any, rows: int) -> None:
    """
    Learn from a finished job's frame size.

    Refs without a known runtime carry a guessed window, so their ratio says
    nothing about the jobs' density and is not learned.

    Args:
      ref (Any): The finished job's ref.
      rows (int): Rows of its frame.

    Returns:
      None

    Examples:
      >>> from types import SimpleNamespace
      >>> m = MemoryCostModel(rows_floor=1)
      >>> m.observe(SimpleNamespace(estimated_sample_count=4), 400)
      >>> m.rows_per_host_minute()
      1.0
    """
    if rows > 0 and getattr(ref, "runtime_s", None) is not None:
      self._ratios.append(rows / _estimated_samples(ref))


def pop_fresh_first(
  ready_queue: Any,
  max_n: int,
  admit: Callable[[Any], bool] | None = None,
  skipped_since: dict[Any, float] | None = None,
  head_wait_s: float = float("inf"),
) -> list[Any]:
  """
  Pop up to ``max_n`` refs, fresh-lane refs before backfill-lane refs.

  Within each lane the queue order is kept. The caller holds the queue lock.
  With ``admit``, a ref it refuses stays queued and later refs may be taken
  ahead of it; ``skipped_since`` records when each refused ref was first
  passed over. Once a refused ref has waited ``head_wait_s``, nothing behind
  it in fresh-first order is admitted: an aged backfill ref holds back only
  backfill refs (fresh refs sort ahead of it), an aged fresh ref holds back
  both lanes.

  Args:
    ready_queue (Any): Deque of candidate refs.
    max_n (int): Free slots.
    admit (Callable[[Any], bool] | None): Admission predicate, called in
      fresh-first order; True takes the ref. None takes every ref.
    skipped_since (dict[Any, float] | None): ``jid -> monotonic`` first
      refusal time, updated in place (entries for refs no longer queued
      are dropped).
    head_wait_s (float): Wait after which a refused ref stops skip-ahead.

  Returns:
    list[Any]: Refs removed from the queue.

  Examples:
    >>> from collections import deque
    >>> from types import SimpleNamespace as R
    >>> q = deque([R(jid="b1", lane="backfill"), R(jid="f1"), R(jid="f2")])
    >>> [r.jid for r in pop_fresh_first(q, 2)]
    ['f1', 'f2']
    >>> [r.jid for r in q]
    ['b1']
    >>> q = deque([R(jid="big"), R(jid="small")])
    >>> [r.jid for r in pop_fresh_first(q, 2, admit=lambda r: r.jid != "big")]
    ['small']
    >>> [r.jid for r in q]
    ['big']
  """
  if ready_queue is None or max_n <= 0 or not ready_queue:
    return []
  ordered = [r for r in ready_queue if not is_backfill_ref(r)] + [
    r for r in ready_queue if is_backfill_ref(r)
  ]
  now = time.monotonic()
  taken: list[Any] = []
  keep: list[Any] = []
  blocked = False
  for ref in ordered:
    if blocked or len(taken) >= max_n:
      keep.append(ref)
      continue
    if admit is None or admit(ref):
      taken.append(ref)
      continue
    keep.append(ref)
    if skipped_since is not None:
      first = skipped_since.setdefault(ref.jid, now)
      blocked = now - first >= head_wait_s
  if skipped_since is not None:
    queued = {r.jid for r in keep}
    for jid in [j for j in skipped_since if j not in queued]:
      del skipped_since[jid]
  ready_queue.clear()
  ready_queue.extend(keep)
  return taken


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
    shared_pool (Any): Metrics thread pool, or None.

  Returns:
    bool: True when the sliding session path should run.

  Examples:
    >>> class _P:
    ...   def apply_async(self, *a, **k):
    ...     return None
    >>> should_use_metrics_sliding_session(
    ...   supplement_enabled=True, shared_pool=_P()
    ... )
    True
    >>> should_use_metrics_sliding_session(
    ...   supplement_enabled=True, shared_pool=None
    ... )
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
    >>> [
    ...   r.jid
    ...   for r in pop_idle_slot_supplements(
    ...     q,
    ...     None,
    ...     max_n=1,
    ...     soft_max=10,
    ...     hard_max=80,
    ...     original_still_inflight=True,
    ...   )
    ... ]
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
  on_stall_reset: Any | None = None,
  empty_supplement_sleep_s: float = 0.05,
  on_supplements_taken: Any | None = None,
  on_result: Any | None = None,
  continuous: bool = False,
  should_stop: Any | None = None,
  on_refs_taken: Any | None = None,
  memory_budget_bytes: int = 0,
  head_wait_s: float = float("inf"),
  admit_ok: Callable[[], bool] | None = None,
  cost_model: MemoryCostModel | None = None,
  observe_rows: Callable[[Any], int] | None = None,
  telemetry_fn: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
  """
  Pool-sized sliding metrics+prewarm session with sample-count idle-slot fill.

  Does not call ingest ``imap_sliding_window_watch_pool``. Primary refs are
  drained first; when the primary iterator is empty and original-batch work
  remains in flight, idle slots fill from ``ready_queue`` under soft/hard
  sample caps (RC-E stops when no originals remain in flight). In
  ``continuous`` mode the session instead keeps every slot fed from
  ``ready_queue`` until ``should_stop()`` (no end-of-batch drain).

  Both the primary fill and the continuous refill admit a ref only while
  its memory cost (``MemoryCostModel.cost``) fits in ``memory_budget_bytes``
  next to the cost of everything in flight and ``admit_ok()`` reports
  memory headroom; with nothing in flight one ref is always admitted, so a
  job larger than the whole budget still runs (alone). The count cap
  ``max_inflight`` still applies.

  Args:
    primary_refs (list[Any]): Original compute-batch candidate refs.
    metrics_obj (Any): Metrics manager instance passed to ``unwrap_fn``.
    shared_pool (Any): Live metrics thread pool.
    unwrap_fn (Any): Worker ``(metrics_obj, job) -> payload``.
    persist_fn (Any): Parent persist ``payload -> outcome dict``.
    prewarm_worker_fn (Any): Worker ``jid -> {jid, ok, ...}``.
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
    on_stall_reset (Any | None): Called after stall soft-fail.
    empty_supplement_sleep_s (float): Sleep when waiting with empty fill.
    on_supplements_taken (Any | None): Optional ``(n: int) -> None`` after
        each non-empty ready-queue supplement pop (scheduler dequeue counters).
    on_result (Any | None): Optional ``(row: dict) -> None`` called as each
        ref finishes (metrics failure, prewarm done, stall, shutdown), so the
        caller can account per job instead of at session end.
    continuous (bool): Keep every slot fed from ``ready_queue`` (FIFO, no
        sample tiers, no original-batch gate) and wait for more work when
        the queue is empty, instead of returning when the primary refs are
        done. Ends when ``should_stop()`` is true and nothing is pending.
    should_stop (Any | None): ``() -> bool`` for continuous mode; True stops
        taking new work (producer finished and queue empty, or a scheduler
        stall exit).
    on_refs_taken (Any | None): Optional ``(refs: list) -> None`` called with
        the refs popped from ``ready_queue`` (lane accounting; fresh-lane
        refs are popped before backfill-lane refs).
    memory_budget_bytes (int): Summed in-flight cost allowed in
        flight; 0 disables the budget.
    head_wait_s (float): Seconds a ready-queue ref refused by admission may
        be passed over before refs behind it stop being admitted ahead of it
        (``pop_fresh_first``).
    admit_ok (Callable[[], bool] | None): Memory headroom check; False
        pauses admission while work is in flight. None always admits.
    cost_model (MemoryCostModel | None): Costs refs and learns from
        finished jobs; None uses a fresh model for this session.
    observe_rows (Callable[[Any], int] | None): ``ref -> frame rows`` once
        its metrics persisted (0 when unknown). A known size replaces the
        job's estimated cost and teaches ``cost_model``.
    telemetry_fn (Callable[[dict[str, Any]], None] | None): Called at most
        every ``ADMISSION_TELEMETRY_INTERVAL_S`` while work is in flight
        with ``inflight``, ``estimated_bytes`` (refs costed by estimate),
        ``measured_bytes`` (refs costed by their frame), ``budget_bytes``
        and ``rows_per_host_minute``.

  Returns:
    list[dict[str, Any]]: Per-ref results with keys ``ref``, ``ok``,
    ``base_outcome``, ``metrics_s``, ``prewarm_s``.

  Examples:
    >>> run_metrics_sliding_session(  # doctest: +SKIP
    ...   primary_refs=[],
    ...   metrics_obj=None,
    ...   shared_pool=None,
    ...   unwrap_fn=None,
    ...   persist_fn=None,
    ...   prewarm_worker_fn=None,
    ...   inline_prewarm_fn=None,
    ...   prewarm_mode="inline",
    ...   max_inflight=1,
    ...   poll_timeout_s=0.1,
    ...   stall_timeout_s=1.0,
    ... )
  """
  results: list[dict[str, Any]] = []
  if not primary_refs and not continuous:
    return results

  def _finish_row(row: dict[str, Any]) -> None:
    """
    Record one finished ref and hand it to ``on_result``.

    Args:
      row (dict[str, Any]): Result row (``ref``, ``ok``, ``base_outcome`` ...).

    Returns:
      None

    Examples:
      >>> _finish_row({"ref": None, "ok": False})  # doctest: +SKIP
    """
    results.append(row)
    if callable(on_result):
      on_result(row)

  primary: deque[Any] = deque(primary_refs)
  pending: list[dict[str, Any]] = []
  cap = max(1, int(max_inflight))
  poll_s = max(0.0, float(poll_timeout_s))
  stall_s = max(0.0, float(stall_timeout_s))
  last_progress_at = time.monotonic()
  completed_total = 0
  session_total = len(primary_refs)
  budget = max(0, int(memory_budget_bytes))
  model = cost_model if cost_model is not None else MemoryCostModel()
  skipped_since: dict[Any, float] = {}
  telemetry_at = [time.monotonic()]

  def _telemetry() -> None:
    """
    Report the in-flight memory picture to ``telemetry_fn`` (throttled).

    Returns:
      None

    Examples:
      >>> _telemetry()  # doctest: +SKIP
    """
    now = time.monotonic()
    if not callable(telemetry_fn) or not pending:
      return
    if now - telemetry_at[0] < ADMISSION_TELEMETRY_INTERVAL_S:
      return
    telemetry_at[0] = now
    measured = sum(i["cost"] for i in pending if i.get("measured"))
    with contextlib.suppress(Exception):
      telemetry_fn(
        {
          "inflight": len(pending),
          "estimated_bytes": sum(i["cost"] for i in pending) - measured,
          "measured_bytes": measured,
          "budget_bytes": budget,
          "rows_per_host_minute": model.rows_per_host_minute(),
        }
      )

  def _admit(ref: Any, extra_cost: int = 0) -> bool:
    """
    Decide whether ``ref`` may start now under the memory budget.

    Args:
      ref (Any): Candidate ref.
      extra_cost (int): Cost of refs already taken in the same pop but not
        yet in ``pending``.

    Returns:
      bool: True when nothing is in flight, or when the ref fits the
      budget and the headroom check passes.

    Examples:
      >>> _admit(None)  # doctest: +SKIP
      True
    """
    if not pending and not extra_cost:
      return True
    if budget and (
      sum(item["cost"] for item in pending) + extra_cost + model.cost(ref)
      > budget
    ):
      return False
    return not callable(admit_ok) or bool(admit_ok())

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
    except TypeError, IndexError:
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
    with contextlib.suppress(Exception):
      progress_callback(
        phase=phase,
        completed=completed_total,
        total=max(session_total, completed_total),
      )

  def _submit_metrics(ref: Any, *, is_original: bool) -> None:
    """
    Queue one metrics ``apply_async`` and track it in ``pending``.

    An ``artifact_only`` ref (metrics already complete, artifacts missing or
    stale) skips compute and goes straight to prewarm, whether it is a
    primary or an idle-slot supplement.

    Args:
      ref (Any): Candidate job ref with a ``jid`` attribute.
      is_original (bool): True when ``ref`` came from ``primary_refs``.

    Returns:
      None

    Examples:
      >>> _submit_metrics(None, is_original=True)  # doctest: +SKIP
    """
    item = {
      "ref": ref,
      "cost": model.cost(ref),
      "phase": "metrics",
      "is_original": bool(is_original),
      "t0": time.monotonic(),
      "metrics_s": 0.0,
      "base_outcome": None,
    }
    if getattr(ref, "artifact_only", False):
      item["base_outcome"] = {
        "jid": ref.jid,
        "ok": True,
        "status": "artifact_only",
        "persist_s": 0.0,
      }
      _submit_prewarm(item)
      return
    item["async_result"] = shared_pool.apply_async(
      unwrap_fn, ((metrics_obj, ref),)
    )
    pending.append(item)

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
      _finish_row(
        {
          "ref": ref,
          "ok": bool(item["base_outcome"].get("ok")),
          "prewarm_ok": ok,
          "base_outcome": item["base_outcome"],
          "metrics_s": float(item["metrics_s"]),
          "prewarm_s": prewarm_s,
        }
      )
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
    _finish_row(
      {
        "ref": ref,
        "ok": False,
        "base_outcome": base,
        "metrics_s": float(metrics_s),
        "prewarm_s": 0.0,
      }
    )

  def _take_fifo(max_n: int) -> list[Any]:
    """
    Pop up to ``max_n`` admitted refs from ``ready_queue`` (continuous).

    Args:
      max_n (int): Free slots.

    Returns:
      list[Any]: Refs taken, fresh lane first, queue order within a lane.

    Examples:
      >>> _take_fifo(0)  # doctest: +SKIP
      []
    """
    if ready_queue is None or max_n <= 0:
      return []
    lock = (
      ready_queue_lock
      if ready_queue_lock is not None
      else contextlib.nullcontext()
    )
    taken_cost = [0]

    def _admit_next(ref: Any) -> bool:
      """
      Admit ``ref`` counting the refs this pop already took.

      Args:
        ref (Any): Candidate ref.

      Returns:
        bool: Whether ``ref`` is taken.

      Examples:
        >>> _admit_next(None)  # doctest: +SKIP
        True
      """
      if not _admit(ref, taken_cost[0]):
        return False
      taken_cost[0] += model.cost(ref)
      return True

    with lock:
      return pop_fresh_first(
        ready_queue,
        max_n,
        admit=_admit_next,
        skipped_since=skipped_since,
        head_wait_s=head_wait_s,
      )

  while (primary or pending or continuous) and not _shutting_down():
    _telemetry()
    stop = continuous and callable(should_stop) and bool(should_stop())
    was_idle = not pending
    filled = False
    while len(pending) < cap and primary and _admit(primary[0]):
      ref = primary.popleft()
      _submit_metrics(ref, is_original=True)
      filled = True
      session_total = max(
        session_total, completed_total + len(pending) + len(primary)
      )

    while continuous and not stop and not primary and len(pending) < cap:
      taken = _take_fifo(cap - len(pending))
      if not taken:
        break
      if callable(on_supplements_taken):
        with contextlib.suppress(Exception):
          on_supplements_taken(len(taken))
      if callable(on_refs_taken):
        with contextlib.suppress(Exception):
          on_refs_taken(list(taken))
      for ref in taken:
        _submit_metrics(ref, is_original=True)
        filled = True
        session_total += 1

    while (
      not continuous
      and supplement_enabled
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
        with contextlib.suppress(Exception):
          on_supplements_taken(len(taken))
      for ref in taken:
        _submit_metrics(ref, is_original=False)
        filled = True
        session_total += 1

    if not pending:
      if not continuous or stop:
        break
      time.sleep(max(0.01, float(empty_supplement_sleep_s)))
      continue
    if was_idle and filled:
      # Waiting for new work is not a stall: the clock starts with the work.
      last_progress_at = time.monotonic()

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
          metrics_s = float(item.get("metrics_s") or 0.0)
          if item["phase"] == "prewarm":
            # Metrics rows are already committed; only artifacts are missing.
            _finish_row(
              {
                "ref": ref,
                "ok": bool(item["base_outcome"].get("ok")),
                "prewarm_ok": False,
                "base_outcome": item["base_outcome"],
                "metrics_s": metrics_s,
                "prewarm_s": 0.0,
              }
            )
          else:
            stall = {
              "jid": ref.jid,
              "ok": False,
              "status": "sliding_session_stall",
              "error_type": "MetricsSlidingStall",
              "error_message": f"no progress for {stalled_for:.1f}s",
              "persist_s": 0.0,
            }
            _finalize_failed(ref, stall, metrics_s)
          completed_total += 1
        pending.clear()
        if callable(on_stall_reset):
          on_stall_reset()
        break
      if not filled and empty_supplement_sleep_s > 0:
        time.sleep(float(empty_supplement_sleep_s))
      continue

    pending.pop(ready_idx)
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
        last_progress_at = time.monotonic()
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
      last_progress_at = time.monotonic()
      ready_item["base_outcome"] = base_outcome
      ready_item["metrics_s"] = metrics_s
      rows = 0
      if base_outcome.get("ok") and callable(observe_rows):
        with contextlib.suppress(Exception):
          rows = int(observe_rows(ref) or 0)
      if rows > 0:
        model.observe(ref, rows)
        ready_item["cost"] = model.measured_cost(rows)
        ready_item["measured"] = True
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
    last_progress_at = time.monotonic()
    prewarm_s = max(
      0.0,
      time.monotonic() - float(ready_item.get("t_prewarm0", last_progress_at)),
    )
    base = ready_item["base_outcome"]
    _finish_row(
      {
        "ref": ref,
        "ok": bool(base.get("ok")),
        "prewarm_ok": prewarm_ok,
        "base_outcome": base,
        "metrics_s": float(ready_item["metrics_s"]),
        "prewarm_s": prewarm_s,
      }
    )
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
    _finish_row(
      {
        "ref": ref,
        "ok": False,
        "base_outcome": base,
        "metrics_s": float(item.get("metrics_s") or 0.0),
        "prewarm_s": 0.0,
      }
    )

  return results
