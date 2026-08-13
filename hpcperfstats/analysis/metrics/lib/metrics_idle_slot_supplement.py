"""Sample-count sizing and ready-queue idle-slot supplement for update_metrics."""

from __future__ import annotations

import math
from typing import Any


def estimated_sample_count_for_job(
  nhosts: Any,
  runtime_s: Any,
  *,
  unknown_runtime_s: float = 172800.0,
) -> int:
  """
  Estimate host×minute sample count for idle-slot size filtering.

  Aligns with ``_estimate_one_min_samples_for_window`` cadence:
  ``max(1, nhosts) * max(1, ceil(runtime_s / 60))``.

  Args:
    nhosts (Any): Host count from ``job_data.nhosts`` or ``len(host_list)``.
    runtime_s (Any): Accounting-window seconds, or None for unknown.
    unknown_runtime_s (float): Fallback runtime when ``runtime_s`` is None.

  Returns:
    int: Estimated sample count (always >= 1).

  Examples:
    >>> estimated_sample_count_for_job(2, 120.0)
    4
    >>> estimated_sample_count_for_job(0, None, unknown_runtime_s=60.0)
    1
  """
  try:
    hosts = int(nhosts) if nhosts is not None else 1
  except (TypeError, ValueError):
    hosts = 1
  hosts = max(1, hosts)
  if runtime_s is None:
    rt = float(unknown_runtime_s)
  else:
    try:
      rt = float(runtime_s)
    except (TypeError, ValueError):
      rt = float(unknown_runtime_s)
  rt = max(0.0, rt)
  minutes = max(1, int(math.ceil(rt / 60.0))) if rt > 0 else 1
  return max(1, hosts * minutes)


def resolve_nhosts_for_ref(
  nhosts: Any = None,
  host_list: Any = None,
) -> int:
  """
  Prefer positive ``job_data.nhosts``, else ``len(host_list)``, else 1.

  Args:
    nhosts (Any): Declared host count, or None.
    host_list (Any): Host name list/sequence, or None.

  Returns:
    int: Resolved host count (>= 1).

  Examples:
    >>> resolve_nhosts_for_ref(4, ["a", "b"])
    4
    >>> resolve_nhosts_for_ref(0, ["a", "b"])
    2
    >>> resolve_nhosts_for_ref(None, None)
    1
  """
  try:
    n = int(nhosts) if nhosts is not None else 0
  except (TypeError, ValueError):
    n = 0
  if n > 0:
    return n
  try:
    hl = len(host_list) if host_list is not None else 0
  except TypeError:
    hl = 0
  return max(1, int(hl))


def sample_count_for_ref(
  ref: Any,
  *,
  unknown_runtime_s: float = 172800.0,
) -> int:
  """
  Read or compute ``estimated_sample_count`` on a candidate ref.

  Args:
    ref (Any): Candidate namespace with optional sample/runtime/nhosts fields.
    unknown_runtime_s (float): Fallback when runtime is missing.

  Returns:
    int: Estimated sample count (>= 1).

  Examples:
    >>> from types import SimpleNamespace
    >>> sample_count_for_ref(SimpleNamespace(estimated_sample_count=12))
    12
  """
  existing = getattr(ref, "estimated_sample_count", None)
  if existing is not None:
    try:
      return max(1, int(existing))
    except (TypeError, ValueError):
      pass
  return estimated_sample_count_for_job(
      getattr(ref, "nhosts", None),
      getattr(ref, "runtime_s", None),
      unknown_runtime_s=unknown_runtime_s,
  )


def pop_supplement_refs_from_ready_queue(
  ready_queue: Any,
  *,
  max_n: int,
  soft_max: int,
  hard_max: int,
  original_batch_still_inflight: bool,
) -> list[Any]:
  """
  Pop up to ``max_n`` smaller ready-queue refs (RC-E stop when originals done).

  Two-pass filter: prefer ``estimated_sample_count < soft_max``, then
  ``[soft_max, hard_max)``. Never take ``>= hard_max``. Non-selected refs stay
  in the queue in original relative order.

  Args:
    ready_queue (Any): Mutable deque/list of candidate refs.
    max_n (int): Maximum refs to return.
    soft_max (int): Soft sample ceiling (exclusive preference band).
    hard_max (int): Hard sample ceiling (never select at or above).
    original_batch_still_inflight (bool): When False, return [] (RC-E).

  Returns:
    list[Any]: Selected supplement refs (may be empty).

  Examples:
    >>> from collections import deque
    >>> from types import SimpleNamespace
    >>> q = deque([
    ...     SimpleNamespace(jid="big", estimated_sample_count=90000),
    ...     SimpleNamespace(jid="small", estimated_sample_count=10),
    ... ])
    >>> [r.jid for r in pop_supplement_refs_from_ready_queue(
    ...     q, max_n=1, soft_max=10000, hard_max=80000,
    ...     original_batch_still_inflight=True)]
    ['small']
  """
  if (
      not original_batch_still_inflight
      or max_n <= 0
      or ready_queue is None
      or not ready_queue
  ):
    return []
  soft = max(1, int(soft_max))
  hard = max(soft, int(hard_max))
  limit = max(0, int(max_n))

  items: list[Any] = []
  while ready_queue:
    if hasattr(ready_queue, "popleft"):
      items.append(ready_queue.popleft())
    else:
      items.append(ready_queue.pop(0))

  def _select(
    pred: Any,
    pool: list[Any],
    already: list[Any],
  ) -> tuple[list[Any], list[Any]]:
    """
    Partition ``pool`` into selected refs and leftovers under ``pred``.

    Args:
      pred (Any): Callable ``samples -> bool`` gate for selecting a ref.
      pool (list[Any]): Candidate refs still under consideration.
      already (list[Any]): Refs already selected from a prior pass.

    Returns:
      tuple[list[Any], list[Any]]: ``(taken, remaining)`` lists.

    Examples:
      >>> _select(lambda s: s < 10, [], [])  # doctest: +SKIP
    """
    taken: list[Any] = list(already)
    remaining: list[Any] = []
    for ref in pool:
      if len(taken) >= limit:
        remaining.append(ref)
        continue
      samples = sample_count_for_ref(ref)
      if pred(samples):
        taken.append(ref)
      else:
        remaining.append(ref)
    return taken, remaining

  taken, remaining = _select(lambda s: s < soft, items, [])
  if len(taken) < limit:
    taken, remaining = _select(
        lambda s: soft <= s < hard,
        remaining,
        taken,
    )
  for ref in remaining:
    if hasattr(ready_queue, "append"):
      ready_queue.append(ref)
    else:
      ready_queue.append(ref)
  return taken[:limit]
