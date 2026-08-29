"""
Ingest worker progress heartbeat and idle-stall helpers.

When ``sync_ingest_per_file_timeout_s`` is 0 (wall-clock B demoted), soft-kill
uses ``sync_ingest_stall_idle_s``: no parse/write progress for that window raises
``TimeoutError`` with ``stage=idle_stall`` for coordinator soft-requeue.

Attributes:
  _ingest_idle_stall_s: ContextVar idle window for the active task.
  _ingest_last_progress_mono: ContextVar last heartbeat monotonic time.
  _ingest_progress_path: ContextVar path label for idle-stall errors.
"""
from __future__ import annotations

import contextvars
import time
from typing import Any

import hpcperfstats.dbload.lib.conf_parser as cfg

_ingest_last_progress_mono: contextvars.ContextVar[float | None] = (
    contextvars.ContextVar("ingest_last_progress_mono", default=None)
)
_ingest_idle_stall_s: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "ingest_idle_stall_s",
    default=None,
)
_ingest_progress_path: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ingest_progress_path",
    default=None,
)


def begin_ingest_progress(
  stats_file: str,
  *,
  idle_s: float | None = None,
  clock: Any = time.monotonic,
) -> tuple[Any, Any, Any]:
  """
  Start a progress heartbeat window for one ingest worker task.

  Args:
    stats_file (str): Absolute closed raw stats path for idle-stall errors.
    idle_s (float | None): Idle window seconds; ``None`` reads INI.
    clock (Any): Monotonic clock (injectable for tests).

  Returns:
    tuple[Any, Any, Any]: ContextVar reset tokens ``(progress, idle, path)``.

  Examples:
    >>> toks = begin_ingest_progress("/x", idle_s=0.0)  # doctest: +SKIP
  """
  if idle_s is None:
    idle_s = float(cfg.get_sync_ingest_stall_idle_s())
  idle_s = max(0.0, float(idle_s))
  now = float(clock())
  t_progress = _ingest_last_progress_mono.set(now)
  t_idle = _ingest_idle_stall_s.set(idle_s if idle_s > 0.0 else None)
  t_path = _ingest_progress_path.set(str(stats_file))
  return t_progress, t_idle, t_path


def end_ingest_progress(tokens: tuple[Any, Any, Any] | None) -> None:
  """
  Reset progress ContextVars after ``begin_ingest_progress``.

  Args:
    tokens (tuple[Any, Any, Any] | None): Tokens from ``begin_ingest_progress``.

  Returns:
    None

  Examples:
    >>> end_ingest_progress(None)
  """
  if not tokens:
    return
  t_progress, t_idle, t_path = tokens
  _ingest_last_progress_mono.reset(t_progress)
  _ingest_idle_stall_s.reset(t_idle)
  _ingest_progress_path.reset(t_path)


def touch_ingest_progress(*, clock: Any = time.monotonic) -> None:
  """
  Record parse/write progress and reset the idle-stall clock.

  Args:
    clock (Any): Monotonic clock (injectable for tests).

  Returns:
    None

  Examples:
    >>> touch_ingest_progress()  # doctest: +SKIP
  """
  if _ingest_idle_stall_s.get() is None and _ingest_last_progress_mono.get() is None:
    return
  _ingest_last_progress_mono.set(float(clock()))


def get_ingest_last_progress_mono() -> float | None:
  """
  Return the monotonic timestamp of the last progress heartbeat.

  Returns:
    float | None: Last progress time, or ``None`` when unset.

  Examples:
    >>> get_ingest_last_progress_mono()  # doctest: +SKIP
  """
  return _ingest_last_progress_mono.get()


def get_ingest_idle_stall_s() -> float | None:
  """
  Return the active idle-stall window for this worker task.

  Returns:
    float | None: Idle seconds, or ``None`` when idle stall is disabled.

  Examples:
    >>> get_ingest_idle_stall_s()  # doctest: +SKIP
  """
  return _ingest_idle_stall_s.get()


def raise_if_ingest_idle_stalled(
  stats_file: str | None = None,
  stage: str = "idle_stall",
  *,
  clock: Any = time.monotonic,
) -> None:
  """
  Raise a rich timeout when no progress occurs for the idle window.

  Args:
    stats_file (str | None): Path label; defaults to the begun progress path.
    stage (str): Stage token packed into the timeout (default ``idle_stall``).
    clock (Any): Monotonic clock (injectable for tests).

  Returns:
    None

  Raises:
    IngestPerFileTimeoutError: When idle stall is exceeded.

  Examples:
    >>> raise_if_ingest_idle_stalled("/x")  # doctest: +SKIP
  """
  idle_s = _ingest_idle_stall_s.get()
  if idle_s is None or float(idle_s) <= 0.0:
    return
  last = _ingest_last_progress_mono.get()
  if last is None:
    return
  elapsed_idle = float(clock()) - float(last)
  if elapsed_idle < float(idle_s):
    return
  path = str(stats_file or _ingest_progress_path.get() or "")
  from hpcperfstats.dbload.sync_timedb import IngestPerFileTimeoutError

  raise IngestPerFileTimeoutError(path, stage or "idle_stall", elapsed_idle)
