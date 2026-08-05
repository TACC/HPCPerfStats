"""
Redis-backed caching for Django ORM query results.

Use cached_orm() to wrap any callable that performs a read and returns a
cacheable value (e.g. list of dicts, model instance, DataFrame-serializable).
Cache keys should be unique per query; timeouts are in seconds.

Attributes:
  KEY_ADMIN_CACHE_STATS: Attribute.
  KEY_ADMIN_HOST_STATS: Attribute.
  KEY_ADMIN_RMQ_SNAPSHOT: Attribute.
  KEY_ADMIN_RMQ_STATS: Attribute.
  KEY_ADMIN_TIMESCALE_STATS: Attribute.
  KEY_ADMIN_XALT_STATS: Attribute.
  KEY_AGG_DF: Attribute.
  KEY_DATES: Attribute.
  KEY_GPU_AGG: Attribute.
  KEY_GPU_COUNT: Attribute.
  KEY_HOST_DATA_DF: Attribute.
  KEY_HOST_PLOT: Attribute.
  KEY_HOST_SCHEMA: Attribute.
  KEY_HOST_TIME_DF: Attribute.
  KEY_JID_HOST_WINDOW_ROW_COUNT: Attribute.
  KEY_JOB: Attribute.
  KEY_JOB_CACHE_VERSION: Attribute.
  KEY_JOB_DICT: Attribute.
  KEY_JOB_HOST_LIST: Attribute.
  KEY_JOB_JID_TABLE_WINDOW: Attribute.
  KEY_JOB_PLOT_KEYSET: Attribute.
  KEY_JOB_SCHEMA: Attribute.
  KEY_LLITE_DELTA: Attribute.
  KEY_METRICS_DISTINCT: Attribute.
  KEY_NFS_FSIO: Attribute.
  KEY_NONSTAFF_ACCOUNTS: Attribute.
  KEY_PROC_LIST: Attribute.
  KEY_QUEUES: Attribute.
  KEY_SITE_NEWEST_JOB_END: Attribute.
  KEY_STATES: Attribute.
  KEY_TYPE_DETAIL_AGG: Attribute.
  KEY_TYPE_DETAIL_HOSTS: Attribute.
  KEY_TYPE_DETAIL_HOST_TIME: Attribute.
  KEY_XALT: Attribute.
  SITE_CACHE_TTL_FRESH_SECONDS: Attribute.
  SITE_FRESHNESS_WINDOW_DAYS: Attribute.
  SITE_NEWEST_END_META_TTL_SECONDS: Attribute.
  TIMEOUT_ADMIN_STATS: Attribute.
  _CACHE_MISS: Attribute.
  _INVALID_SITE_NEWEST_END_PROBE: Attribute.
"""
from __future__ import annotations

from typing import Any

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
from django.db.models.query import prefetch_related_objects
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


def _coerce_site_newest_job_end_time(m: Any) -> Any:
  """
  Normalize DB or cache probe to timezone-aware datetime, or None. _INVALID_*.
  
    if.
  
    unusable.
  
  Args:
    m (Any): M passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerce_site_newest_job_end_time(None)  # doctest: +SKIP
  """
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
  """
  Return True if extra cache debug logging should be enabled.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _cache_debug_enabled()  # doctest: +SKIP
  """
  if getattr(settings, "DEBUG", False):
    return True
  return os.environ.get("HPCPERF_CACHE_DEBUG", "").lower() in ("1", "true", "yes")


def cached_orm(cache_key: Any, timeout: int, query_fn: Any) -> Any:
  """
  Execute query_fn() on cache miss; return cached value on hit.
  
  query_fn is a callable that takes no arguments and returns the value to cache.
  The value must be picklable (e.g. list of dicts from .values(), or None).
  None is stored as a wrapped tuple so we can distinguish "missing key" from
    "cached None".
  If the cache backend is unavailable (e.g. Redis down), query_fn() is used and
    the result is not cached.
  When DEBUG or HPCPERF_CACHE_DEBUG is enabled, log basic hit/miss and timing
  information for visibility into heavy ORM/cache usage.
  
  Args:
    cache_key (Any): Cache key passed to this helper.
    timeout (int): Integer value for timeout.
    query_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cached_orm(None, 0, None)  # doctest: +SKIP
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


def _unwrap_meta_value(wrapped: Any) -> Any:
  """
  Internal helper to handle unwrap meta value.
  
  Args:
    wrapped (Any): Wrapped passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unwrap_meta_value(None)  # doctest: +SKIP
  """
  if isinstance(wrapped, tuple) and len(wrapped) == 1:
    return wrapped[0]
  return wrapped


def get_site_newest_job_end_time() -> Any:
  """
  Return max(job_data.end_time) with a short-lived cache; None if no jobs.
  
  Values are normalized to timezone-aware datetimes. Cache entries may be legacy
  ints (Unix epoch) or ISO strings depending on serializer — those are accepted.
  
  Returns:
    Any: Open return polymorphism from ``get_site_newest_job_end_time``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_site_newest_job_end_time()  # doctest: +SKIP
  """
  try:
    from .models import job_data

    def from_db() -> Any:
      """
      From db.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> from_db()  # doctest: +SKIP
      """
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


def get_site_content_cache_timeout() -> Any:
  """
  TTL for workload/reference cache entries: 1h if DB is fresh, else None (LRU.
  
    only).
  
  Empty DB (no end_time) uses the fresh TTL so new deployments do not stick
    forever.
  
  Returns:
    Any: Open return polymorphism from ``get_site_content_cache_timeout``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_site_content_cache_timeout()  # doctest: +SKIP
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


def invalidate_home_options_query_cache() -> None:
  """
  Drop cached_orm keys and site newest probe used by GET /api/home/.
  
    (home_options).
  
  Returns:
    None
  
  Examples:
    >>> invalidate_home_options_query_cache()  # doctest: +SKIP
  """
  try:
    cache.delete(KEY_SITE_NEWEST_JOB_END)
    cache.delete(KEY_DATES)
    cache.delete(KEY_METRICS_DISTINCT)
    cache.delete(KEY_QUEUES)
    cache.delete(KEY_STATES)
  except Exception:
    pass


def invalidate_after_job_data_ingest(
  inserted_count: int,
  inserted_jids: Any | None = None,
) -> None:
  """
  Drop site freshness probe and home_options reference keys after new job_data.
  
    rows.
  
  When *inserted_jids* is provided, only expansion-factor dashboard artifacts
    for
  those jobs' calendar periods are marked for rebuild (see
  :func:`invalidate_public_metrics_artifacts_for_jids`). Otherwise falls back
  to marking every prewarmed /pub row stale — avoid that in hot paths where
  *inserted_jids* is knowable (e.g. accounting ingest).
  
  Args:
    inserted_count (int): Integer value for inserted count.
    inserted_jids (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_after_job_data_ingest(0, None)  # doctest: +SKIP
  """
  if inserted_count <= 0:
    return
  invalidate_home_options_query_cache()
  try:
    from hpcperfstats.site.lib.machine.public_metrics_artifacts import (
        invalidate_all_public_metrics_artifacts,
        invalidate_public_metrics_artifacts_for_jids,
    )

    if inserted_jids:
      invalidate_public_metrics_artifacts_for_jids(inserted_jids)
    else:
      invalidate_all_public_metrics_artifacts()
  except Exception:
    pass


def make_job_detail_cache_key(jid: Any) -> Any:
  """
  Redis key for cached job_data rows used by job detail (versioned for prefetch.
  
    shape).
  
  Args:
    jid (Any): Jid passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> make_job_detail_cache_key(None)  # doctest: +SKIP
  """
  return f"{KEY_JOB}:{KEY_JOB_CACHE_VERSION}:{jid}"


def ensure_job_metrics_data_prefetched(job: Any) -> Any:
  """
  Always refresh metrics_data_set from DB (Redis may pickle a stale prefetch).
  
  Django can pickle ``_prefetched_objects_cache`` into KEY_JOB. Trusting that
  cache after metrics persist left Job Detail Resources (e.g. watt-hours) blank
  while ``metrics_data`` already had values. Drop any pickled prefetch and
  re-query so display lists match the live catalog.
  
  Args:
    job (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ensure_job_metrics_data_prefetched(None)  # doctest: +SKIP
  """
  if job is None:
    return job
  from hpcperfstats.site.lib.machine.models import job_data

  if not isinstance(job, job_data):
    return job
  prefetched = getattr(job, "_prefetched_objects_cache", None)
  if isinstance(prefetched, dict):
    prefetched.pop("metrics_data_set", None)
  prefetch_related_objects([job], "metrics_data_set")
  return job


def cached_non_staff_visible_accounts(username: Any, timeout: int) -> Any:
  """
  Distinct accounts for jobs owned by username (non-staff list visibility).
  
  Args:
    username (Any): Username passed to this helper.
    timeout (int): Integer value for timeout.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cached_non_staff_visible_accounts(None, 0)  # doctest: +SKIP
  """
  username = str(username or "").strip()
  if not username:
    return []
  from hpcperfstats.site.lib.machine.models import job_data

  return cached_orm(
      f"{KEY_NONSTAFF_ACCOUNTS}:{username}",
      timeout,
      lambda: list(
          job_data.objects.filter(username=username)
          .exclude(account__isnull=True)
          .exclude(account="")
          .values_list("account", flat=True)
          .distinct()
      ),
  )


def warm_job_cache_entries(job_instances: Any, timeout: int) -> None:
  """
  Seed versioned KEY_JOB cache rows from in-memory job_data (e.g. post.
  
    bulk_create).
  
  Args:
    job_instances (Any): Job instances passed to this helper.
    timeout (int): Integer value for timeout.
  
  Returns:
    None
  
  Examples:
    >>> warm_job_cache_entries(None, 0)  # doctest: +SKIP
  """
  if not job_instances:
    return
  try:
    prefetch_related_objects(job_instances, "metrics_data_set")
    for obj in job_instances:
      jid = getattr(obj, "jid", None)
      if jid:
        cache.set(make_job_detail_cache_key(jid), obj, timeout=timeout)
  except Exception:
    pass


def invalidate_jid_derived_cache_keys(jids: Any) -> None:
  """
  Remove per-job aggregate caches after host_data / proc_data ingest.
  
  Args:
    jids (Any): Jids passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_jid_derived_cache_keys(None)  # doctest: +SKIP
  """
  if not jids:
    return
  invalidate_jid_host_window_row_count_cache(jids)
  try:
    from hpcperfstats.site.lib.machine.models import job_data as _job_data_model

    _job_data_model.objects.filter(
        jid__in=[j for j in jids if j],
    ).update(host_data_schema_json=None)
  except Exception:
    pass
  try:
    for jid in jids:
      if not jid:
        continue
      cache.delete(make_cache_key(KEY_JOB_JID_TABLE_WINDOW, jid))
      cache.delete(f"{KEY_GPU_AGG}:v3:{jid}")
      cache.delete(f"{KEY_GPU_COUNT}:{jid}")
      cache.delete(f"{KEY_XALT}:{jid}")
      cache.delete(f"{KEY_PROC_LIST}:{jid}")
  except Exception:
    pass


def _get_redis_py_client() -> Any:
  """
  Best-effort redis-py client from Django's default cache (for SCAN).
  
  Returns:
    Any: Open return polymorphism from ``_get_redis_py_client``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _get_redis_py_client()  # doctest: +SKIP
  """
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


def invalidate_job_plot_cache_keys_for_jids(jids: Any) -> None:
  """
  Delete JOB_PLOTS_JSON / JOB_PLOTS_DATA Redis keys for the given jids (SCAN).
  
  Args:
    jids (Any): Jids passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_job_plot_cache_keys_for_jids(None)  # doctest: +SKIP
  """
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
    from hpcperfstats.site.lib.machine.models import job_detail_artifact, job_plot_artifact

    job_plot_artifact.objects.filter(jid_id__in=[j for j in jids if j]).delete()
    job_detail_artifact.objects.filter(jid_id__in=[j for j in jids if j]).delete()
  except Exception:
    pass
  try:
    from hpcperfstats.site.lib.machine.public_metrics_artifacts import (
        invalidate_public_metrics_artifacts_for_jids,
    )

    invalidate_public_metrics_artifacts_for_jids(jids)
  except Exception:
    pass


def invalidate_metrics_distinct_cache() -> None:
  """
  Clear distinct metrics list after metrics_data writes.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_metrics_distinct_cache()  # doctest: +SKIP
  """
  try:
    cache.delete(KEY_METRICS_DISTINCT)
  except Exception:
    pass


# Key prefixes for namespacing
KEY_JOB = "job"
# Bumped when cached job_data shape/relations change (e.g. metrics prefetch on read).
KEY_JOB_CACHE_VERSION = "v2"
KEY_NONSTAFF_ACCOUNTS = "nonstaff_accounts_v1"
# Pickle-safe (host_list, start_time, end_time) row for :class:`jid_table` only;
# do not reuse ``KEY_JOB`` for this — that key holds full ``job_data`` instances
# for the job detail API and ingest warmers.
KEY_JOB_JID_TABLE_WINDOW = "job_jid_table_win"
KEY_JOB_HOST_LIST = "job_host_list"
KEY_JOB_SCHEMA = "job_schema"
# Bumped when home_options metrics list shape changes (OpenAPI requires type/metric/units).
KEY_METRICS_DISTINCT = "metrics_distinct_v2"
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


def make_cache_key(prefix: str, *parts: Any) -> str:
  """
  Build a cache key from a prefix and optional parts joined by ':'.
  
  Args:
    prefix (str): String for prefix.
    *parts (Any): Extra positional values for ``parts``; element types match
    the helper's documented protocol.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> make_cache_key("x")  # doctest: +SKIP
  """
  if not parts:
    return prefix
  return ":".join([prefix] + [str(p) for p in parts])


def make_cache_key_bounded(
  prefix: str,
  *parts: Any,
  max_piece_len: int = 56,
  digest_len: int = 40,
) -> str:
  """
  Like ``make_cache_key`` but replace overly long *parts* with a SHA-256 prefix.
  
  Keeps keys under typical Memcached 250-byte limits when *parts* include long
  event-name lists (e.g. aggregate DataFrame cache keys).
  
  Args:
    prefix (str): String for prefix.
    *parts (Any): Extra positional values for ``parts``; element types match
    the helper's documented protocol.
    max_piece_len (int): Integer value for max piece len.
    digest_len (int): Integer value for digest len.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> make_cache_key_bounded("x", 0, 0)  # doctest: +SKIP
  """
  pieces = [prefix]
  for p in parts:
    s = str(p)
    if len(s) > max_piece_len:
      s = hashlib.sha256(s.encode("utf-8")).hexdigest()[:digest_len]
    pieces.append(s)
  return ":".join(pieces)


def register_job_plot_cache_key(jid: Any, cache_key: Any) -> None:
  """
  Track per-jid plot cache keys to avoid expensive wildcard scans.
  
  Args:
    jid (Any): Jid passed to this helper.
    cache_key (Any): Cache key passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> register_job_plot_cache_key(None, None)  # doctest: +SKIP
  """
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


def invalidate_jid_host_window_row_count_cache(jids: Any) -> None:
  """
  Drop cached window row counts for ``jid_table`` large-job gating.
  
  Args:
    jids (Any): Jids passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_jid_host_window_row_count_cache(None)  # doctest: +SKIP
  """
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
