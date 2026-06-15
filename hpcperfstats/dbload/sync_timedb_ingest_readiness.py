"""DB ingest-readiness checks before sync_timedb archives or deletes raw stats files.

When ``sync_archive_require_db_head_ingest=yes``, closed segments must have every
**sampled** timestamp line present in ``host_data`` for the hostname token on that
line (stride = ``sync_bulk_create_batch_size // 2``, plus the last timestamp at EOF).
The monitor emits fractional seconds; ingest stores subsecond ``time`` values — probes
use Unix-second windows, not exact ``time=`` equality.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload import sync_timedb_host_itimes
from hpcperfstats.dbload.sync_timedb_archive_helpers import stats_file_is_active_segment
from hpcperfstats.dbload.sync_timedb_parsing import (
    collect_stats_file_sampled_timestamp_identities_streaming,
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


def head_unix_second_window(timestamp_utc):
  """Return ``(unix_second, inclusive_start, exclusive_end)`` for a head timestamp."""
  ts_sec = int(timestamp_utc.timestamp())
  ts_start = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
  ts_end = ts_start + timedelta(seconds=1)
  return ts_sec, ts_start, ts_end


def head_timestamp_present_in_db(hostname, timestamp_utc):
  """Return whether ``host_data`` has any row for ``hostname`` in that Unix second."""
  from hpcperfstats.site.machine.models import host_data

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


def host_timestamp_seconds_all_present(host, unix_seconds):
  """Return whether every sampled Unix second for ``host`` exists in ``host_data``."""
  return sync_timedb_host_itimes.host_sampled_timestamp_seconds_all_present(
      host, unix_seconds)


def sampled_identities_ready_in_db(sampled_by_host):
  """Return True when every host's sampled seconds pass the batched DB gate."""
  if not sampled_by_host:
    return False
  for host, seconds in sampled_by_host.items():
    if not host_timestamp_seconds_all_present(host, seconds):
      return False
  return True


def archive_db_head_ingest_gate_enabled():
  """Whether tar append and raw removal require sampled timestamps in DB."""
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
  """Return True when every sampled timestamp for the closed segment exists in DB."""
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
      stride = cfg.get_sync_archive_db_ingest_gate_sample_stride()
      sampled_by_host = collect_stats_file_sampled_timestamp_identities_streaming(
          path,
          sample_stride=stride,
      )
      ready = sampled_identities_ready_in_db(sampled_by_host)

    _PATH_READY_CACHE[fp] = {"ready": bool(ready), "checked_at": now}
    _trim_path_ready_cache()
    return ready


def build_head_ingest_ready_set(
    closed_paths,
    sampled_timestamp_identities_by_path,
    *,
    log_fn=None,
):
  """Return paths whose sampled ``(host, unix_second)`` sets all exist in ``host_data``."""
  from hpcperfstats.dbload.sync_timedb import _sync_worker_db_task

  with _sync_worker_db_task():
    if not archive_db_head_ingest_gate_enabled():
      _log_gate_disabled_once(log_fn)
      return set(closed_paths or [])

    seconds_by_host = {}
    path_samples = {}
    for path in closed_paths or []:
      if stats_file_is_active_segment(path):
        continue
      sampled = sampled_timestamp_identities_by_path.get(path)
      if not sampled:
        continue
      path_samples[path] = sampled
      for host, seconds in sampled.items():
        seconds_by_host.setdefault(host, set()).update(seconds)

    host_ok = {
        host: host_timestamp_seconds_all_present(host, seconds)
        for host, seconds in seconds_by_host.items()
    }

    ready_paths = set()
    for path, sampled in path_samples.items():
      if all(host_ok.get(host, False) for host in sampled):
        ready_paths.add(path)
    return ready_paths


def filter_paths_head_ingested(
    paths,
    *,
    log_fn=None,
    sampled_timestamp_identities_by_path=None,
    head_identity_by_path=None,
):
  """Return ``(ready_paths, skipped_paths)`` using batched or per-path gate."""
  if head_identity_by_path is not None and sampled_timestamp_identities_by_path is None:
    sampled_timestamp_identities_by_path = {
        path: {host: {unix_second}}
        for path, (host, unix_second) in head_identity_by_path.items()
    }
  if sampled_timestamp_identities_by_path is not None:
    ready_set = build_head_ingest_ready_set(
        paths,
        sampled_timestamp_identities_by_path,
        log_fn=log_fn,
    )
    ready = [p for p in paths if p in ready_set]
    skipped = [p for p in paths if p not in ready_set]
    if skipped and log_fn is not None:
      log_fn(
          "Archive/delete gate: skipped %d path(s) without sampled timestamps in DB"
          % len(skipped),
          flush=True,
      )
    return ready, skipped

  ready = []
  skipped = []
  for path in paths:
    if stats_file_head_ingested_in_db(path, log_fn=log_fn):
      ready.append(path)
    else:
      skipped.append(path)
  if skipped and log_fn is not None:
    log_fn(
        "Archive/delete gate: skipped %d path(s) without sampled timestamps in DB"
        % len(skipped),
        flush=True,
    )
  return ready, skipped
