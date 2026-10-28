"""
Generalized write-behind persistence for sync_timedb sidecars.

Hot ingest paths used to rewrite a whole sidecar document under a
``file_write_lock`` per event (about 1.5 s per mark on a 42 MB document), so
a 64-wide pool serialized on one lock. This module keeps the master copy in
memory, notifies a single named writer thread on mutation, and lets that
thread write one coalesced snapshot per interval, the same shape as the job
store snapshot writer.

Durability rules:

* Adds are lazy (lost on a hard crash, recomputed from disk and DB at boot).
* Removals and ``replace_all`` are durable before they return, so a removed
  key is never resurrected by a stale merge.
* A flush merges with the on-disk copy (another process may have written)
  and adopts foreign keys into memory.
* The persistence format and contract version are unchanged.

Attributes:
  DEFAULT_WRITE_BEHIND_INTERVAL_S: Default coalescing interval in seconds.
  FORCE_FLUSH_TIMEOUT_S: Upper bound a forced flush waits for the writer.
  _REGISTRY_LOCK: Guards the writer and store registries.
  _STORES: Entry stores by sidecar path.
  _WRITERS: Running writers, flushed at shutdown.
"""

from __future__ import annotations

import atexit
import threading
import time
from collections.abc import Callable
from typing import Any

from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  load_persistence_document,
  save_persistence_document,
)

DEFAULT_WRITE_BEHIND_INTERVAL_S = 60.0
FORCE_FLUSH_TIMEOUT_S = 120.0

_WRITERS: list[WriteBehindWriter] = []
_STORES: dict[str, WriteBehindEntryStore] = {}
_REGISTRY_LOCK = threading.Lock()


class WriteBehindWriter:
  """
  One named daemon thread that coalesces dirty notifications into writes.

  Attributes:
    name: Thread name (also the process-title role).
    interval_s: Minimum seconds between non-forced writes.
    _cv: Condition guarding every field below.
    _dirty: True when a write is owed.
    _error: Failure from the latest write attempt.
    _force: True when a flush was requested.
    _force_asked: Count of flush requests made.
    _force_done: Count of flush requests completed.
    _gen: Dirty-mark counter used to coalesce racing writes.
    _last: Monotonic time of the last successful write.
    _stop: True when ``stop`` asked the thread to exit.
    _thread: The daemon writer thread, or ``None``.
    _write_fn: Callback that performs one write.
  """

  def __init__(
    self,
    *,
    name: str,
    write_fn: Callable[[bool], Any],
    interval_s: float = DEFAULT_WRITE_BEHIND_INTERVAL_S,
  ) -> None:
    """
    Create a stopped writer.

    Args:
      name (str): Thread name.
      write_fn (Callable[[bool], Any]): Called with ``force`` on the writer
        thread; must raise on failure so the dirty flag is kept.
      interval_s (float): Minimum seconds between non-forced writes.

    Returns:
      None

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).dirty
      False
    """
    self.name = name
    self.interval_s = float(interval_s)
    self._write_fn = write_fn
    self._cv = threading.Condition(threading.Lock())
    self._dirty = False
    self._gen = 0
    self._stop = False
    self._force = False
    self._force_done = 0
    self._force_asked = 0
    self._error: BaseException | None = None
    self._last = 0.0
    self._thread: threading.Thread | None = None

  @property
  def dirty(self) -> bool:
    """
    Whether unwritten changes are pending.

    Returns:
      bool: True when a write is owed.

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).dirty
      False
    """
    return self._dirty

  def start(self) -> None:
    """
    Start the writer thread once (idempotent).

    Returns:
      None

    Examples:
      >>> w = WriteBehindWriter(name="x", write_fn=lambda f: None)
      >>> w.start()
      ... w.stop()
    """
    with self._cv:
      if self._thread is not None and self._thread.is_alive():
        return
      self._stop = False
      self._thread = threading.Thread(
        target=self._main, name=self.name, daemon=True
      )
      self._thread.start()
    with _REGISTRY_LOCK:
      if self not in _WRITERS:
        _WRITERS.append(self)

  def mark_dirty(self) -> None:
    """
    Record a change and wake the writer; never touches disk.

    Returns:
      None

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).mark_dirty()
    """
    with self._cv:
      self._dirty = True
      self._gen += 1
      self._cv.notify_all()

  @property
  def generation(self) -> int:
    """
    Number of dirty marks recorded so far.

    Returns:
      int: Monotonic dirty-mark counter.

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).generation
      0
    """
    return self._gen

  def clear_dirty(self) -> None:
    """
    Drop the dirty flag without writing (state was just restored from disk).

    Returns:
      None

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).clear_dirty()
    """
    with self._cv:
      self._dirty = False

  def flush(self, *, inline_ok: bool = False) -> None:
    """
    Force a write and wait for it; raise the write error if it failed.

    Args:
      inline_ok (bool): Write on the caller when no thread is running
        instead of raising ``RuntimeError``.

    Returns:
      None

    Raises:
      RuntimeError: No thread is running and ``inline_ok`` is False.
      TimeoutError: The writer did not finish within the force timeout.
      self._error: The write failure, re-raised to the caller.

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).flush(
      ...   inline_ok=True
      ... )
    """
    with self._cv:
      alive = self._thread is not None and self._thread.is_alive()
      if alive:
        self._force_asked += 1
        ticket = self._force_asked
        self._force = True
        self._error = None
        self._cv.notify_all()
        deadline = time.monotonic() + FORCE_FLUSH_TIMEOUT_S
        while self._force_done < ticket:
          left = deadline - time.monotonic()
          if left <= 0:
            raise TimeoutError(f"write-behind flush timed out: {self.name}")
          self._cv.wait(left)
        if self._error is not None:
          raise self._error
        return
    if not inline_ok:
      raise RuntimeError(f"write-behind writer not running: {self.name}")
    self._attempt(True)
    if self._error is not None:
      raise self._error

  def stop(self, *, final_flush: bool = True) -> None:
    """
    Stop the thread; flush pending changes first unless disabled.

    Args:
      final_flush (bool): Write pending dirty state before exiting.

    Returns:
      None

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None).stop()
    """
    with self._cv:
      thread = self._thread
      self._stop = True
      if not final_flush:
        self._dirty = False
      self._cv.notify_all()
    if thread is not None and thread.is_alive():
      thread.join(FORCE_FLUSH_TIMEOUT_S)
    with _REGISTRY_LOCK:
      if self in _WRITERS:
        _WRITERS.remove(self)

  def _attempt(self, force: bool) -> None:
    """
    Run one write; clear dirty only when no change raced it.

    Args:
      force (bool): Passed through to ``write_fn``.

    Returns:
      None

    Examples:
      >>> WriteBehindWriter(name="x", write_fn=lambda f: None)._attempt(True)
    """
    with self._cv:
      gen = self._gen
    try:
      self._write_fn(force)
    except BaseException as exc:
      with self._cv:
        self._error = exc
      return
    with self._cv:
      self._error = None
      self._last = time.monotonic()
      if self._gen == gen:
        self._dirty = False

  def _main(self) -> None:
    """
    Writer thread body.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    set_daemon_thread_title(
      "sync_timedb.py", script_name="sync_timedb.py", role=self.name
    )
    while True:
      with self._cv:
        while True:
          if self._force:
            break
          if self._stop:
            if not self._dirty:
              return
            break
          if self._dirty:
            left = self.interval_s - (time.monotonic() - self._last)
            if left <= 0:
              break
            self._cv.wait(left)
          else:
            self._cv.wait()
        forced = self._force
        self._force = False
        asked = self._force_asked
      self._attempt(forced or self._stop)
      with self._cv:
        if forced:
          self._force_done = max(self._force_done, asked)
        self._cv.notify_all()
        if self._stop and self._error is not None:
          return


class WriteBehindEntryStore:
  """
  In-memory master of a ``{"entries": {...}}`` sidecar with lazy persistence.

  Attributes:
    path: Sidecar document path.
    kind: Persistence artifact kind.
    _entries: Live master mapping, ``None`` until first load.
    _io_lock: Serializes disk writes.
    _load: Uncached disk loader.
    _lock: Guards ``_entries`` and ``_tombstones``.
    _schema_version: Envelope ``schema_version`` to write.
    _tombstones: Removed keys not yet persisted.
    _writer: Coalescing write-behind writer.
  """

  def __init__(
    self,
    path: str,
    kind: str,
    *,
    schema_version: int,
    load_entries: Callable[[str], dict],
    interval_s: float = DEFAULT_WRITE_BEHIND_INTERVAL_S,
  ) -> None:
    """
    Create a store; the document is read lazily on first access.

    Args:
      path (str): Sidecar document path.
      kind (str): Persistence artifact kind.
      schema_version (int): Envelope ``schema_version`` to write.
      load_entries (Callable[[str], dict]): Uncached disk loader.
      interval_s (float): Coalescing interval for the writer.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    self.path = path
    self.kind = kind
    self._schema_version = schema_version
    self._load = load_entries
    self._lock = threading.RLock()
    self._io_lock = threading.Lock()
    self._entries: dict[str, Any] | None = None
    self._tombstones: set[str] = set()
    self._writer = WriteBehindWriter(
      name=f"sidecar-persist-{kind}",
      write_fn=self._write,
      interval_s=interval_s,
    )

  def _ensure_loaded(self) -> dict[str, Any]:
    """
    Return the live entry dict, loading from disk once.

    Returns:
      dict[str, Any]: Live master mapping (caller holds ``_lock``).

    Examples:
      >>> True
      True
    """
    if self._entries is None:
      self._entries = dict(self._load(self.path))
    return self._entries

  def contains(self, key: str) -> bool:
    """
    Report whether a key is present.

    Args:
      key (str): Entry key.

    Returns:
      bool: True when present.

    Examples:
      >>> True
      True
    """
    with self._lock:
      return key in self._ensure_loaded()

  def get(self, key: str) -> Any:
    """
    Return one entry or ``None``.

    Args:
      key (str): Entry key.

    Returns:
      Any: Stored value or ``None``.

    Examples:
      >>> True
      True
    """
    with self._lock:
      return self._ensure_loaded().get(key)

  def snapshot(self) -> dict[str, Any]:
    """
    Return a shallow copy of all entries.

    Returns:
      dict[str, Any]: Copy of the master mapping.

    Examples:
      >>> True
      True
    """
    with self._lock:
      return dict(self._ensure_loaded())

  def set(self, key: str, value: Any) -> None:
    """
    Insert or replace an entry; no disk I/O.

    Args:
      key (str): Entry key.
      value (Any): JSON-serializable value.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    with self._lock:
      self._ensure_loaded()[key] = value
      self._tombstones.discard(key)
    self._writer.start()
    self._writer.mark_dirty()

  def remove_if(self, predicate: Callable[[str, Any], bool]) -> int:
    """
    Remove matching entries and persist the removal before returning.

    Args:
      predicate (Callable[[str, Any], bool]): ``(key, value)`` selector.

    Returns:
      int: Number of entries removed.

    Examples:
      >>> True
      True
    """
    with self._lock:
      entries = self._ensure_loaded()
      doomed = [k for k, v in entries.items() if predicate(k, v)]
      for k in doomed:
        del entries[k]
        self._tombstones.add(k)
    if doomed:
      self._write(True)
    return len(doomed)

  def replace_all(self, entries: dict[str, Any]) -> None:
    """
    Replace the whole mapping and persist before returning.

    Args:
      entries (dict[str, Any]): New contents.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    with self._lock:
      old = self._ensure_loaded()
      self._tombstones.update(k for k in old if k not in entries)
      self._entries = dict(entries)
    self._write(True)

  def flush(self) -> None:
    """
    Write pending state now (inline when the thread is not running).

    Returns:
      None

    Examples:
      >>> True
      True
    """
    self._writer.flush(inline_ok=True)

  def stop(self, *, final_flush: bool = True) -> None:
    """
    Stop the writer thread.

    Args:
      final_flush (bool): Write pending state first.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    self._writer.stop(final_flush=final_flush)

  def _write(self, force: bool) -> None:
    """
    Merge with disk under the file lock and save one snapshot.

    Args:
      force (bool): Unused; present for the writer callback shape.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    del force
    with self._io_lock:
      with self._lock:
        mine = dict(self._ensure_loaded())
        dead = set(self._tombstones)
      with file_write_lock(self.path):
        merged = {
          k: v for k, v in self._load(self.path).items() if k not in dead
        }
        merged.update(mine)
        save_persistence_document(
          self.path,
          self.kind,
          {"schema_version": self._schema_version, "entries": merged},
        )
      with self._lock:
        live = self._ensure_loaded()
        for k, v in merged.items():
          if k not in live and k not in self._tombstones:
            live[k] = v
        self._tombstones -= dead


def get_entry_store(
  path: str,
  kind: str,
  *,
  schema_version: int,
  load_entries: Callable[[str], dict],
  interval_s: float = DEFAULT_WRITE_BEHIND_INTERVAL_S,
) -> WriteBehindEntryStore:
  """
  Return the process-wide store for ``path`` (created on first use).

  Args:
    path (str): Sidecar document path.
    kind (str): Persistence artifact kind.
    schema_version (int): Envelope ``schema_version`` to write.
    load_entries (Callable[[str], dict]): Uncached disk loader.
    interval_s (float): Coalescing interval for a new store.

  Returns:
    WriteBehindEntryStore: Shared store.

  Examples:
    >>> True
    True
  """
  with _REGISTRY_LOCK:
    store = _STORES.get(path)
    if store is None:
      store = WriteBehindEntryStore(
        path,
        kind,
        schema_version=schema_version,
        load_entries=load_entries,
        interval_s=interval_s,
      )
      _STORES[path] = store
    return store


def flush_all_write_behind() -> None:
  """
  Flush every dirty registered store and writer (shutdown barrier).

  Returns:
    None

  Examples:
    >>> flush_all_write_behind()
  """
  with _REGISTRY_LOCK:
    stores = list(_STORES.values())
    writers = list(_WRITERS)
  for store in stores:
    if store._writer.dirty:
      store.flush()
  for writer in writers:
    if writer.dirty:
      writer.flush(inline_ok=True)


def reset_write_behind_for_tests(*, flush: bool = True) -> None:
  """
  Stop all writers and forget all stores (tests; ``flush=False`` = crash).

  Args:
    flush (bool): Write pending state before stopping.

  Returns:
    None

  Examples:
    >>> reset_write_behind_for_tests()
  """
  with _REGISTRY_LOCK:
    stores = list(_STORES.values())
    writers = list(_WRITERS)
    _STORES.clear()
  for store in stores:
    store.stop(final_flush=flush)
  for writer in writers:
    writer.stop(final_flush=flush)


atexit.register(flush_all_write_behind)

__all__ = [
  "DEFAULT_WRITE_BEHIND_INTERVAL_S",
  "WriteBehindEntryStore",
  "WriteBehindWriter",
  "flush_all_write_behind",
  "get_entry_store",
  "load_persistence_document",
  "reset_write_behind_for_tests",
]
