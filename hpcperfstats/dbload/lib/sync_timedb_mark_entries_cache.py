"""
Process-local mtime/size L1 cache for durable mark JSON ``entries`` loads.

Used by file-complete and zero-host ingest mark modules so discover-bg /
archive readiness can call ``has_*`` repeatedly without re-loading multi-MiB
sidecars when the on-disk file identity is unchanged.

Attributes:
  _ENTRIES_CACHE: mark_path -> (mtime_ns, size, entries dict).
  _ENTRIES_CACHE_LOCK: FT-safe RLock guarding ``_ENTRIES_CACHE``.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

# mark_path -> (mtime_ns, size, entries)
_ENTRIES_CACHE: dict[str, tuple[int, int, dict]] = {}
_ENTRIES_CACHE_LOCK = threading.RLock()


def clear_mark_entries_cache(mark_path: str | None = None) -> None:
  """
  Drop cached entries for one mark path, or the whole process cache.

  Args:
    mark_path (str | None): Mark JSON path. ``None`` clears all entries.

  Returns:
    None

  Examples:
    >>> clear_mark_entries_cache(None)  # doctest: +SKIP
  """
  with _ENTRIES_CACHE_LOCK:
    if mark_path is None:
      _ENTRIES_CACHE.clear()
      return
    _ENTRIES_CACHE.pop(str(mark_path), None)


def load_cached_mark_entries(
  mark_path: str,
  *,
  load_uncached: Callable[[str], dict],
) -> dict:
  """
  Return mark ``entries`` using process-local (mtime_ns, size) identity.

  On miss or identity change, call ``load_uncached`` and store a shallow copy
  of the returned dict. Callers that mutate entries must clear before save so
  the next load does not serve a stale map.

  Args:
    mark_path (str): Path to the mark JSON file.
    load_uncached (Callable[[str], dict]): Loads entries from disk (no cache).

  Returns:
    dict: Shallow copy of fingerprint -> metadata entries.

  Examples:
    >>> load_cached_mark_entries("/x", load_uncached=lambda _p: {})
    {}
  """
  path = str(mark_path or "")
  if not path:
    return dict(load_uncached(path) or {})
  try:
    st = os.stat(path)
  except OSError:
    clear_mark_entries_cache(path)
    return dict(load_uncached(path) or {})
  identity = (int(st.st_mtime_ns), int(st.st_size))
  with _ENTRIES_CACHE_LOCK:
    cached = _ENTRIES_CACHE.get(path)
    if (
        cached is not None
        and cached[0] == identity[0]
        and cached[1] == identity[1]
    ):
      return dict(cached[2])
  entries = dict(load_uncached(path) or {})
  with _ENTRIES_CACHE_LOCK:
    _ENTRIES_CACHE[path] = (identity[0], identity[1], dict(entries))
  return dict(entries)


def reset_mark_entries_cache_for_tests() -> None:
  """
  Clear the process cache (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_mark_entries_cache_for_tests()  # doctest: +SKIP
  """
  clear_mark_entries_cache(None)
