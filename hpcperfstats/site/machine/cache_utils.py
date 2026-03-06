"""Memcached-backed caching for Django ORM query results.

Use cached_orm() to wrap any callable that performs a read and returns
a cacheable value (e.g. list of dicts, model instance, DataFrame-serializable).
Cache keys should be unique per query; timeouts are in seconds.
"""
from django.core.cache import cache

# Sentinel so we can cache None (e.g. "job not found")
_CACHE_MISS = object()


def cached_orm(cache_key, timeout, query_fn):
    """Execute query_fn() on cache miss; return cached value on hit.

    query_fn is a callable that takes no arguments and returns the value to cache.
    The value must be picklable (e.g. list of dicts from .values(), or None).
    None is stored as a wrapped tuple so we can distinguish "missing key" from "cached None".
    If the cache backend is unavailable (e.g. memcached down), query_fn() is used and the result is not cached.
    """
    try:
        wrapped = cache.get(cache_key, _CACHE_MISS)
        if wrapped is not _CACHE_MISS:
            return (
                wrapped[0]
                if isinstance(wrapped, tuple) and len(wrapped) == 1
                else wrapped
            )
        value = query_fn()
        cache.set(
            cache_key, (value,) if value is None else value, timeout=timeout
        )
        return value
    except Exception:
        return query_fn()


# Default timeouts (seconds)
TIMEOUT_SHORT = 60       # Job-specific, host-specific (1 min)
TIMEOUT_MEDIUM = 300     # Reference data: queues, states, date list (5 min)
TIMEOUT_LONG = 600       # Rarely changing: distinct metrics list (10 min)

# Key prefixes for namespacing
KEY_JOB = "job"
KEY_JOB_HOST_LIST = "job_host_list"
KEY_JOB_SCHEMA = "job_schema"
KEY_METRICS_DISTINCT = "metrics_distinct"
KEY_DATES = "dates"
KEY_QUEUES = "queues"
KEY_STATES = "states"
KEY_ALL_HOSTS = "all_hosts"
KEY_HOST_LAST = "host_last"
KEY_LLITE_DELTA = "llite_delta"
KEY_GPU_QS = "gpu_qs"
KEY_XALT = "xalt"
KEY_TYPE_DETAIL_HOSTS = "type_detail_hosts"
KEY_HOST_DATA_DF = "host_data_df"
KEY_AGG_DF = "agg_df"
KEY_HOST_TIME_DF = "host_time_df"
KEY_UPDATE_METRICS_JOBS = "update_metrics_jobs"
