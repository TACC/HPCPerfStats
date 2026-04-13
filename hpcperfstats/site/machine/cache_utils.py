"""Redis-backed caching for Django ORM query results.

Use cached_orm() to wrap any callable that performs a read and returns
a cacheable value (e.g. list of dicts, model instance, DataFrame-serializable).
Cache keys should be unique per query; timeouts are in seconds.
"""
import hashlib
import logging
import os
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Max
from django.utils import timezone

# Sentinel so we can cache None (e.g. "job not found")
_CACHE_MISS = object()

# Site-wide freshness: max(job_data.end_time) within this window => short TTL.
SITE_FRESHNESS_WINDOW_DAYS = 14
SITE_CACHE_TTL_FRESH_SECONDS = 3600
# How long to cache the Max(end_time) probe itself (seconds).
SITE_NEWEST_END_META_TTL_SECONDS = 60

KEY_SITE_NEWEST_JOB_END = "site_newest_job_end_v1"

# Sentinel: cached probe value could not be interpreted as a datetime (corrupt / legacy key).
_INVALID_SITE_NEWEST_END_PROBE = object()


def _coerce_site_newest_job_end_time(m):
  """Normalize DB or cache probe to timezone-aware datetime, or None. _INVALID_* if unusable."""
  if m is None:
    return None
  if isinstance(m, datetime):
    if m.tzinfo is None:
      return timezone.make_aware(m, dt_timezone.utc)
    return m
  if isinstance(m, (int, float)):
    ts = float(m)
    if ts > 1e12:
      ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
  if isinstance(m, str):
    s = m.strip()
    if s.endswith("Z"):
      s = s[:-1] + "+00:00"
    try:
      parsed = datetime.fromisoformat(s)
    except ValueError:
      return _INVALID_SITE_NEWEST_END_PROBE
    if parsed.tzinfo is None:
      return timezone.make_aware(parsed, dt_timezone.utc)
    return parsed
  return _INVALID_SITE_NEWEST_END_PROBE


def _cache_debug_enabled() -> bool:
  """Return True if extra cache debug logging should be enabled."""
  if getattr(settings, "DEBUG", False):
    return True
  return os.environ.get("HPCPERF_CACHE_DEBUG", "").lower() in ("1", "true", "yes")


def cached_orm(cache_key, timeout, query_fn):
  """Execute query_fn() on cache miss; return cached value on hit.

    query_fn is a callable that takes no arguments and returns the value to cache.
    The value must be picklable (e.g. list of dicts from .values(), or None).
    None is stored as a wrapped tuple so we can distinguish "missing key" from "cached None".
    If the cache backend is unavailable (e.g. Redis down), query_fn() is used and the result is not cached.
    When DEBUG or HPCPERF_CACHE_DEBUG is enabled, log basic hit/miss and timing
    information for visibility into heavy ORM/cache usage.
    """
  log_debug = _cache_debug_enabled()
  logger = logging.getLogger(__name__)
  start = time.time() if log_debug else None
  try:
    wrapped = cache.get(cache_key, _CACHE_MISS)
  except Exception:
    if log_debug and start is not None:
      logger.exception(
          "cached_orm cache.get error key=%s elapsed_ms=%.1f (falling back to query_fn)",
          cache_key,
          (time.time() - start) * 1000.0,
      )
    close_old_connections()
    return query_fn()

  if wrapped is not _CACHE_MISS:
    if log_debug and start is not None:
      logger.info(
          "cached_orm hit key=%s elapsed_ms=%.1f",
          cache_key,
          (time.time() - start) * 1000.0,
      )
    return (wrapped[0]
            if isinstance(wrapped, tuple) and len(wrapped) == 1 else wrapped)

  value = query_fn()
  try:
    cache.set(cache_key, (value,) if value is None else value, timeout=timeout)
  except Exception:
    if log_debug and start is not None:
      logger.exception(
          "cached_orm cache.set error key=%s elapsed_ms=%.1f (returning uncached)",
          cache_key,
          (time.time() - start) * 1000.0,
      )
  if log_debug and start is not None:
    logger.info(
        "cached_orm miss key=%s elapsed_ms=%.1f",
        cache_key,
        (time.time() - start) * 1000.0,
    )
  return value


def _unwrap_meta_value(wrapped):
  if isinstance(wrapped, tuple) and len(wrapped) == 1:
    return wrapped[0]
  return wrapped


def get_site_newest_job_end_time():
  """Return max(job_data.end_time) with a short-lived cache; None if no jobs.

  Values are normalized to timezone-aware datetimes. Cache entries may be legacy
  ints (Unix epoch) or ISO strings depending on serializer — those are accepted.
  """
  try:
    from .models import job_data

    def from_db():
      return job_data.objects.aggregate(x=Max("end_time"))["x"]

    wrapped = cache.get(KEY_SITE_NEWEST_JOB_END, _CACHE_MISS)
    if wrapped is not _CACHE_MISS:
      raw = _unwrap_meta_value(wrapped)
      coerced = _coerce_site_newest_job_end_time(raw)
      if coerced is not _INVALID_SITE_NEWEST_END_PROBE:
        return coerced
      try:
        cache.delete(KEY_SITE_NEWEST_JOB_END)
      except Exception:
        pass

    m = from_db()
    try:
      cache.set(
          KEY_SITE_NEWEST_JOB_END,
          (m,) if m is None else m,
          timeout=SITE_NEWEST_END_META_TTL_SECONDS,
      )
    except Exception:
      pass
    coerced = _coerce_site_newest_job_end_time(m)
    return None if coerced is _INVALID_SITE_NEWEST_END_PROBE else coerced
  except Exception:
    try:
      from .models import job_data

      raw = job_data.objects.aggregate(x=Max("end_time"))["x"]
      coerced = _coerce_site_newest_job_end_time(raw)
      return None if coerced is _INVALID_SITE_NEWEST_END_PROBE else coerced
    except Exception:
      return None


def get_site_content_cache_timeout():
  """TTL for workload/reference cache entries: 1h if DB is fresh, else None (LRU only).

    Empty DB (no end_time) uses the fresh TTL so new deployments do not stick forever.
    """
  m = get_site_newest_job_end_time()
  if m is None:
    return SITE_CACHE_TTL_FRESH_SECONDS
  if m.tzinfo is None:
    m = timezone.make_aware(m, dt_timezone.utc)
  now = timezone.now()
  if now - m < timedelta(days=SITE_FRESHNESS_WINDOW_DAYS):
    return SITE_CACHE_TTL_FRESH_SECONDS
  return None


def invalidate_after_job_data_ingest(inserted_count):
  """Drop site freshness probe and home_options reference keys after new job_data rows."""
  if inserted_count <= 0:
    return
  try:
    cache.delete(KEY_SITE_NEWEST_JOB_END)
    cache.delete(KEY_DATES)
    cache.delete(KEY_QUEUES)
    cache.delete(KEY_STATES)
  except Exception:
    pass


def warm_job_cache_entries(job_instances, timeout):
  """Seed KEY_JOB:{jid} from in-memory job_data instances (e.g. post bulk_create)."""
  if not job_instances:
    return
  try:
    for obj in job_instances:
      jid = getattr(obj, "jid", None)
      if jid:
        cache.set(f"{KEY_JOB}:{jid}", obj, timeout=timeout)
  except Exception:
    pass


def invalidate_jid_derived_cache_keys(jids):
  """Remove per-job aggregate caches after host_data / proc_data ingest."""
  if not jids:
    return
  invalidate_jid_host_window_row_count_cache(jids)
  try:
    from hpcperfstats.site.machine.models import job_data as _job_data_model

    _job_data_model.objects.filter(
        jid__in=[j for j in jids if j],
    ).update(host_data_schema_json=None)
  except Exception:
    pass
  try:
    for jid in jids:
      if not jid:
        continue
      cache.delete(f"{KEY_GPU_AGG}:v3:{jid}")
      cache.delete(f"{KEY_GPU_COUNT}:{jid}")
      cache.delete(f"{KEY_XALT}:{jid}")
      cache.delete(f"{KEY_PROC_LIST}:{jid}")
  except Exception:
    pass


def _get_redis_py_client():
  """Best-effort redis-py client from Django's default cache (for SCAN)."""
  backend = getattr(cache, "_cache", None)
  if backend is None:
    return None
  client = backend
  if hasattr(client, "get_client"):
    try:
      client = client.get_client()
    except Exception:
      return None
  if client is None:
    client = getattr(cache, "client", None)
    if hasattr(client, "get_client"):
      try:
        client = client.get_client()
      except Exception:
        client = None
  return client


def invalidate_job_plot_cache_keys_for_jids(jids):
  """Delete JOB_PLOTS_JSON / JOB_PLOTS_DATA Redis keys for the given jids (SCAN)."""
  if not jids:
    return
  client = _get_redis_py_client()
  if client is not None:
    try:
      for jid in jids:
        if not jid:
          continue
        keyset_name = f"{KEY_JOB_PLOT_KEYSET}:{jid}"
        try:
          raw_members = client.smembers(keyset_name) or set()
          for raw_key in raw_members:
            try:
              client.delete(raw_key)
            except Exception:
              pass
          client.delete(keyset_name)
        except Exception:
          pass
        for needle in (f":JOB_PLOTS_JSON:{jid}:", f":JOB_PLOTS_DATA:{jid}:"):
          try:
            for raw_key in client.scan_iter(match=f"*{needle}*", count=500):
              try:
                client.delete(raw_key)
              except Exception:
                pass
          except Exception:
            pass
    except Exception:
      pass
  try:
    from hpcperfstats.site.machine.models import job_plot_artifact

    job_plot_artifact.objects.filter(jid_id__in=[j for j in jids if j]).delete()
  except Exception:
    pass


def invalidate_metrics_distinct_cache():
  """Clear distinct metrics list after metrics_data writes."""
  try:
    cache.delete(KEY_METRICS_DISTINCT)
  except Exception:
    pass


# Key prefixes for namespacing
KEY_JOB = "job"
KEY_JOB_HOST_LIST = "job_host_list"
KEY_JOB_SCHEMA = "job_schema"
KEY_METRICS_DISTINCT = "metrics_distinct"
KEY_DATES = "dates"
KEY_QUEUES = "queues"
KEY_STATES = "states"
KEY_LLITE_DELTA = "llite_delta"
KEY_NFS_FSIO = "nfs_fsio"
# Aggregate GPU stats (Count/Max/Avg) cache namespace.
KEY_GPU_AGG = "gpu_agg"
# Per-job sum of per-host max(nvidia_gpu/amd_gpu gpu_count) in the accounting window.
KEY_GPU_COUNT = "gpu_count"
KEY_XALT = "xalt"
KEY_TYPE_DETAIL_HOSTS = "type_detail_hosts"
KEY_HOST_DATA_DF = "host_data_df"
KEY_AGG_DF = "agg_df"
KEY_HOST_TIME_DF = "host_time_df"
KEY_PROC_LIST = "proc_list"
KEY_HOST_PLOT = "host_plot"
KEY_JOB_DICT = "job_dict"
KEY_TYPE_DETAIL_HOST_TIME = "type_detail_host_time"
KEY_TYPE_DETAIL_AGG = "type_detail_agg"
KEY_HOST_SCHEMA = "host_schema"
KEY_ADMIN_CACHE_STATS = "admin_monitor_cache_stats"
KEY_ADMIN_RMQ_STATS = "admin_monitor_rmq_stats"
KEY_ADMIN_RMQ_SNAPSHOT = "admin_monitor_rmq_snapshot"
KEY_ADMIN_TIMESCALE_STATS = "admin_monitor_timescaledb_stats"
KEY_ADMIN_HOST_STATS = "admin_monitor_host_stats"
KEY_ADMIN_XALT_STATS = "admin_monitor_xalt_stats"
KEY_JOB_PLOT_KEYSET = "job_plot_keyset"

# HPCPerfStats Monitor Redis/RabbitMQ stats: short TTL to avoid hammering backends
TIMEOUT_ADMIN_STATS = 10


def make_cache_key(prefix: str, *parts) -> str:
  """Build a cache key from a prefix and optional parts joined by ':'."""
  if not parts:
    return prefix
  return ":".join([prefix] + [str(p) for p in parts])


def make_cache_key_bounded(
    prefix: str,
    *parts,
    max_piece_len: int = 56,
    digest_len: int = 40,
) -> str:
  """Like ``make_cache_key`` but replace overly long *parts* with a SHA-256 prefix.

  Keeps keys under typical Memcached 250-byte limits when *parts* include long
  event-name lists (e.g. aggregate DataFrame cache keys).
  """
  pieces = [prefix]
  for p in parts:
    s = str(p)
    if len(s) > max_piece_len:
      s = hashlib.sha256(s.encode("utf-8")).hexdigest()[:digest_len]
    pieces.append(s)
  return ":".join(pieces)


def register_job_plot_cache_key(jid, cache_key):
  """Track per-jid plot cache keys to avoid expensive wildcard scans."""
  if not jid or not cache_key:
    return
  client = _get_redis_py_client()
  if client is None:
    return
  try:
    client.sadd(f"{KEY_JOB_PLOT_KEYSET}:{jid}", cache_key)
  except Exception:
    pass


KEY_JID_HOST_WINDOW_ROW_COUNT = "jid_hwrow"


def invalidate_jid_host_window_row_count_cache(jids):
  """Drop cached window row counts for ``jid_table`` large-job gating."""
  if not jids:
    return
  client = _get_redis_py_client()
  if client is None:
    return
  try:
    for jid in jids:
      if not jid:
        continue
      needle = "{}:{}:".format(KEY_JID_HOST_WINDOW_ROW_COUNT, jid)
      for raw_key in client.scan_iter(match="*{}*".format(needle), count=500):
        try:
          client.delete(raw_key)
        except Exception:
          pass
  except Exception:
    pass
