"""Shared host_data timestamp probes for sync_timedb ingest and archive gate."""
import time
from datetime import datetime, timedelta, timezone

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.site.lib.machine.models import host_data

HOST_ITIMES_SET_OVERFLOW = object()

_HOST_ITIMES_CACHE = {}
_HOST_ITIMES_CACHE_REFRESH_SECONDS = 20
_HOST_ITIMES_CACHE_MAX_ENTRIES = 2000
_HOST_SECOND_PRESENT_CACHE = {}
_HOST_SECOND_PRESENT_CACHE_TTL_S = 60
_HOST_SECOND_PRESENT_CACHE_MAX_ENTRIES = 50000

# Back-compat alias for sync_timedb module attribute re-exports.
_HOST_ITIMES_SET_OVERFLOW = HOST_ITIMES_SET_OVERFLOW


def reset_host_itimes_caches():
  """Clear per-process itimes caches between sync_timedb sessions."""
  _HOST_ITIMES_CACHE.clear()
  _HOST_SECOND_PRESENT_CACHE.clear()


def host_recent_timestamps_cached(hostname, ts_low, ts_high):
  """Return cached distinct Unix seconds for ``hostname`` in ``[ts_low, ts_high)``."""
  key = (hostname, int(ts_low.timestamp()), int(ts_high.timestamp()))
  now = time.time()
  cached = _HOST_ITIMES_CACHE.get(key)
  if cached and (now - cached["checked_at"] <= _HOST_ITIMES_CACHE_REFRESH_SECONDS):
    return set(cached["times"])
  itimes_set = set()
  qs_times = (
      host_data.objects.filter(
          host=hostname,
          time__gte=ts_low,
          time__lt=ts_high,
      )
      .values_list("time", flat=True)
      .distinct()
  )
  for dt in qs_times.iterator():
    if dt is None:
      continue
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    itimes_set.add(int(dt.timestamp()))
    max_timestamps = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()
    if len(itimes_set) > max_timestamps:
      return HOST_ITIMES_SET_OVERFLOW
  max_timestamps = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()
  if len(itimes_set) <= max_timestamps:
    _HOST_ITIMES_CACHE[key] = {"times": tuple(itimes_set), "checked_at": now}
  if len(_HOST_ITIMES_CACHE) > _HOST_ITIMES_CACHE_MAX_ENTRIES:
    oldest_keys = sorted(
        _HOST_ITIMES_CACHE.keys(),
        key=lambda k: _HOST_ITIMES_CACHE[k]["checked_at"],
    )[:100]
    for drop_key in oldest_keys:
      _HOST_ITIMES_CACHE.pop(drop_key, None)
  return itimes_set


def host_timestamp_second_present_in_db(host, unix_second):
  """Per-(host, second) exists probe when host_itimes cache overflows."""
  key = (str(host).strip(), int(unix_second))
  now = time.time()
  cached = _HOST_SECOND_PRESENT_CACHE.get(key)
  if cached and (now - cached[1] <= _HOST_SECOND_PRESENT_CACHE_TTL_S):
    return cached[0]
  ts_low = datetime.fromtimestamp(int(unix_second), tz=timezone.utc)
  ts_high = ts_low + timedelta(seconds=1)
  present = host_data.objects.filter(
      host=key[0],
      time__gte=ts_low,
      time__lt=ts_high,
  ).exists()
  _HOST_SECOND_PRESENT_CACHE[key] = (present, now)
  if len(_HOST_SECOND_PRESENT_CACHE) > _HOST_SECOND_PRESENT_CACHE_MAX_ENTRIES:
    oldest = sorted(
        _HOST_SECOND_PRESENT_CACHE.keys(),
        key=lambda k: _HOST_SECOND_PRESENT_CACHE[k][1],
    )[:1000]
    for drop_key in oldest:
      _HOST_SECOND_PRESENT_CACHE.pop(drop_key, None)
  return present


def host_sampled_timestamp_seconds_all_present(host, unix_seconds):
  """Return whether every Unix second in ``unix_seconds`` exists for ``host`` in DB."""
  if not unix_seconds:
    return False
  host_key = str(host).strip()
  seconds = {int(s) for s in unix_seconds}
  ts_low = datetime.fromtimestamp(min(seconds), tz=timezone.utc)
  ts_high = datetime.fromtimestamp(max(seconds), tz=timezone.utc) + timedelta(seconds=1)
  itimes_set = host_recent_timestamps_cached(host_key, ts_low, ts_high)
  if itimes_set is HOST_ITIMES_SET_OVERFLOW:
    for unix_second in seconds:
      if not host_timestamp_second_present_in_db(host_key, unix_second):
        return False
    return True
  return all(unix_second in itimes_set for unix_second in seconds)
