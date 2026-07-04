"""DB ingest-readiness checks before sync_timedb archives or deletes raw stats files.

When ``sync_archive_require_db_ingest=yes``, closed segments must pass the
head+tail gate: first and last digit-leading timestamp lines for their hostname
tokens must each have a ``host_data`` row in the same Unix second.

Probes use streaming head and EOF-backward tail reads (no full-file load). The
monitor emits fractional seconds; ingest stores subsecond ``time`` values —
probes use Unix-second windows, not exact ``time=`` equality.
"""
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


def head_tail_identity_as_gate_identities(head_identity_by_path, tail_identity_by_path):
  """Convert head/tail ``(host, unix_second)`` maps to batched gate identity shape."""
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


def head_identity_as_gate_identities(head_identity_by_path):
  """Deprecated head-only converter; prefer ``head_tail_identity_as_gate_identities``."""
  return {
      path: {host: {unix_second}}
      for path, (host, unix_second) in (head_identity_by_path or {}).items()
  }


def host_timestamp_seconds_all_present(host, unix_seconds):
  """Return whether every Unix second for ``host`` exists in ``host_data``."""
  return sync_timedb_host_itimes.host_sampled_timestamp_seconds_all_present(
      host, unix_seconds)


def gate_identities_ready_in_db(gate_by_host):
  """Return True when every host's gate seconds pass the batched DB gate."""
  if not gate_by_host:
    return False
  for host, seconds in gate_by_host.items():
    if not host_timestamp_seconds_all_present(host, seconds):
      return False
  return True


# Back-compat alias for older call sites / tests.
sampled_identities_ready_in_db = gate_identities_ready_in_db


def archive_db_head_ingest_gate_enabled():
  """Whether tar append and raw removal require DB ingest readiness."""
  return bool(cfg.get_sync_archive_require_db_ingest())


def _archive_gate_skip_label():
  return "head/tail timestamps"


def _log_gate_disabled_once(log_fn):
  global _GATE_DISABLED_LOGGED
  if _GATE_DISABLED_LOGGED or log_fn is None:
    return
  _GATE_DISABLED_LOGGED = True
  log_fn(
      "sync_archive_require_db_ingest is disabled; skipping DB readiness "
      "checks before archive/delete",
      flush=True,
  )


def _path_head_tail_ready_in_db(path):
  """Return True when head and tail timestamp seconds are present in ``host_data``."""
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


def stats_file_head_ingested_in_db(path, *, log_fn=None):
  """Return True when the closed segment passes the head+tail DB ingest gate."""
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
      ready = _path_head_tail_ready_in_db(path)

    _PATH_READY_CACHE[fp] = {"ready": bool(ready), "checked_at": now}
    _trim_path_ready_cache()
    return ready


def build_head_ingest_ready_set(
    closed_paths,
    gate_identities_by_path,
    *,
    log_fn=None,
):
  """Return paths whose gate ``(host, unix_second)`` sets all exist in ``host_data``."""
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

    ready_paths = set()
    for path, sampled in path_samples.items():
      if all(host_ok.get(host, False) for host in sampled):
        ready_paths.add(path)
    return ready_paths


def filter_paths_head_ingested(
    paths,
    *,
    log_fn=None,
    gate_identities_by_path=None,
    sampled_timestamp_identities_by_path=None,
    head_identity_by_path=None,
):
  """Return ``(ready_paths, skipped_paths)`` using batched or per-path gate.

  ``head_identity_by_path`` alone is not sufficient (head-only would miss tails);
  pass ``gate_identities_by_path`` for the batched path, otherwise each path is
  probed with streaming head+tail reads.
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
    if skipped and log_fn is not None:
      log_fn(
          "Archive/delete gate: skipped %d path(s) without %s in DB"
          % (len(skipped), _archive_gate_skip_label()),
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
        "Archive/delete gate: skipped %d path(s) without %s in DB"
        % (len(skipped), _archive_gate_skip_label()),
        flush=True,
    )
  return ready, skipped
