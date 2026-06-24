"""Pipelined startup tail ingest: consume discover-slice enqueue on a background thread."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg

from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    calendar_date_from_daily_tar_path,
    unprocessed_tar_paths_still_on_disk,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import is_giant_ingest_budget


class StartupTailIngestCoordinator:
  """Background consumer for discover-slice tail-eligible day enqueue."""

  def __init__(
      self,
      *,
      log_fn,
      run_ingest_batch: Callable[[List[str], str], Tuple[List[str], List[str]]],
      submit_day_close: Callable[[str, str], bool],
      signal_janitor: Callable[[], None],
      get_startup_snapshot: Callable[[], Any],
      live_unprocessed_by_tar: Callable[[], Dict[str, List[str]]],
      discover_done_fn: Callable[[], bool],
      process_title: str = "sync_timedb.py",
  ):
    self.log_fn = log_fn
    self.run_ingest_batch = run_ingest_batch
    self.submit_day_close = submit_day_close
    self.signal_janitor = signal_janitor
    self.get_startup_snapshot = get_startup_snapshot
    self.live_unprocessed_by_tar = live_unprocessed_by_tar
    self.discover_done_fn = discover_done_fn
    self.process_title = process_title
    self._lock = threading.Lock()
    self._condition = threading.Condition(self._lock)
    self._queue: List[Tuple[date, str, List[str]]] = []
    self._seen_tars: set[str] = set()
    self._deferred_above_max: set[str] = set()
    self._deferred_giant: set[str] = set()
    self._in_progress_tar: Optional[str] = None
    self._tail_ingest_done = False
    self._executor: Optional[ThreadPoolExecutor] = None
    self._ingest_future = None
    self.enabled = cfg.get_sync_startup_tail_ingest_enabled()
    if not self.enabled:
      self._tail_ingest_done = True

  def tail_ingest_done(self) -> bool:
    with self._lock:
      return self._tail_ingest_done

  def pending_count(self) -> int:
    with self._lock:
      return len(self._queue) + (1 if self._in_progress_tar else 0)

  def enqueue_tail_day(self, tar_norm: str, paths: List[str]) -> bool:
    """Idempotent enqueue for one calendar day (``paths`` on disk)."""
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm or not paths:
      return False
    giant_paths = [path for path in paths if is_giant_ingest_budget(path)]
    if giant_paths:
      self.note_deferred_giant(tar_norm, len(giant_paths), len(paths))
      return False
    with self._condition:
      if tar_norm in self._seen_tars:
        return False
      day_date = calendar_date_from_daily_tar_path(tar_norm)
      if day_date is None:
        return False
      self._seen_tars.add(tar_norm)
      self._queue.append((day_date, tar_norm, list(paths)))
      self._queue.sort(key=lambda item: item[0])
      if self.log_fn:
        day_iso = day_date.isoformat()
        self.log_fn(
            "sync_timedb: startup tail ingest enqueue day=%s paths=%d"
            % (day_iso, len(paths)),
            flush=True,
        )
      self._condition.notify_all()
      return True

  def note_deferred_above_max(
      self,
      tar_norm: str,
      path_count: int,
      max_files: int,
  ) -> None:
    """Log once per tar that exceeds startup tail ingest max (main ingest handles)."""
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm:
      return
    with self._lock:
      if tar_norm in self._deferred_above_max or tar_norm in self._seen_tars:
        return
      self._deferred_above_max.add(tar_norm)
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    day_iso = day_date.isoformat() if day_date is not None else tar_norm
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tail ingest defer day=%s paths=%d "
          "reason=above_max_files max=%d"
          % (day_iso, path_count, max_files),
          flush=True,
      )

  def note_deferred_giant(
      self,
      tar_norm: str,
      giant_count: int,
      path_count: int,
  ) -> None:
    """Log once per tar deferred because a path exceeds giant ingest budget."""
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm:
      return
    with self._lock:
      if (
          tar_norm in self._deferred_giant
          or tar_norm in self._seen_tars
          or tar_norm in self._deferred_above_max
      ):
        return
      self._deferred_giant.add(tar_norm)
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    day_iso = day_date.isoformat() if day_date is not None else tar_norm
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tail ingest defer day=%s paths=%d "
          "reason=giant_ingest_budget giants=%d"
          % (day_iso, path_count, giant_count),
          flush=True,
      )

  def start_async_tail_ingest(self) -> None:
    if not self.enabled:
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tail ingest disabled",
            flush=True,
        )
      return
    with self._lock:
      if self._executor is not None:
        return
      self._executor = ThreadPoolExecutor(max_workers=1)
      self._ingest_future = self._executor.submit(self._tail_ingest_loop)
      self._ingest_future.add_done_callback(self._on_ingest_future_done)
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tail ingest thread started",
          flush=True,
      )

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    with self._condition:
      self._condition.notify_all()
    executor.shutdown(wait=wait)

  def _on_ingest_future_done(self, future) -> None:
    try:
      exc = future.exception()
    except Exception as callback_exc:
      exc = callback_exc
    if exc is None:
      return
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tail ingest thread failed err=%s" % exc,
          flush=True,
      )

  def _maybe_mark_done(self) -> None:
    if not self.discover_done_fn():
      return
    with self._condition:
      if self._queue or self._in_progress_tar:
        return
      if not self._tail_ingest_done:
        self._tail_ingest_done = True
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tail ingest phase complete",
              flush=True,
          )
      self._condition.notify_all()

  def _pop_oldest_queued(self) -> Optional[Tuple[str, List[str]]]:
    with self._condition:
      if not self._queue:
        return None
      _day, tar_norm, paths = self._queue.pop(0)
      self._in_progress_tar = tar_norm
      return tar_norm, paths

  def _clear_in_progress(self, tar_norm: str) -> None:
    with self._condition:
      if self._in_progress_tar == tar_norm:
        self._in_progress_tar = None

  def _tail_ingest_loop(self) -> None:
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="startup-tail-ingest",
    )
    max_files = max(1, int(cfg.get_sync_startup_tail_ingest_max_files()))
    max_wall_s = float(cfg.get_sync_startup_tail_ingest_max_wall_seconds())
    wall_started = time.time()
    try:
      while not shutdown_requested[0]:
        if max_wall_s > 0 and (time.time() - wall_started) >= max_wall_s:
          with self._condition:
            if not self._tail_ingest_done:
              self._tail_ingest_done = True
              if self.log_fn:
                pending = len(self._queue) + (1 if self._in_progress_tar else 0)
                self.log_fn(
                    "sync_timedb: startup tail ingest wall budget exceeded "
                    "max_wall_s=%.0f pending_days=%d"
                    % (max_wall_s, pending),
                    flush=True,
                )
          break
        item = self._pop_oldest_queued()
        if item is None:
          self._maybe_mark_done()
          if self.tail_ingest_done():
            break
          with self._condition:
            self._condition.wait(timeout=0.25)
          continue
        tar_norm, paths = item
        day_date = calendar_date_from_daily_tar_path(tar_norm)
        day_iso = day_date.isoformat() if day_date is not None else tar_norm
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tail ingest begin day=%s paths=%d max=%d"
              % (day_iso, len(paths), max_files),
              flush=True,
          )
        try:
          self.get_startup_snapshot()
          successful_paths, failed_paths = self.run_ingest_batch(
              paths,
              "startup_tail_ingest",
          )
        finally:
          self._clear_in_progress(tar_norm)
        unprocessed_after = self.live_unprocessed_by_tar()
        checkpoint_complete = not unprocessed_tar_paths_still_on_disk(
            unprocessed_after,
            tar_norm,
        )
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tail ingest complete day=%s ingested=%d "
              "failed=%d checkpoint_complete=%s"
              % (
                  day_iso,
                  len(successful_paths),
                  len(failed_paths),
                  "yes" if checkpoint_complete else "no",
              ),
              flush=True,
          )
        if checkpoint_complete and self.submit_day_close(
            tar_norm,
            "startup_tail_ingest",
        ):
          if self.log_fn:
            self.log_fn(
                "sync_timedb: startup tail ingest submitted day_close tar=%s"
                % tar_norm,
                flush=True,
            )
          self.signal_janitor()
        self._maybe_mark_done()
    finally:
      with self._condition:
        if shutdown_requested[0] or self.discover_done_fn():
          if not self._queue and not self._in_progress_tar:
            if not self._tail_ingest_done and self.log_fn:
              self.log_fn(
                  "sync_timedb: startup tail ingest phase complete",
                  flush=True,
              )
            self._tail_ingest_done = True
