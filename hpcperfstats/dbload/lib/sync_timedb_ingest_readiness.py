"""
DB ingest-readiness checks before sync_timedb archives or deletes raw stats
files.

When ``sync_archive_require_db_ingest=yes``, closed segments must pass the
archive/delete gate: classic host_data head+tail **or** a durable zero-host
ingest mark (successful ``stats_rows=0`` / proc-only ingest; ``proc_data`` has
no time column so it cannot mirror head/tail probes).

Host probes use streaming head and EOF-backward tail reads (no full-file load).
The monitor emits fractional seconds; ingest stores subsecond ``time`` values —
probes use Unix-second windows, not exact ``time=`` equality.

Attributes:
  _GATE_DISABLED_LOGGED: Attribute.
  _HEAD_DB_CACHE: Attribute.
  _HEAD_DB_CACHE_MAX_ENTRIES: Attribute.
  _HEAD_DB_CACHE_REFRESH_SECONDS: Attribute.
  _PATH_READY_CACHE: Attribute.
  _PATH_READY_CACHE_MAX_ENTRIES: Attribute.
  _PATH_READY_CACHE_REFRESH_SECONDS: Attribute.
  sampled_identities_ready_in_db: Attribute.
"""
from __future__ import annotations

from typing import Any

import os
import time
from datetime import datetime, timedelta, timezone

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_host_itimes
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    read_stats_file_head_identity,
    read_stats_file_tail_identity,
    stats_file_is_active_segment,
)

_HEAD_DB_CACHE = {}
_PATH_READY_CACHE = {}
_HEAD_DB_CACHE_REFRESH_SECONDS = 60
_HEAD_DB_CACHE_MAX_ENTRIES = 20000
_PATH_READY_CACHE_REFRESH_SECONDS = 60
_PATH_READY_CACHE_MAX_ENTRIES = 20000
_GATE_DISABLED_LOGGED = False


def reset_sync_ingest_readiness_caches() -> None:
  """
  Clear readiness caches between sync_timedb sessions.
  
  Returns:
    None
  
  Examples:
    >>> reset_sync_ingest_readiness_caches()  # doctest: +SKIP
  """
  _HEAD_DB_CACHE.clear()
  _PATH_READY_CACHE.clear()
  global _GATE_DISABLED_LOGGED
  _GATE_DISABLED_LOGGED = False


def path_ingest_ready_fingerprint(path: str) -> Any:
  """
  Return ``(path, mtime, size)`` for cache keying, or ``None`` if missing.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> path_ingest_ready_fingerprint("x")  # doctest: +SKIP
  """
  try:
    st = os.stat(path)
    return (path, int(st.st_mtime), int(st.st_size))
  except OSError:
    return None


def _trim_head_db_cache() -> None:
  """
  Internal helper to handle trim head db cache.
  
  Returns:
    None
  
  Examples:
    >>> _trim_head_db_cache()  # doctest: +SKIP
  """
  if len(_HEAD_DB_CACHE) <= _HEAD_DB_CACHE_MAX_ENTRIES:
    return
  oldest_keys = sorted(
      _HEAD_DB_CACHE.keys(),
      key=lambda k: _HEAD_DB_CACHE[k]["checked_at"],
  )[:1000]
  for drop_key in oldest_keys:
    _HEAD_DB_CACHE.pop(drop_key, None)


def _trim_path_ready_cache() -> None:
  """
  Internal helper to handle trim path ready cache.
  
  Returns:
    None
  
  Examples:
    >>> _trim_path_ready_cache()  # doctest: +SKIP
  """
  if len(_PATH_READY_CACHE) <= _PATH_READY_CACHE_MAX_ENTRIES:
    return
  oldest_keys = sorted(
      _PATH_READY_CACHE.keys(),
      key=lambda k: _PATH_READY_CACHE[k]["checked_at"],
  )[:1000]
  for drop_key in oldest_keys:
    _PATH_READY_CACHE.pop(drop_key, None)


def head_unix_second_window(timestamp_utc: Any) -> Any:
  """
  Return ``(unix_second, inclusive_start, exclusive_end)`` for a head timestamp.
  
  Args:
    timestamp_utc (Any): Timestamp utc passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> head_unix_second_window(None)  # doctest: +SKIP
  """
  ts_sec = int(timestamp_utc.timestamp())
  ts_start = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
  ts_end = ts_start + timedelta(seconds=1)
  return ts_sec, ts_start, ts_end


def head_timestamp_present_in_db(hostname: Any, timestamp_utc: Any) -> Any:
  """
  Return whether ``host_data`` has any row for ``hostname`` in that Unix second.
  
  Args:
    hostname (Any): Hostname passed to this helper.
    timestamp_utc (Any): Timestamp utc passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> head_timestamp_present_in_db(None, None)  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.models import host_data

  _ts_sec, ts_start, ts_end = head_unix_second_window(timestamp_utc)
  key = (hostname, _ts_sec)
  now = time.time()
  cached = _HEAD_DB_CACHE.get(key)
  if cached and (now - cached["checked_at"] <= _HEAD_DB_CACHE_REFRESH_SECONDS):
    return bool(cached["present"])
  present = host_data.objects.filter(
      host=hostname,
      time__gte=ts_start,
      time__lt=ts_end,
  ).exists()
  _HEAD_DB_CACHE[key] = {"present": bool(present), "checked_at": now}
  _trim_head_db_cache()
  return present


def head_tail_identity_as_gate_identities(
  head_identity_by_path: str,
  tail_identity_by_path: str,
) -> Any:
  """
  Convert head/tail ``(host, unix_second)`` maps to batched gate identity shape.
  
  Args:
    head_identity_by_path (str): String for head identity by path.
    tail_identity_by_path (str): String for tail identity by path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> head_tail_identity_as_gate_identities("x", "x")  # doctest: +SKIP
  """
  gate = {}
  for path, head_ident in (head_identity_by_path or {}).items():
    if not head_ident or head_ident[0] is None or head_ident[1] is None:
      continue
    tail_ident = (tail_identity_by_path or {}).get(path)
    if not tail_ident or tail_ident[0] is None or tail_ident[1] is None:
      continue
    by_host = {}
    head_host, head_sec = str(head_ident[0]).strip(), int(head_ident[1])
    tail_host, tail_sec = str(tail_ident[0]).strip(), int(tail_ident[1])
    by_host.setdefault(head_host, set()).add(head_sec)
    by_host.setdefault(tail_host, set()).add(tail_sec)
    gate[path] = by_host
  return gate

def host_timestamp_seconds_all_present(host: Any, unix_seconds: int) -> Any:
  """
  Return whether every Unix second for ``host`` exists in ``host_data``.
  
  Args:
    host (Any): Host passed to this helper.
    unix_seconds (int): Integer value for unix seconds.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> host_timestamp_seconds_all_present(None, 0)  # doctest: +SKIP
  """
  return sync_timedb_host_itimes.host_sampled_timestamp_seconds_all_present(
      host, unix_seconds)


def gate_identities_ready_in_db(gate_by_host: Any) -> Any:
  """
  Return True when every host's gate seconds pass the batched DB gate.
  
  Args:
    gate_by_host (Any): Gate by host passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> gate_identities_ready_in_db(None)  # doctest: +SKIP
  """
  if not gate_by_host:
    return False
  for host, seconds in gate_by_host.items():
    if not host_timestamp_seconds_all_present(host, seconds):
      return False
  return True


# Back-compat alias for older call sites / tests.
sampled_identities_ready_in_db = gate_identities_ready_in_db


def archive_db_head_ingest_gate_enabled() -> Any:
  """
  Whether tar append and raw removal require DB ingest readiness.
  
  Returns:
    Any: Open return polymorphism from
    ``archive_db_head_ingest_gate_enabled``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> archive_db_head_ingest_gate_enabled()  # doctest: +SKIP
  """
  return bool(cfg.get_sync_archive_require_db_ingest())


def _archive_gate_skip_label() -> Any:
  """
  Internal helper to archive the gate skip label.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_gate_skip_label()  # doctest: +SKIP
  """
  return "head/tail timestamps"


def _log_gate_disabled_once(log_fn: Any) -> None:
  """
  Internal helper to log the gate disabled once.
  
  Args:
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> _log_gate_disabled_once(None)  # doctest: +SKIP
  """
  global _GATE_DISABLED_LOGGED
  if _GATE_DISABLED_LOGGED or log_fn is None:
    return
  _GATE_DISABLED_LOGGED = True
  log_fn(
      "sync_archive_require_db_ingest is disabled; skipping DB readiness "
      "checks before archive/delete",
      flush=True,
  )


def _path_head_tail_ready_in_db(path: str) -> Any:
  """
  Return True when head and tail timestamp seconds are present in ``host_data``.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _path_head_tail_ready_in_db("x")  # doctest: +SKIP
  """
  head_host, head_ts = read_stats_file_head_identity(path)
  if head_host is None or head_ts is None:
    return False
  if not head_timestamp_present_in_db(head_host, head_ts):
    return False
  tail_host, tail_ts = read_stats_file_tail_identity(path)
  if tail_host is None or tail_ts is None:
    return False
  if head_host == tail_host and int(head_ts.timestamp()) == int(tail_ts.timestamp()):
    return True
  return head_timestamp_present_in_db(tail_host, tail_ts)


def _path_ready_via_zero_host_mark(path: str) -> Any:
  """
  Internal helper to handle path ready via zero host mark.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _path_ready_via_zero_host_mark("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark import (
      has_zero_host_ingest_mark,
  )

  return bool(has_zero_host_ingest_mark(path))


def _path_ready_via_file_complete_mark(path: str) -> Any:
  """
  Internal helper to handle path ready via file complete mark.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _path_ready_via_file_complete_mark("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark import (
      has_file_complete_ingest_mark,
  )

  return bool(has_file_complete_ingest_mark(path))


def _live_db_ingest_enabled() -> Any:
  """
  Internal helper to handle live db ingest enabled.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _live_db_ingest_enabled()  # doctest: +SKIP
  """
  try:
    return bool(cfg.get_listend_db_ingest_enabled())
  except Exception:
    return False


def stats_file_head_ingested_in_db(
  path: str,
  *,
  log_fn: Any | None = None,
) -> Any:
  """
  Return True when the path is archive/delete ready.
  
  Default (live ingest off): host_data head+tail **or** zero-host mark.
  When listend live DB ingest is on: sync_timedb file-complete mark **or**
  zero-host mark (never live-only head+tail).
  
  Args:
    path (str): String for path.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> stats_file_head_ingested_in_db("x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.sync_timedb import _sync_worker_db_task

  with _sync_worker_db_task():
    if not archive_db_head_ingest_gate_enabled():
      _log_gate_disabled_once(log_fn)
      return True

    fp = path_ingest_ready_fingerprint(path)
    if fp is None:
      return False
    now = time.time()
    path_cached = _PATH_READY_CACHE.get(fp)
    if path_cached and (now - path_cached["checked_at"] <= _PATH_READY_CACHE_REFRESH_SECONDS):
      return bool(path_cached["ready"])

    ready = False
    if not stats_file_is_active_segment(path):
      if _live_db_ingest_enabled():
        ready = (
            _path_ready_via_file_complete_mark(path)
            or _path_ready_via_zero_host_mark(path)
        )
      else:
        ready = _path_head_tail_ready_in_db(path)
        if not ready:
          ready = _path_ready_via_zero_host_mark(path)

    _PATH_READY_CACHE[fp] = {"ready": bool(ready), "checked_at": now}
    _trim_path_ready_cache()
    return ready


def build_head_ingest_ready_set(
  closed_paths: Any,
  gate_identities_by_path: str,
  *,
  log_fn: Any | None = None,
) -> Any:
  """
  Return paths ready via host_data gate seconds OR durable ingest marks.
  
  When listend live DB ingest is on, host_data head+tail alone is insufficient —
  require the sync_timedb file-complete mark (or zero-host mark for proc-only).
  
  Args:
    closed_paths (Any): Iterable of filesystem paths as strings.
    gate_identities_by_path (str): String for gate identities by path.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_head_ingest_ready_set(None, "x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.sync_timedb import _sync_worker_db_task

  with _sync_worker_db_task():
    if not archive_db_head_ingest_gate_enabled():
      _log_gate_disabled_once(log_fn)
      return set(closed_paths or [])

    live_on = _live_db_ingest_enabled()
    ready_paths = set()

    if not live_on:
      seconds_by_host = {}
      path_samples = {}
      for path in closed_paths or []:
        if stats_file_is_active_segment(path):
          continue
        sampled = gate_identities_by_path.get(path)
        if not sampled:
          continue
        path_samples[path] = sampled
        for host, seconds in sampled.items():
          seconds_by_host.setdefault(host, set()).update(seconds)

      host_ok = {
          host: host_timestamp_seconds_all_present(host, seconds)
          for host, seconds in seconds_by_host.items()
      }

      for path, sampled in path_samples.items():
        if all(host_ok.get(host, False) for host in sampled):
          ready_paths.add(path)

    for path in closed_paths or []:
      if path in ready_paths:
        continue
      if stats_file_is_active_segment(path):
        continue
      if live_on:
        if (
            _path_ready_via_file_complete_mark(path)
            or _path_ready_via_zero_host_mark(path)
        ):
          ready_paths.add(path)
      elif _path_ready_via_zero_host_mark(path):
        ready_paths.add(path)
    return ready_paths


def filter_paths_head_ingested(
  paths: Any,
  *,
  log_fn: Any | None = None,
  gate_identities_by_path: Any | None = None,
  sampled_timestamp_identities_by_path: Any | None = None,
  head_identity_by_path: Any | None = None,
) -> Any:
  """
  Return ``(ready_paths, skipped_paths)`` using batched or per-path gate.
  
  ``head_identity_by_path`` alone is not sufficient (head-only would miss
    tails);
  pass ``gate_identities_by_path`` for the batched path, otherwise each path is
  probed with streaming head+tail reads.
  
  Ready when host_data head+tail passes **or** a durable zero-host ingest mark
  is present for the path fingerprint.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    log_fn (Any | None): One of ``Any``, ``None``.
    gate_identities_by_path (Any | None): One of ``Any``, ``None``.
    sampled_timestamp_identities_by_path (Any | None): One of ``Any``,
    ``None``.
    head_identity_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> filter_paths_head_ingested(None, None, None, None, None)
  """
  del head_identity_by_path  # no longer used for head-only batch conversion
  if gate_identities_by_path is None:
    gate_identities_by_path = sampled_timestamp_identities_by_path
  if gate_identities_by_path is not None:
    ready_set = build_head_ingest_ready_set(
        paths,
        gate_identities_by_path,
        log_fn=log_fn,
    )
    ready = [p for p in paths if p in ready_set]
    skipped = [p for p in paths if p not in ready_set]
    # INFO flood removed: day-line gate_skip=/ingest_handoff= owns visibility.
    del log_fn
    return ready, skipped

  ready = []
  skipped = []
  for path in paths:
    if stats_file_head_ingested_in_db(path, log_fn=log_fn):
      ready.append(path)
    else:
      skipped.append(path)
  # INFO flood removed: day-line gate_skip=/ingest_handoff= owns visibility.
  return ready, skipped
