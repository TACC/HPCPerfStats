"""DB ingest-readiness checks before sync_timedb archives or deletes raw stats files.

Head-ingested means the first stats timestamp line's (host, time) exists in host_data,
where ``host`` is the hostname token from file content (not the archive path dirname).
This is a lightweight gate (one exists() per cache miss); partial ingest may still pass.
"""
import os
import time

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    read_stats_file_head_identity,
    stats_file_is_active_segment,
)

_HEAD_DB_CACHE = {}
_PATH_READY_CACHE = {}
_HEAD_DB_CACHE_REFRESH_SECONDS = 60
_HEAD_DB_CACHE_MAX_ENTRIES = 20000
_PATH_READY_CACHE_REFRESH_SECONDS = 60
_PATH_READY_CACHE_MAX_ENTRIES = 20000
_GATE_DISABLED_LOGGED = False


def reset_sync_ingest_readiness_caches():
  """Clear readiness caches between sync_timedb sessions."""
  _HEAD_DB_CACHE.clear()
  _PATH_READY_CACHE.clear()
  global _GATE_DISABLED_LOGGED
  _GATE_DISABLED_LOGGED = False


def path_ingest_ready_fingerprint(path):
  """Return ``(path, mtime, size)`` for cache keying, or ``None`` if missing."""
  try:
    st = os.stat(path)
    return (path, int(st.st_mtime), int(st.st_size))
  except OSError:
    return None


def _trim_head_db_cache():
  if len(_HEAD_DB_CACHE) <= _HEAD_DB_CACHE_MAX_ENTRIES:
    return
  oldest_keys = sorted(
      _HEAD_DB_CACHE.keys(),
      key=lambda k: _HEAD_DB_CACHE[k]["checked_at"],
  )[:1000]
  for drop_key in oldest_keys:
    _HEAD_DB_CACHE.pop(drop_key, None)


def _trim_path_ready_cache():
  if len(_PATH_READY_CACHE) <= _PATH_READY_CACHE_MAX_ENTRIES:
    return
  oldest_keys = sorted(
      _PATH_READY_CACHE.keys(),
      key=lambda k: _PATH_READY_CACHE[k]["checked_at"],
  )[:1000]
  for drop_key in oldest_keys:
    _PATH_READY_CACHE.pop(drop_key, None)


def head_timestamp_present_in_db(hostname, timestamp_utc):
  """Return whether ``host_data`` has a row for ``(hostname, timestamp_utc)``."""
  from hpcperfstats.site.machine.models import host_data

  key = (hostname, int(timestamp_utc.timestamp()))
  now = time.time()
  cached = _HEAD_DB_CACHE.get(key)
  if cached and (now - cached["checked_at"] <= _HEAD_DB_CACHE_REFRESH_SECONDS):
    return bool(cached["present"])
  present = host_data.objects.filter(host=hostname, time=timestamp_utc).exists()
  _HEAD_DB_CACHE[key] = {"present": bool(present), "checked_at": now}
  _trim_head_db_cache()
  return present


def archive_db_head_ingest_gate_enabled():
  """Whether tar append and raw removal require head timestamp in DB."""
  return bool(cfg.get_sync_archive_require_db_head_ingest())


def _log_gate_disabled_once(log_fn):
  global _GATE_DISABLED_LOGGED
  if _GATE_DISABLED_LOGGED or log_fn is None:
    return
  _GATE_DISABLED_LOGGED = True
  log_fn(
      "sync_archive_require_db_head_ingest is disabled; skipping DB readiness "
      "checks before archive/delete",
      flush=True,
  )


def stats_file_head_ingested_in_db(path, *, log_fn=None):
  """Return True when the file's head timestamp exists in host_data for its host."""
  from django.db import close_old_connections

  close_old_connections()
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
  if stats_file_is_active_segment(path):
    ready = False
  else:
    host, timestamp_utc = read_stats_file_head_identity(path)
    if host is not None and timestamp_utc is not None:
      ready = head_timestamp_present_in_db(host, timestamp_utc)

  _PATH_READY_CACHE[fp] = {"ready": bool(ready), "checked_at": now}
  _trim_path_ready_cache()
  return ready


def filter_paths_head_ingested(paths, *, log_fn=None):
  """Return ``(ready_paths, skipped_paths)`` using ``stats_file_head_ingested_in_db``."""
  ready = []
  skipped = []
  for path in paths:
    if stats_file_head_ingested_in_db(path, log_fn=log_fn):
      ready.append(path)
    else:
      skipped.append(path)
  if skipped and log_fn is not None:
    log_fn(
        "Archive/delete gate: skipped %d path(s) without head timestamp in DB"
        % len(skipped),
        flush=True,
    )
  return ready, skipped
