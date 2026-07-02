"""Session-static and ephemeral thread pools for sync_timedb background roles."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple, TypeVar

from django.db import close_old_connections

from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

T = TypeVar("T")
R = TypeVar("R")


class SessionSingleFlightExecutor:
  """Eager max_workers=1 ThreadPoolExecutor for supervisor background roles."""

  def __init__(
      self,
      *,
      thread_name_prefix: str,
      process_title: str,
      thread_role: str,
      enabled: bool = True,
  ):
    self.thread_name_prefix = thread_name_prefix
    self.process_title = process_title
    self.thread_role = thread_role
    self.enabled = bool(enabled)
    self._executor: Optional[ThreadPoolExecutor] = None
    if self.enabled:
      self._executor = ThreadPoolExecutor(
          max_workers=1,
          thread_name_prefix=self.thread_name_prefix,
      )

  @property
  def is_active(self) -> bool:
    return self._executor is not None

  def submit(self, fn: Callable[..., R], *args: Any, **kwargs: Any):
    if self._executor is None:
      raise RuntimeError(
          "SessionSingleFlightExecutor is disabled or not initialized "
          "(thread_name_prefix=%s)" % self.thread_name_prefix,
      )

    def _run() -> R:
      set_daemon_thread_title(
          "",
          script_name=self.process_title,
          role=self.thread_role,
      )
      close_old_connections()
      try:
        return fn(*args, **kwargs)
      finally:
        close_old_connections()

    return self._executor.submit(_run)

  def shutdown(self, wait: bool = True) -> None:
    if self._executor is None:
      return
    self._executor.shutdown(wait=wait)


def iter_bounded_thread_pool(
    items: Iterable[T],
    worker_fn: Callable[[T], R],
    *,
    max_workers: int,
    thread_role: Optional[str] = None,
    process_title: str = "sync_timedb.py",
) -> Iterator[Tuple[T, Optional[R], Optional[BaseException]]]:
  """Run ``worker_fn(item)`` with bounded parallelism.

  Yields ``(item, result, error)`` per completed task. ``error`` is set when the
  worker raised; ``result`` is set on success.
  """
  item_list = list(items)
  if not item_list:
    return
  workers = max(1, min(int(max_workers), len(item_list)))
  if workers <= 1 or len(item_list) <= 1:
    for item in item_list:
      if thread_role:
        set_daemon_thread_title(
            "",
            script_name=process_title,
            role=thread_role,
        )
      try:
        yield item, worker_fn(item), None
      except BaseException as exc:
        yield item, None, exc
    return

  def _task(item: T) -> R:
    if thread_role:
      set_daemon_thread_title(
          "",
          script_name=process_title,
          role=thread_role,
      )
    return worker_fn(item)

  with ThreadPoolExecutor(max_workers=workers) as executor:
    future_to_item = {
        executor.submit(_task, item): item for item in item_list
    }
    for future in as_completed(future_to_item):
      item = future_to_item[future]
      try:
        yield item, future.result(), None
      except BaseException as exc:
        yield item, None, exc


def run_bounded_thread_pool(
    items: Iterable[T],
    worker_fn: Callable[[T], R],
    *,
    max_workers: int,
    thread_role: Optional[str] = None,
    process_title: str = "sync_timedb.py",
) -> list[Tuple[T, Optional[R], Optional[BaseException]]]:
  """Collect ``iter_bounded_thread_pool`` results in completion order."""
  return list(
      iter_bounded_thread_pool(
          items,
          worker_fn,
          max_workers=max_workers,
          thread_role=thread_role,
          process_title=process_title,
      ),
  )
