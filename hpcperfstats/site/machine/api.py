"""Django REST Framework API views for machine app. All data via JSON for React SPA."""
import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from functools import partial
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone as dt_timezone

import hpcperfstats.conf_parser as cfg
from bokeh.embed import json_item
from django.utils import timezone
from pandas import DataFrame
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django.conf import settings
from django.core.cache import cache
from django.db import connection, close_old_connections, transaction
from django.test import RequestFactory
from django.utils.cache import (
    _generate_cache_header_key,
    _generate_cache_key,
    get_cache_key,
)
import os
from types import SimpleNamespace

logger = logging.getLogger(__name__)


class _JSONResponse(Response):
    """Response subclass with a json() helper for unit tests.

    Django's test client adds a json() method on response objects, but when
    calling views directly (as these tests do) we get the raw DRF Response.
    Providing json() keeps tests readable without changing production behavior.
    """

    def json(self):
        return self.data

from .bokeh_plot_layout import _apply_zoom_layout_to_json_item
from .bokeh_embed import new_spa_embedded_figure
from .cache_middleware import dynamic_cache_page
from .cache_utils import (
    KEY_ADMIN_CACHE_STATS,
    KEY_ADMIN_RMQ_STATS,
    KEY_ADMIN_RMQ_SNAPSHOT,
    KEY_ADMIN_TIMESCALE_STATS,
    KEY_ADMIN_HOST_STATS,
    KEY_ADMIN_XALT_STATS,
    KEY_DATES,
    KEY_METRICS_DISTINCT,
    KEY_QUEUES,
    KEY_STATES,
    KEY_GPU_AGG,
    KEY_GPU_COUNT,
    KEY_XALT,
    KEY_JOB,
    KEY_JOB_SEARCH_JID,
    KEY_PROC_LIST,
    KEY_HOST_PLOT,
    cached_orm,
    get_site_content_cache_timeout,
    invalidate_home_options_query_cache,
    make_cache_key,
    register_job_plot_cache_key,
    TIMEOUT_ADMIN_STATS,
)
from .job_plot_artifacts import (
    JOB_PLOT_JSON_KEYS,
    JOB_PLOT_KIND_SPECS,
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_NORMAL,
    JOB_PLOT_LAYOUT_ZOOM_V3,
    compute_plot_item_for_kind,
    compute_plot_input_fingerprint,
    get_live_distinct_time_count_for_jid,
    load_cached_job_plot_entry,
)
from .job_detail_artifacts import (
    ARTIFACT_KIND_JOB_DETAIL,
    ARTIFACT_KIND_TYPE_DETAIL,
    compute_detail_input_fingerprint,
    load_job_detail_artifact,
)
from hpcperfstats.analysis.metrics.metrics import build_job_metrics_display_list
from hpcperfstats.dbload.sync_acct import sync_acct_from_content
from .models import ApiKey, host_data, job_data, metrics_data
from .oauth2 import check_for_tokens
from .query_utils import (
    expand_month_date_to_range,
    get_job_list_order_by,
    normalize_job_list_query_params,
    partition_job_list_acct_filters,
)
from .job_list_performance import annotate_job_list_performance_fields
from .job_list_queue_wait import aggregate_queue_wait_seconds_stats, queue_wait_hours_series
from .serializers import JobListSerializer
from .views import (
    job_hist,
    local_timezone,
    libset_c,
    xalt_data_c,
)

# Shared thread pools (capped total threads per process).
_small_executor = None   # dashboard, queue histograms, job_detail, job_plots (≤8 tasks)
_job_plots_lock = threading.Lock()

_job_plot_inflight = {}
_JOB_PLOT_INFLIGHT_TTL_SECONDS = 180


def site_response_cache_timeout(request):
    """Per-request TTL for @dynamic_cache_page (site-aware)."""
    return get_site_content_cache_timeout()

_JOB_LIST_QUERY_FIELD_EXCLUDES_BASE = ("page", "order_by")
_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM = ("group", "metric", "_histogram_embed_v")


def _get_admin_host_stats_statement_timeout_ms():
    """Statement timeout for heavy admin host-stats query only."""
    try:
        default_ms = int(cfg.get_db_statement_timeout_ms())
    except Exception:
        default_ms = 120000
    return max(default_ms, 600000)


@contextmanager
def _pg_session_statement_timeout_for_admin_host_stats_query():
    """Increase statement timeout only while evaluating admin host stats query."""
    if connection.vendor != "postgresql":
        yield
        return

    timeout_ms = _get_admin_host_stats_statement_timeout_ms()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
        yield


def _collect_future_results_with_deadline(future_to_key, max_wait_seconds):
    """
    Collect completed future results until `max_wait_seconds` elapses.

    Returns:
      (results_by_key, remaining_keys)

    `results_by_key` contains only keys whose futures completed within the
    deadline and did not raise.
    """
    results_by_key = {}
    remaining_keys = set(future_to_key.values())

    try:
        for future in as_completed(future_to_key, timeout=max_wait_seconds):
            key = future_to_key[future]
            remaining_keys.discard(key)
            try:
                results_by_key[key] = future.result()
            except Exception:
                # Best-effort: if a task fails, job_detail should still render.
                pass
    except FuturesTimeoutError:
        # Deadline exceeded; we'll return partial results for completed tasks.
        pass

    # Best-effort cancellation for futures that haven't finished yet.
    for future, key in future_to_key.items():
        if key in remaining_keys and not future.done():
            future.cancel()

    return results_by_key, remaining_keys


def _evict_stale_inflight_plot_tasks():
    """Remove stale in-flight plot tasks to bound map growth.

    Do not evict entries solely because ``future.done()`` is true: ``job_plots``
    must run ``_finalize_job_plot_future`` first to persist results to the cache.
    Otherwise a plot that finishes after the previous request returns 202 can be
    dropped before the client polls again.
    """
    now = time.monotonic()
    stale_keys = []
    for inflight_key, inflight_meta in list(_job_plot_inflight.items()):
        future = inflight_meta.get("future")
        created_at = float(inflight_meta.get("created_at", 0.0))
        if future is None or (now - created_at) > _JOB_PLOT_INFLIGHT_TTL_SECONDS:
            if future is not None and not future.done():
                future.cancel()
            stale_keys.append(inflight_key)
    for inflight_key in stale_keys:
        _job_plot_inflight.pop(inflight_key, None)

def _get_small_executor():
    global _small_executor
    if _small_executor is None:
        _small_executor = ThreadPoolExecutor(
            max_workers=cfg.get_api_small_executor_max_workers()
        )
    return _small_executor


def _gpu_agg_rows_for_job(j):
    """host_data GPU util rows for job window; delegates to metrics GPU helper."""
    from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
        gpu_agg_rows_for_job_window,
    )

    return gpu_agg_rows_for_job_window(j)


_DETAIL_GPU_METRIC_NAMES = (
    "detail_gpu_active",
    "detail_gpu_util_max",
    "detail_gpu_util_mean",
    "detail_gpu_count",
)


def _gpu_detail_tuple_from_metrics(job):
    """Return (active, max%, mean%, count) from metrics_data if all four rows exist with values.

    Jobs not yet processed by update_metrics fall back to host_data in _fetch_gpu.
    """
    by_m = {o.metric: o for o in job.metrics_data_set.all()}
    rows = []
    for name in _DETAIL_GPU_METRIC_NAMES:
        r = by_m.get(name)
        if r is None or r.value is None:
            return None
        rows.append(r)
    try:
        return (
            int(round(float(rows[0].value))),
            float(rows[1].value),
            float(rows[2].value),
            int(round(float(rows[3].value))),
        )
    except (TypeError, ValueError):
        return None


def _fsio_dict_from_metrics(job):
    """Return job_detail-shaped fsio dict from metrics_data, or None if incomplete."""
    by_m = {o.metric: o for o in job.metrics_data_set.all()}
    lr = by_m.get("detail_fsio_llite_read_mb")
    lw = by_m.get("detail_fsio_llite_write_mb")
    if (
        lr is not None
        and lw is not None
        and lr.value is not None
        and lw.value is not None
    ):
        return {"llite": [float(lr.value), float(lw.value)]}
    nr = by_m.get("detail_fsio_nfs_read_mb")
    nw = by_m.get("detail_fsio_nfs_write_mb")
    if (
        nr is not None
        and nw is not None
        and nr.value is not None
        and nw.value is not None
    ):
        return {"nfs": [float(nr.value), float(nw.value)]}
    return None


def _compute_job_gpu_stats(job, j, job_cache_timeout, include_gpu_count=True):
    """Compute per-job GPU stats (host_data); used when metrics_data rows are missing."""
    from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
        gpu_count_total_for_job_window,
        reduce_gpu_agg_to_util_stats,
    )

    gpu_active, gpu_max, gpu_mean, gpu_count_total = None, None, None, None
    close_old_connections()
    try:
        try:
            agg = cached_orm(
                f"{KEY_GPU_AGG}:v3:{job.jid}",
                job_cache_timeout,
                lambda: list(_gpu_agg_rows_for_job(j)),
            )
            if isinstance(agg, dict):
                agg = [agg]
            gpu_active, gpu_max, gpu_mean = reduce_gpu_agg_to_util_stats(agg)
        except Exception:
            pass

        if include_gpu_count:
            try:
                gpu_count_total = cached_orm(
                    f"{KEY_GPU_COUNT}:{job.jid}",
                    job_cache_timeout,
                    lambda: gpu_count_total_for_job_window(j),
                )
            except Exception:
                gpu_count_total = None

        return (gpu_active, gpu_max, gpu_mean, gpu_count_total)
    finally:
        close_old_connections()


from django.db.models import (
    Case,
    Count,
    IntegerField,
    Sum,
    Q,
    F,
    FloatField,
    When,
    ExpressionWrapper,
    Max,
)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

import hpcperfstats.analysis.gen.jid_table as jid_table
from hpcperfstats.analysis.gen.jid_table import HostDataProvider
import hpcperfstats.analysis.plot as plots
from hpcperfstats.site.xalt.models import join_run_object, lib, run


def _age_bucket(age: timedelta) -> str:
    """Map last-seen age to admin monitor bucket labels (Redis + DB host stats)."""
    thresholds = (
        (timedelta(weeks=1), "gt_week"),
        (timedelta(days=1), "gt_day"),
        (timedelta(hours=1), "gt_hour"),
        (timedelta(minutes=10), "gt_10min"),
    )
    for td, label in thresholds:
        if age > td:
            return label
    return "ok"


def _admin_monitor_host_stat_dict(host, last_time, now):
    """Build one ``host_stats`` row for admin monitor, or ``None`` to skip this host."""
    host = host or ""
    if not host or last_time is None or "." not in host:
        return None
    age = now - last_time
    return {
        "host": host,
        "last_time": last_time.isoformat() if last_time else None,
        "age_bucket": _age_bucket(age),
    }


def _format_log_timestamp(ts):
    """
    Format a datetime for client/server log URLs.

    Desired format: %Y-%m-%dT%H:%M:%S%:z
    Python's strftime does not support %:z directly, so we build the offset
    manually while preserving any existing tzinfo.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            # Assume UTC for naive datetimes so that logs remain unambiguous.
            ts = ts.replace(tzinfo=dt_timezone.utc)
        base = ts.strftime("%Y-%m-%dT%H:%M:%S")
        offset = ts.strftime("%z") or "+0000"
        if len(offset) == 5:
            offset = f"{offset[:3]}:{offset[3:]}"
        return f"{base}{offset}"
    return str(ts)


def _get_api_key_from_request(request):
    """Extract API key from Authorization or X-API-Key headers.

    Supported formats:
    - Authorization: Api-Key <key>
    - X-API-Key header
    """
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth:
        parts = auth.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "api-key":
            return parts[1].strip() or None
    header_key = request.META.get("HTTP_X_API_KEY") or request.headers.get(
        "X-API-Key"
    )
    if header_key:
        return header_key.strip() or None
    return None


def _api_key_valid(key: str):
    """Return ApiKey instance if key is valid and active, else None."""
    if not key:
        return None
    key_hash = ApiKey.hash_raw_key(key)
    try:
        api_key_obj = ApiKey.objects.get(key=key_hash, is_active=True)
    except ApiKey.DoesNotExist:
        return None
    # Best-effort last-used update; ignore errors.
    try:
        api_key_obj.last_used_at = timezone.now()
        api_key_obj.save(update_fields=["last_used_at"])
    except Exception:
        pass
    return api_key_obj


def _require_auth(request):
    """Return 401 JSON if not authenticated via OAuth2 session or API key."""
    if check_for_tokens(request):
        return None

    api_key = _get_api_key_from_request(request)
    api_key_obj = _api_key_valid(api_key)
    if api_key_obj is not None:
        # Associate valid API-key clients with the username that created the key.
        session = request.session
        session["username"] = api_key_obj.username
        # For API keys, trust the is_staff flag stored on the key itself so that
        # staff vs non-staff behavior is stable even outside of an OAuth session.
        session["is_staff"] = bool(getattr(api_key_obj, "is_staff", False))
        session.setdefault("access_token", f"api-key:{api_key_obj.username}")
        return None

    return Response(
        {"detail": "Authentication required", "login_url": "/login_prompt"},
        status=status.HTTP_401_UNAUTHORIZED,
    )


def _require_staff(request):
    """Return 401/403 Response if not authenticated or not staff; else None."""
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _apply_non_staff_job_visibility(queryset, request):
    """Restrict non-staff visibility to own jobs and jobs in own-used accounts."""
    session = getattr(request, "session", None)
    if session is None:
        raw_req = getattr(request, "_request", None)
        session = getattr(raw_req, "session", None)
    if session is None:
        # Keep behavior unchanged for call-sites/tests that do not attach session.
        return queryset

    if session.get("is_staff", False):
        return queryset

    username = str(session.get("username") or "").strip()
    if not username:
        return queryset.none()

    account_list = list(
        job_data.objects.filter(username=username)
        .exclude(account__isnull=True)
        .exclude(account="")
        .values_list("account", flat=True)
        .distinct()
    )
    if account_list:
        return queryset.filter(Q(username=username) | Q(account__in=account_list))
    return queryset.filter(username=username)


def _get_visible_job_or_error_response(request, pk, queryset_builder):
    """Return (job, None) if visible, else (None, Response)."""
    site_ttl = get_site_content_cache_timeout()
    job = cached_orm(
        f"{KEY_JOB}:{pk}",
        site_ttl,
        queryset_builder,
    )
    if not job:
        return None, Response(
            {"error": "Job not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not _apply_non_staff_job_visibility(job_data.objects.filter(jid=pk), request).exists():
        return None, Response(
            {"error": "Not allowed to view this job"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return job, None


def _job_times_as_local(start_time, end_time):
    """Return start/end as timezone-aware datetimes in local timezone."""
    if start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time, dt_timezone.utc)
    if end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time, dt_timezone.utc)
    return start_time.astimezone(local_timezone), end_time.astimezone(local_timezone)


def _build_job_list_queryset_from_request(request, extra_excluded_fields=(), annotate_all=False):
    """Build filtered ordered queryset and parsed filter maps for job list endpoints."""
    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)
    excluded_fields = set(_JOB_LIST_QUERY_FIELD_EXCLUDES_BASE) | set(extra_excluded_fields)
    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics" and k not in excluded_fields
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    acct_kwargs, host_val = partition_job_list_acct_filters(acct_data)
    queryset = job_data.objects.filter(**acct_kwargs)
    if host_val:
        queryset = queryset.filter(host_list__contains=[host_val])
    queryset = _apply_non_staff_job_visibility(queryset, request)
    if annotate_all or order_by.lstrip("-") == "performance_sort_rank":
        queryset = annotate_job_list_performance_fields(queryset)
    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.split("_", 1)[0] == "metrics"
    }
    for key, val in cur_metrics.items():
        if "__" not in key:
            logger.warning("Ignoring malformed metrics filter key %r", key)
            continue
        name, op = key.split("__", 1)
        if not name or not op:
            logger.warning("Ignoring malformed metrics filter key %r", key)
            continue
        queryset = queryset.filter(
            **{
                "metrics_data__metric": name,
                "metrics_data__value__" + op: val,
            }
        )
    queryset = queryset.order_by(order_by)
    return queryset, fields, cur_metrics, order_by


def _get_redis_cache_client():
    """Best-effort unwrap of a redis-py client from Django's cache backend."""
    client = getattr(cache, "_cache", None)
    if hasattr(client, "get_client"):
        try:
            client = client.get_client()
        except Exception:
            client = None
    if client is None:
        client = getattr(cache, "client", None)
        if hasattr(client, "get_client"):
            try:
                client = client.get_client()
            except Exception:
                client = None
    return client


def _full_page_cache_url_digests_for_request_paths(request, paths):
    """Return MD5 hex digests of absolute URIs used in Django ``cache_page`` keys.

    Matches ``django.utils.cache._generate_cache_key`` / ``get_cache_key`` URL
    hashing (``md5(request.build_absolute_uri().encode("ascii"))``).
    """
    factory = RequestFactory()
    host = request.get_host() or "localhost"
    digests = set()
    for raw_path in paths:
        path = raw_path if (raw_path or "").startswith("/") else f"/{raw_path}"
        for secure in (False, True):
            req = factory.get(path, HTTP_HOST=host, secure=secure)
            uri = req.build_absolute_uri()
            digests.add(
                hashlib.md5(uri.encode("ascii"), usedforsecurity=False).hexdigest()
            )
    return digests


def _delete_django_cache_page_entries_for_request(request, paths):
    """Delete ``@cache_page`` / ``dynamic_cache_page`` entries via Django's cache API.

    Drops the ``cache_header`` registry entry, then either the ``get_cache_key`` page
    key or—when the registry is missing—the empty-``Vary`` page key Django would use.
    """
    factory = RequestFactory()
    host = request.get_host() or "localhost"
    deleted = 0
    key_prefix = settings.CACHE_MIDDLEWARE_KEY_PREFIX
    for raw_path in paths:
        path = raw_path if (raw_path or "").startswith("/") else f"/{raw_path}"
        for secure in (False, True):
            req = factory.get(path, HTTP_HOST=host, secure=secure)
            hk = _generate_cache_header_key(key_prefix, req)
            if cache.delete(hk):
                deleted += 1
            ck = get_cache_key(req)
            if ck is not None:
                if cache.delete(ck):
                    deleted += 1
            else:
                nk = _generate_cache_key(req, "GET", [], key_prefix)
                if cache.delete(nk):
                    deleted += 1
    return deleted


def _redis_delete_cache_page_keys_matching_digests(client, digests):
    """Delete raw Redis keys for ``cache_page`` / ``cache_header`` rows matching URL digests.

    Needed when responses ``Vary`` on headers (e.g. ``Cookie``): each variant has
    a distinct full key, so ``get_cache_key`` for a single synthetic request is
    not enough. Keys still embed the URL MD5 hex from Django's cache layer.
    """
    if not digests or client is None or not hasattr(client, "scan_iter"):
        return 0
    deleted = 0
    for d in digests:
        iterator = None
        try:
            iterator = client.scan_iter(match=f"*{d}*", count=500)
        except TypeError:
            iterator = client.scan_iter(count=500)
        try:
            for raw_key in iterator:
                key_str = (
                    raw_key.decode("utf-8", "replace")
                    if isinstance(raw_key, bytes)
                    else str(raw_key)
                )
                if d not in key_str:
                    continue
                if (
                    "views.decorators.cache.cache_page" not in key_str
                    and "views.decorators.cache.cache_header" not in key_str
                ):
                    continue
                try:
                    deleted += int(client.delete(raw_key) or 0)
                except Exception:
                    continue
        except Exception:
            continue
    return deleted


def _get_cache_stats():
    """Return basic Redis/cache statistics for the HPCPerfStats Monitor."""
    # First try to return a recently cached snapshot of the Redis stats so that
    # repeated HPCPerfStats Monitor polls do not issue heavy INFO/SCAN calls.
    try:
        cached_stats = cache.get(KEY_ADMIN_CACHE_STATS)
        if isinstance(cached_stats, dict):
            return cached_stats
    except Exception:
        cached_stats = None

    stats = {}
    try:
        default_cache_cfg = (getattr(settings, "CACHES", {}) or {}).get("default", {})
        if default_cache_cfg:
            stats["location"] = default_cache_cfg.get("LOCATION")
            stats["default_timeout"] = default_cache_cfg.get("TIMEOUT")

        # Try to unwrap the real Redis client from Django's cache backend.
        # Django's built-in Redis cache exposes a RedisCacheClient instance on
        # _cache, which must be further unwrapped via get_client() to get the
        # actual redis-py client that implements .info(), .scan_iter(), etc.
        client = _get_redis_cache_client()

        if client is not None and hasattr(client, "info"):
            info = client.info()
            stats["redis_version"] = info.get("redis_version")
            stats["connected_clients"] = info.get("connected_clients")
            stats["uptime_in_seconds"] = info.get("uptime_in_seconds")

            # Total data cached (memory used by Redis).
            total_bytes = info.get("used_memory")
            if total_bytes is not None:
                stats["total_data_cached_bytes"] = total_bytes
            used_memory_human = info.get("used_memory_human")
            if used_memory_human is not None:
                stats["total_data_cached_human"] = used_memory_human

            # Total cache usable (configured Redis memory limit).
            maxmemory_bytes = info.get("maxmemory")
            if maxmemory_bytes is not None:
                stats["total_cache_usable_bytes"] = maxmemory_bytes
            maxmemory_human = info.get("maxmemory_human")
            if maxmemory_human is not None:
                stats["total_cache_usable_human"] = maxmemory_human

            # Cache hit/miss counters.
            hits = info.get("keyspace_hits")
            misses = info.get("keyspace_misses")
            if hits is not None:
                stats["cache_hits"] = hits
            if misses is not None:
                stats["cache_misses"] = misses

            db0 = info.get("db0") or {}
            keys = None
            if isinstance(db0, dict):
                keys = db0.get("keys")
            elif isinstance(db0, str):
                try:
                    parts = dict(
                        part.split("=", 1) for part in db0.split(",") if "=" in part
                    )
                    if "keys" in parts:
                        keys = int(parts["keys"])
                except Exception:
                    keys = None
            if keys is not None:
                stats["db0_keys"] = keys

            # Attempt to identify the most memory-heavy cached keys.
            # This uses SCAN and MEMORY USAGE to avoid blocking Redis too long.
            try:
                top_keys = []
                total_sampled_bytes = 0
                # Limit to a reasonable number of sampled keys to keep this light.
                max_sample = 500
                scanned = 0
                for key in client.scan_iter(count=max_sample):
                    if scanned >= max_sample:
                        break
                    scanned += 1
                    try:
                        size = client.memory_usage(key) or 0
                    except Exception:
                        size = 0
                    total_sampled_bytes += size
                    if isinstance(key, bytes):
                        key_str = key.decode("utf-8", "replace")
                    else:
                        key_str = str(key)
                    top_keys.append((key_str, size))

                if top_keys:
                    top_keys.sort(key=lambda kv: kv[1], reverse=True)
                    stats["most_used_cached_keys"] = [
                        {"key": k, "approx_size_bytes": v} for k, v in top_keys[:10]
                    ]
                    stats["total_data_cached_bytes_sampled"] = total_sampled_bytes
            except Exception:
                # If anything goes wrong while probing individual keys, just skip this
                # detailed per-key section and fall back to the top-level memory stats.
                pass
    except Exception:
        # If anything goes wrong (e.g., Redis down), return whatever we have.
        pass

    # Best-effort cache of the freshly gathered stats; if this fails we still
    # return the live snapshot.
    try:
        cache.set(KEY_ADMIN_CACHE_STATS, stats, timeout=TIMEOUT_ADMIN_STATS)
    except Exception:
        pass

    return stats


def _get_timescaledb_stats():
    """Return basic TimescaleDB/PostgreSQL statistics for the HPCPerfStats Monitor."""
    try:
        cached_stats = cache.get(KEY_ADMIN_TIMESCALE_STATS)
        if isinstance(cached_stats, dict):
            return cached_stats
    except Exception:
        cached_stats = None

    stats = {}

    try:
        with connection.cursor() as cur:
            # Basic database/server info.
            try:
                cur.execute("SELECT current_database(), version()")
                row = cur.fetchone()
                if row:
                    stats["database_name"] = row[0]
                    stats["server_version"] = row[1]
            except Exception:
                pass

            # TimescaleDB extension version, if installed.
            try:
                cur.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
                )
                row = cur.fetchone()
                if row:
                    stats["timescaledb_version"] = row[0]
            except Exception:
                pass

            # Hypertable and chunk counts (if TimescaleDB catalog is available).
            try:
                cur.execute("SELECT count(*) FROM timescaledb_information.hypertables")
                row = cur.fetchone()
                if row and row[0] is not None:
                    stats["hypertable_count"] = int(row[0])
            except Exception:
                pass

            try:
                cur.execute(
                    """
                    SELECT
                        count(*) AS total_chunks,
                        count(*) FILTER (
                            WHERE is_compressed
                        ) AS compressed_chunks
                    FROM timescaledb_information.chunks
                    """
                )
                row = cur.fetchone()
                if row:
                    total_chunks, compressed_chunks = row
                    if total_chunks is not None:
                        stats["chunk_count"] = int(total_chunks)
                    if compressed_chunks is not None:
                        stats["compressed_chunk_count"] = int(compressed_chunks)
            except Exception:
                pass

            # Aggregate approximate on-disk sizes for compressed vs uncompressed chunks.
            try:
                cur.execute(
                    """
                    WITH chunk_sizes AS (
                        SELECT
                            sum(
                                pg_total_relation_size(
                                    format('%I.%I', chunk_schema, chunk_name)
                                )
                            ) FILTER (
                                WHERE is_compressed
                            ) AS compressed_bytes,
                            sum(
                                pg_total_relation_size(
                                    format('%I.%I', chunk_schema, chunk_name)
                                )
                            ) FILTER (
                                WHERE NOT is_compressed
                                    OR is_compressed IS NULL
                            ) AS uncompressed_bytes
                        FROM timescaledb_information.chunks
                    )
                    SELECT
                        compressed_bytes,
                        uncompressed_bytes,
                        pg_size_pretty(compressed_bytes),
                        pg_size_pretty(uncompressed_bytes)
                    FROM chunk_sizes
                    """
                )
                row = cur.fetchone()
                if row:
                    (
                        compressed_bytes,
                        uncompressed_bytes,
                        compressed_pretty,
                        uncompressed_pretty,
                    ) = row
                    if compressed_bytes is not None:
                        stats["compressed_chunks_size_bytes"] = int(compressed_bytes)
                        if compressed_pretty is not None:
                            stats["compressed_chunks_size_pretty"] = compressed_pretty
                    if uncompressed_bytes is not None:
                        stats["uncompressed_chunks_size_bytes"] = int(uncompressed_bytes)
                        if uncompressed_pretty is not None:
                            stats["uncompressed_chunks_size_pretty"] = (
                                uncompressed_pretty
                            )
                        # Treat all currently uncompressed chunk data as "pending"
                        # compression for monitoring purposes.
                        stats["pending_compression_size_bytes"] = int(
                            uncompressed_bytes
                        )
                        if uncompressed_pretty is not None:
                            stats["pending_compression_size_pretty"] = (
                                uncompressed_pretty
                            )
            except Exception:
                pass

            # Approximate size and row count for the primary hypertable host_data.
            try:
                cur.execute(
                    """
                    SELECT
                        reltuples::bigint AS row_estimate,
                        pg_total_relation_size(c.oid) AS total_bytes,
                        pg_size_pretty(pg_total_relation_size(c.oid)) AS total_pretty
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = 'host_data'
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row:
                    row_estimate, total_bytes, total_pretty = row
                    if row_estimate is not None:
                        stats["host_data_row_estimate"] = int(row_estimate)
                    if total_bytes is not None:
                        stats["host_data_size_bytes"] = int(total_bytes)
                    if total_pretty is not None:
                        stats["host_data_size_pretty"] = total_pretty
            except Exception:
                pass
    except Exception:
        # If anything goes wrong at the connection level, just return what we have.
        pass

    try:
        cache.set(KEY_ADMIN_TIMESCALE_STATS, stats, timeout=TIMEOUT_ADMIN_STATS)
    except Exception:
        pass

    return stats

def _get_rabbitmq_stats():
    """Return basic RabbitMQ queue statistics for the HPCPerfStats Monitor.

    Uses the RabbitMQ Management HTTP API if available. The management base URL and
    credentials can be overridden via environment variables:
      - RABBITMQ_MANAGEMENT_URL (default: http://<rmq_server>:15672)
      - RABBITMQ_MANAGEMENT_USER (default: guest)
      - RABBITMQ_MANAGEMENT_PASSWORD (default: guest)

    The "messages in the last day" counter is approximated from deltas of cumulative
    publish counters between snapshots stored in the cache.
    """
    try:
        cached_stats = cache.get(KEY_ADMIN_RMQ_STATS)
        if isinstance(cached_stats, dict):
            return cached_stats
    except Exception:
        cached_stats = None

    stats = {}

    # Import requests lazily so that a missing dependency does not break startup.
    try:
        import requests  # type: ignore
    except Exception:
        return stats

    try:
        rmq_host = cfg.get_rmq_server()
        rmq_queue = cfg.get_rmq_queue()
    except Exception:
        return stats

    base_url = os.environ.get("RABBITMQ_MANAGEMENT_URL", f"http://{rmq_host}:15672")
    user = os.environ.get("RABBITMQ_MANAGEMENT_USER", "guest")
    password = os.environ.get("RABBITMQ_MANAGEMENT_PASSWORD", "guest")

    url = f"{base_url.rstrip('/')}/api/queues/%2F/{rmq_queue}"

    try:
        resp = requests.get(url, auth=(user, password), timeout=5)
    except Exception as e:
        stats["error"] = f"Failed to connect to RabbitMQ management API: {e}"
    else:
        if resp.status_code != 200:
            stats["error"] = f"RabbitMQ management API returned HTTP {resp.status_code}"
        else:
            try:
                data = resp.json()
            except Exception as e:
                stats["error"] = f"Failed to decode RabbitMQ management API response: {e}"
                data = {}

            stats["queue"] = rmq_queue
            stats["messages"] = data.get("messages")
            stats["messages_ready"] = data.get("messages_ready")
            stats["messages_unacknowledged"] = data.get("messages_unacknowledged")
            stats["consumers"] = data.get("consumers")

            # Approximate sizes in bytes (if the management plugin exposes them).
            stats["message_bytes"] = data.get("message_bytes")
            stats["message_bytes_ready"] = data.get("message_bytes_ready")
            stats["message_bytes_unacknowledged"] = data.get(
                "message_bytes_unacknowledged"
            )

            msg_stats = data.get("message_stats") or {}
            publish_total = msg_stats.get("publish")
            deliver_total = msg_stats.get("deliver_get")
            if publish_total is not None:
                stats["messages_published_total"] = publish_total
            if deliver_total is not None:
                stats["messages_delivered_total"] = deliver_total

            # Use cached snapshot of cumulative publish counter to approximate
            # messages published over the last interval and scale to ~24h.
            now = timezone.now()
            snapshot = None
            try:
                snapshot = cache.get(KEY_ADMIN_RMQ_SNAPSHOT)
            except Exception:
                snapshot = None

            if isinstance(snapshot, dict):
                ts = snapshot.get("timestamp")
                prev_publish = snapshot.get("publish")
                try:
                    if ts is not None and prev_publish is not None:
                        prev_time = datetime.fromisoformat(ts)
                        if prev_time.tzinfo is None:
                            prev_time = timezone.make_aware(prev_time, dt_timezone.utc)
                        delta = now - prev_time
                        hours = delta.total_seconds() / 3600.0
                        if hours > 0 and publish_total is not None:
                            since_snapshot = max(
                                0, int(publish_total - int(prev_publish))
                            )
                            stats["messages_published_since_snapshot"] = since_snapshot
                            stats["snapshot_hours"] = round(hours, 2)
                            # Scale to a 24h estimate based on the observed window.
                            rate_per_hour = since_snapshot / hours
                            stats["messages_published_last_24h_estimate"] = int(
                                rate_per_hour * 24.0
                            )
                except Exception:
                    # If anything goes wrong with the snapshot math, just skip the
                    # derived counters and fall back to cumulative totals.
                    pass

            # Store a fresh snapshot of the cumulative publish counter.
            try:
                cache.set(
                    KEY_ADMIN_RMQ_SNAPSHOT,
                    {
                        "timestamp": now.isoformat(),
                        "publish": publish_total,
                    },
                    timeout=2 * 24 * 3600,
                )
            except Exception:
                pass

    try:
        cache.set(KEY_ADMIN_RMQ_STATS, stats, timeout=TIMEOUT_ADMIN_STATS)
    except Exception:
        pass

    return stats


def _get_recent_rabbitmq_host_stats():
    """Return per-host last-seen timestamps sourced directly from Redis keys."""
    now = timezone.now()
    host_stats = []

    def _decode_key(raw_key):
        if isinstance(raw_key, bytes):
            return raw_key.decode("utf-8", "replace")
        return str(raw_key)

    try:
        client = getattr(cache, "_cache", None)
        if hasattr(client, "get_client"):
            try:
                client = client.get_client()
            except Exception:
                client = None
        if client is None:
            client = getattr(cache, "client", None)
            if hasattr(client, "get_client"):
                try:
                    client = client.get_client()
                except Exception:
                    client = None

        if client is not None and hasattr(client, "scan_iter"):
            for key in client.scan_iter(match="recent_host:*", count=1000):
                key_str = _decode_key(key)
                if not key_str.startswith("recent_host:"):
                    continue
                host = key_str.split("recent_host:", 1)[1]
                if not host or "." not in host:
                    continue
                try:
                    raw_val = client.get(key)
                except Exception:
                    continue
                if raw_val is None:
                    continue
                if isinstance(raw_val, bytes):
                    raw_val = raw_val.decode("utf-8", "replace")
                try:
                    ts_epoch = int(float(raw_val))
                    last_time = datetime.fromtimestamp(ts_epoch, tz=dt_timezone.utc)
                except (TypeError, ValueError, OSError):
                    continue

                row = _admin_monitor_host_stat_dict(host, last_time, now)
                if row is not None:
                    host_stats.append(row)
    except Exception:
        host_stats = []
    return host_stats


@api_view(["GET"])
def session_info(request):
    """Return current session state for SPA (logged_in, username, is_staff)."""
    err = _require_auth(request)
    if err is not None:
        return err
    return Response({
        "logged_in": True,
        "username": request.session.get("username", ""),
        "is_staff": request.session.get("is_staff", False),
        "machine_name": cfg.get_host_name_ext(),
    })


@api_view(["GET"])
def user_api_key_status(request):
    """Return API key visibility for the OAuth session (create key if none exists)."""
    if not check_for_tokens(request):
        return Response(
            {"detail": "Authentication required", "login_url": "/login_prompt"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    username = request.session.get("username") or "unknown"
    is_staff = bool(request.session.get("is_staff", False))
    key_obj = (
        ApiKey.objects.filter(username=username, is_active=True, is_staff=is_staff)
        .order_by("-created_at")
        .first()
    )
    raw_key = None
    if key_obj is None:
        key_obj, raw_key = ApiKey.create_from_raw_key(
            username=username,
            is_staff=is_staff,
        )
    return Response({
        "username": username,
        "raw_key": raw_key,
        "key_prefix": key_obj.key_prefix if key_obj else "",
    })


@api_view(["POST"])
def user_api_key_rotate(request):
    """Revoke active keys for this user and return a newly minted raw key."""
    if not check_for_tokens(request):
        return Response(
            {"detail": "Authentication required", "login_url": "/login_prompt"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    username = request.session.get("username") or "unknown"
    is_staff = bool(request.session.get("is_staff", False))
    ApiKey.objects.filter(username=username, is_active=True, is_staff=is_staff).update(
        is_active=False
    )
    key_obj, raw_key = ApiKey.create_from_raw_key(
        username=username,
        is_staff=is_staff,
    )
    return Response({
        "username": username,
        "raw_key": raw_key,
        "key_prefix": key_obj.key_prefix,
    })


@api_view(["POST"])
def drop_staff_for_session(request):
    """Remove staff access for the current authenticated session only."""
    err = _require_staff(request)
    if err is not None:
        return err

    request.session["is_staff"] = False
    if hasattr(request.session, "modified"):
        request.session.modified = True
    return Response(
        {
            "ok": True,
            "message": (
                "Staff access removed for this session. "
                "Log out and log back in to restore staff access."
            ),
            "is_staff": False,
        }
    )


@api_view(["POST"])
def invalidate_cache_for_page(request):
    """Invalidate cache entries associated with the provided page path.

    Deletes Django ``@cache_page`` / ``dynamic_cache_page`` rows via ``cache.delete``
    (correct key prefix/versioning), then raw Redis ``SCAN`` for keys whose names
    embed the same URL MD5 as Django's cache layer (covers ``Vary`` variants).

    For any ``/machine`` or ``/machine/...`` path, also targets ``/api/home/`` and
    drops ``home_options`` ORM cache keys (dates, metrics list, queues, states,
    site newest job end). Works without a raw Redis client (LocMem): ORM + Django
    cache deletes still run.
    """
    err = _require_staff(request)
    if err is not None:
        return err

    page_path = (request.data.get("page_path") or "").strip()
    if not page_path:
        return Response(
            {"error": "Missing page_path"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not page_path.startswith("/"):
        page_path = f"/{page_path}"

    normalized_path = page_path.rstrip("/") or "/"
    path_variants = {normalized_path}
    if normalized_path != "/":
        path_variants.add(f"{normalized_path}/")

    # Machine SPA browse pages load calendar/year options from GET /api/home/, which
    # uses its own @dynamic_cache_page entry plus cached_orm keys (dates, queues, …).
    # Invalidate those whenever any /machine path is purged so staff "invalidate this
    # page" refreshes the calendar data contract for the whole SPA shell.
    if normalized_path == "/machine" or normalized_path.startswith("/machine/"):
        path_variants.add("/api/home")
        path_variants.add("/api/home/")
        invalidate_home_options_query_cache()

    paths_sorted = sorted(path_variants)
    digests = _full_page_cache_url_digests_for_request_paths(request, paths_sorted)

    deleted_count = 0
    deleted_count += _delete_django_cache_page_entries_for_request(request, paths_sorted)

    client = _get_redis_cache_client()
    redis_deleted = 0
    scanned_count = 0
    matched_keys = []
    max_legacy_scan = 5000
    if client is not None and hasattr(client, "scan_iter"):
        redis_deleted += _redis_delete_cache_page_keys_matching_digests(client, digests)
        for raw_key in client.scan_iter(count=500):
            scanned_count += 1
            if scanned_count > max_legacy_scan:
                break
            key_str = (
                raw_key.decode("utf-8", "replace")
                if isinstance(raw_key, bytes)
                else str(raw_key)
            )
            has_path_match = any(pv in key_str for pv in path_variants)
            if not has_path_match:
                continue
            try:
                redis_deleted += int(client.delete(raw_key) or 0)
                if len(matched_keys) < 25:
                    matched_keys.append(key_str)
            except Exception:
                continue

    deleted_count += redis_deleted

    return Response(
        {
            "ok": True,
            "page_path": normalized_path,
            "deleted_keys": deleted_count,
            "scanned_keys": scanned_count,
            "matched_sample": matched_keys,
            "truncated_scan": scanned_count > max_legacy_scan,
        }
    )


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def home_options(request):
    """Return options for search form: date_list, metrics, queues, states, machine_name."""
    err = _require_auth(request)
    if err is not None:
        return err

    site_ttl = get_site_content_cache_timeout()

    def _dates_fn():
        return sorted(job_data.objects.dates("end_time", "day"))

    def _metrics_fn():
        return list(
            metrics_data.objects.distinct("metric").values("metric", "units")
        )

    def _queues_fn():
        return list(
            job_data.objects.distinct("queue").values_list("queue", flat=True)
        )

    def _states_fn():
        return list(
            job_data.objects.exclude(state__contains="CANCELLED by")
            .distinct("state")
            .values_list("state", flat=True)
        )

    executor = _get_small_executor()
    date_future = executor.submit(
        cached_orm, KEY_DATES, site_ttl, _dates_fn
    )
    metrics_future = executor.submit(
        cached_orm, KEY_METRICS_DISTINCT, site_ttl, _metrics_fn
    )
    queues_future = executor.submit(
        cached_orm, KEY_QUEUES, site_ttl, _queues_fn
    )
    states_future = executor.submit(
        cached_orm, KEY_STATES, site_ttl, _states_fn
    )
    date_list = date_future.result()
    metrics = metrics_future.result()
    queues = queues_future.result()
    states = states_future.result()

    month_dict = {}
    year_set = set()
    if date_list:
        for d in date_list:
            year_set.add(d.year)
            key = f"{d.year}-{d.month:02d}"  # YYYY-MM
            if key not in month_dict:
                month_dict[key] = []
            month_dict[key].append((str(d), str(d.day)))
    year_list = sorted(year_set, reverse=True)

    return Response({
        "machine_name": cfg.get_host_name_ext(),
        "year_list": year_list,
        "date_list": sorted(month_dict.items(), reverse=True),
        "metrics": list(metrics) if metrics else [],
        "queues": [q for q in (queues or []) if q],
        "states": [s for s in (states or []) if s],
    })


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def search_dispatch(request):
    """
    Dispatch search: if jid -> return redirect url; if host+times -> return host plot data; else return job list (index).
    """
    err = _require_auth(request)
    if err is not None:
        return err

    site_ttl = get_site_content_cache_timeout()

    if request.GET.get("jid"):
        jid = request.GET["jid"]
        base_qs = _apply_non_staff_job_visibility(job_data.objects.all(), request)
        job_jid = cached_orm(
            f"{KEY_JOB_SEARCH_JID}:{jid}",
            site_ttl,
            lambda: base_qs.filter(jid=jid).values_list("jid", flat=True).first(),
        )
        if job_jid:
            return Response({"redirect": f"/machine/job/{job_jid}/"})
        return Response(
            {"error": "No result found in search"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.GET.get("host"):
        # Redirect to SPA host plot page with query params
        host = request.GET.get("host", "").strip()
        if host:
            q = request.GET.copy()
            q.pop("host", None)
            query = q.urlencode()
            path = f"/machine/host/{host}/plot/"
            if query:
                path = f"{path}?{query}"
            return Response({"redirect": path})
        return job_list(request)

    return job_list(request)


# Display titles for built-in job list histogram columns (column name -> UI title)
JOB_HIST_DISPLAY_NAMES = {
    "runtime": "Number of jobs by cpu hours",
    "nhosts": "Number of jobs by number of nodes",
    "queue_wait": "Number of jobs by queue wait time",
}


def _build_histogram_queryset(request):
    """
    Build the base queryset and metric filters for histogram endpoints.

    Returns (job_list_qs, nj, fields, cur_metrics) where:
    - job_list_qs: filtered and ordered queryset
    - nj: count of jobs in queryset
    - fields: normalized/expanded query params dict
    - cur_metrics: dict of metric_name__op -> value (from query params)
    """
    try:
        job_list_qs, fields, cur_metrics, _ = _build_job_list_queryset_from_request(
            request,
            extra_excluded_fields=_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM,
            annotate_all=True,
        )
        nj = job_list_qs.count()
        return job_list_qs, nj, fields, cur_metrics
    except Exception:
        # Database unavailable or other error: behave as if no jobs matched.
        return job_data.objects.none(), 0, {}, {}


def _build_histogram_dataframe(job_list_qs, cur_metrics):
    """
    Build the DataFrame and histogram metric list used for metric-based histograms.

    Returns (df, hist_metrics, jids_ordered) where:
    - df: pandas DataFrame indexed by jid with metric columns + runtime/nhosts/queue_wait
    - hist_metrics: list of (metric_name, units_label)
    - jids_ordered: list of jids in deterministic order
    """
    acc_cols = ["jid", "start_time", "submit_time", "runtime", "nhosts"]
    job_rows = list(job_list_qs.values(*acc_cols))
    jids_ordered = [r["jid"] for r in job_rows]
    job_df = DataFrame(job_rows).set_index("jid")

    metrics_rows = list(
        metrics_data.objects.filter(jid_id__in=jids_ordered).values(
            "jid_id", "metric", "units", "value"
        )
    )
    metric_dict = {}
    hist_metrics_set = set()
    for row in metrics_rows:
        jid_id = row["jid_id"]
        metric_dict.setdefault(row["metric"], []).append((jid_id, row["value"]))
        hist_metrics_set.add((row["metric"], row["units"]))

    df_fields = list(
        set(name for name, _ in (key.split("__") for key in cur_metrics))
    )
    jid_dict = {"jid": jids_ordered}
    for name in df_fields:
        jid_to_val = {jid: val for jid, val in metric_dict.get(name, [])}
        jid_dict[name] = [jid_to_val.get(jid, None) for jid in jids_ordered]
    df = DataFrame(jid_dict).set_index("jid")
    hist_metrics = list(hist_metrics_set)
    df = df.join(job_df)
    df["queue_wait"] = queue_wait_hours_series(job_df["start_time"], job_df["submit_time"])
    df["runtime"] = df["runtime"] / 3600
    # Fixed histograms use actual df column names; display titles mapped for UI
    hist_metrics += [("runtime", "hours"), ("nhosts", "# nodes"), ("queue_wait", "hours")]
    # Keep df numeric for histograms; do not run clean_dataframe here (it would
    # replace NaN with '' and break job_hist). job_hist filters to finite values.
    # Only plot metrics that exist as columns (df has filter metrics + runtime/nhosts/queue_wait)
    hist_metrics = [(m, label) for m, label in hist_metrics if m in df.columns]
    return df, hist_metrics, jids_ordered


def _extract_bokeh_doc_root_ids(doc):
    """Return a set of root ids declared in a Bokeh json_item doc payload."""
    ids = set()
    if not isinstance(doc, dict):
        return ids
    roots = doc.get("roots")
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                ids.add(root.strip())
                continue
            if isinstance(root, dict):
                rid = root.get("id")
                if isinstance(rid, str) and rid.strip():
                    ids.add(rid.strip())
                elif isinstance(rid, int):
                    ids.add(str(rid))
        return ids
    if not isinstance(roots, dict):
        return ids
    for rid in roots.get("root_ids", []) or []:
        if isinstance(rid, str) and rid.strip():
            ids.add(rid.strip())
        elif isinstance(rid, int):
            ids.add(str(rid))
    for ref in roots.get("references", []) or []:
        if isinstance(ref, dict):
            rid = ref.get("id")
            if isinstance(rid, str) and rid.strip():
                ids.add(rid.strip())
            elif isinstance(rid, int):
                ids.add(str(rid))
    return ids


def _is_valid_bokeh_json_item_payload(payload):
    """True when json_item has declared root ids and doc/root consistency."""
    if not isinstance(payload, dict):
        return False
    doc = payload.get("doc")
    if not isinstance(doc, dict):
        return False
    doc_root_ids = _extract_bokeh_doc_root_ids(doc)
    if not doc_root_ids:
        return False
    root_id = payload.get("root_id")
    if isinstance(root_id, str) and root_id.strip():
        return root_id.strip() in doc_root_ids
    if isinstance(root_id, int):
        return str(root_id) in doc_root_ids
    root_ids = payload.get("root_ids")
    if not isinstance(root_ids, list) or not root_ids:
        return False
    for rid in root_ids:
        key = rid.strip() if isinstance(rid, str) else (str(rid) if isinstance(rid, int) else "")
        if not key or key not in doc_root_ids:
            return False
    return True


def _sanitize_hist_plot_item(plot):
    """Convert Bokeh plot to json_item and drop invalid payloads."""
    if plot is None:
        return None
    try:
        payload = json_item(plot)
    except Exception:
        return None
    return payload if _is_valid_bokeh_json_item_payload(payload) else None


def _job_list_metric_hist_pair(df, metric_name, label, display_title, thumb_wh, full_wh):
    """Return (thumb_figure, full_figure) for one metric via ``job_hist``."""
    tw, th = thumb_wh
    fw, fh = full_wh
    p_thumb = job_hist(
        df, metric_name, label, width=tw, height=th, title=display_title
    )
    p_full = job_hist(
        df, metric_name, label, width=fw, height=fh, title=display_title
    )
    return p_thumb, p_full


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def job_list_histograms(request):
    """
    Return Bokeh histograms for the job list, loaded incrementally.

    This endpoint supports per-plot loading for metric histograms. The caller
    must provide a 'group' query parameter:

    - group=metric&metric=<name>: return a single metric histogram (thumb and
      full) for the given metric name.

    Example:
      /api/jobs/histograms/?end_time__date=2024-01-01&group=metric&metric=runtime
    """
    err = _require_auth(request)
    if err is not None:
        return err
    group = (request.GET.get("group") or "").strip()
    if not group:
        return _JSONResponse(
            {
                "error": "Missing 'group' parameter.",
                "allowed_groups": ["metric"],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    job_list_qs, nj, fields, cur_metrics = _build_histogram_queryset(request)
    if nj == 0:
        # Preserve a consistent shape even when no jobs match the filter.
        if group == "metric":
            metric_name = (request.GET.get("metric") or "").strip()
            return _JSONResponse(
                {
                    "group": "metric",
                    "metric": metric_name or None,
                    "nj": 0,
                    "title": JOB_HIST_DISPLAY_NAMES.get(metric_name, metric_name),
                    "plot_item_thumb": None,
                    "plot_item_full": None,
                    "plot_unavailable_reason": "No jobs matched this query.",
                }
            )
        return _JSONResponse(
            {
                "error": f"Unknown group '{group}'.",
                "allowed_groups": ["metric"],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    THUMB_WIDTH, THUMB_HEIGHT = 280, 200
    FULL_WIDTH, FULL_HEIGHT = 600, 400

    if group == "metric":
        metric_name = (request.GET.get("metric") or "").strip()
        if not metric_name:
            return Response(
                {
                    "error": "Missing 'metric' parameter for group 'metric'.",
                    "detail": "Provide ?metric=<metric_name> to load one metric histogram at a time.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        df, hist_metrics, _ = _build_histogram_dataframe(job_list_qs, cur_metrics)
        label = None
        for m, lbl in hist_metrics:
            if m == metric_name:
                label = lbl
                break
        if label is None:
            return Response(
                {
                    "error": f"Metric '{metric_name}' is not available for this query.",
                    "available_metrics": [m for m, _ in hist_metrics],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        display_title = JOB_HIST_DISPLAY_NAMES.get(metric_name, metric_name)
        p_thumb, p_full = _job_list_metric_hist_pair(
            df,
            metric_name,
            label,
            display_title,
            (THUMB_WIDTH, THUMB_HEIGHT),
            (FULL_WIDTH, FULL_HEIGHT),
        )
        thumb_item = _sanitize_hist_plot_item(p_thumb)
        full_item = _sanitize_hist_plot_item(p_full)

        return _JSONResponse(
            {
                "group": "metric",
                "metric": metric_name,
                "nj": nj,
                "title": display_title,
                "plot_item_thumb": thumb_item,
                "plot_item_full": full_item,
                "plot_unavailable_reason": None
                if (thumb_item is not None and full_item is not None)
                else f"No histogram data available for metric '{metric_name}' in this query.",
            }
        )

    return _JSONResponse(
        {
            "error": f"Unknown group '{group}'.",
            "allowed_groups": ["metric"],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def job_list(request):
    """Paginated job list only (histograms via separate job_list_histograms endpoint)."""
    err = _require_auth(request)
    if err is not None:
        return err

    job_list_qs, fields, _cur_metrics, order_by = _build_job_list_queryset_from_request(
        request,
        extra_excluded_fields=_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM,
        annotate_all=True,
    )

    try:
        nj = job_list_qs.count()
    except Exception:
        # Database unavailable or other error: behave as if no jobs matched.
        logger.exception("job_list: count() failed")
        return Response(
            {"error": "No data found for this search request"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if nj == 0:
        return Response(
            {"error": "No data found for this search request"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Aggregate over full filtered list (non-paginated) for listing-page metrics
    # Use node_hrs directly from the database to compute total node hours.
    agg = job_list_qs.aggregate(total_node_hours=Sum("node_hrs"))
    total_node_hours = agg.get("total_node_hours") or 0.0

    aggregates = {"total_node_hours": round(total_node_hours, 4)}
    if request.session.get("is_staff", False):
        try:
            wait_agg = aggregate_queue_wait_seconds_stats(job_list_qs)
            mean_s = wait_agg.get("mean_wait_s")
            if mean_s is not None:
                aggregates["queue_wait_mean_hours"] = round(float(mean_s) / 3600.0, 4)
        except Exception:
            logger.exception("job_list: queue wait aggregate failed")

    page_num = request.GET.get("page", 1)
    paginator = Paginator(job_list_qs, min(100, nj))
    try:
        page = paginator.page(page_num)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    current_path = request.get_full_path() if "?" in request.get_full_path() else None
    qname = "Jobs"
    date_param = request.GET.get("end_time__date", "").strip()
    if date_param and len(date_param) == 4 and date_param.isdigit():
        qname = f"Jobs for year {date_param}"
    elif date_param:
        qname = f"Jobs for date {date_param}"
    elif fields.get("queue"):
        qname = f"Jobs in queue {fields['queue']}"

    serialized_jobs = JobListSerializer(page.object_list, many=True).data
    if not request.session.get("is_staff", False):
        for row in serialized_jobs:
            row.pop("sample_count", None)

    return Response({
        "job_list": serialized_jobs,
        "nj": nj,
        "aggregates": aggregates,
        "current_path": current_path,
        "qname": qname,
        "order_by": order_by,
        "pagination": {
            "page": page.number,
            "num_pages": paginator.num_pages,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": page.previous_page_number() if page.has_previous() else None,
            "next_page_number": page.next_page_number() if page.has_next() else None,
        },
    })


@api_view(["GET"])
def job_detail(request, pk):
    """Single job detail: metadata, host_list, fsio, xalt, schema, URLs (plots via separate job_plots endpoint)."""
    err = _require_auth(request)
    if err is not None:
        return err

    job_cache_timeout = get_site_content_cache_timeout()
    job, err = _get_visible_job_or_error_response(
        request,
        pk,
        lambda: job_data.objects.filter(jid=pk).prefetch_related("metrics_data_set").first(),
    )
    if err is not None:
        return err

    j = jid_table.jid_table(job.jid)
    host_list = j.acct_host_list
    light_mode = str(request.GET.get("light", "")).lower() in ("1", "true", "yes")

    def _fetch_xalt():
        close_old_connections()

        def _xalt_fn():
            xalt_data = xalt_data_c()
            # XALT can be very large for some jobs. Truncate to keep this
            # endpoint from timing out under proxy/gunicorn limits.
            max_xalt_runs = 200
            max_xalt_joins = 5000

            runs = list(
                run.objects.using("xalt")
                .filter(job_id=job.jid)
                .order_by("run_id")
                .only("exec_path", "cwd", "run_id")[:max_xalt_runs]
            )
            run_ids = [r.run_id for r in runs]
            joins = (
                list(
                    join_run_object.objects.using("xalt")
                    .filter(run_id__in=run_ids)
                    .order_by("run_id")
                    .only("run_id", "obj_id")[:max_xalt_joins]
                )
                if run_ids
                else []
            )
            obj_ids = list(set(jo.obj_id for jo in joins))
            libs_by_id = {
                l.obj_id: l
                for l in lib.objects.using("xalt")
                .filter(obj_id__in=obj_ids)
                .only("object_path", "module_name")
            } if obj_ids else {}
            joins_by_run = {}
            for jo in joins:
                joins_by_run.setdefault(jo.run_id, []).append(jo)
            for r in runs:
                if "usr" in (r.exec_path or "").split("/"):
                    continue
                xalt_data.exec_path.append(r.exec_path)
                xalt_data.cwd.append((r.cwd or "")[0:128])
                for join in joins_by_run.get(r.run_id, []):
                    obj = libs_by_id.get(join.obj_id)
                    if not obj:
                        continue
                    module_name = obj.module_name or "none"
                    if any(libtmp.module_name == module_name for libtmp in xalt_data.libset):
                        continue
                    xalt_data.libset.append(
                        libset_c(object_path=obj.object_path, module_name=module_name)
                    )
            xalt_data.exec_path = list(set(xalt_data.exec_path))
            xalt_data.cwd = list(set(xalt_data.cwd))
            xalt_data.libset = sorted(xalt_data.libset, key=lambda x: x.module_name)
            return {
                "exec_path": xalt_data.exec_path,
                "cwd": xalt_data.cwd,
                "libset": [(l.object_path, l.module_name) for l in xalt_data.libset],
            }

        try:
            return cached_orm(f"{KEY_XALT}:{job.jid}", job_cache_timeout, _xalt_fn)
        finally:
            close_old_connections()

    detail_payload = load_job_detail_artifact(
        job.jid,
        ARTIFACT_KIND_JOB_DETAIL,
        "",
        compute_detail_input_fingerprint(job),
    ) or {}

    def _fetch_proc_list():
        from .models import proc_data

        close_old_connections()
        try:
            return cached_orm(
                f"{KEY_PROC_LIST}:{job.jid}",
                job_cache_timeout,
                lambda: list(
                    proc_data.objects.filter(jid=job.jid)
                    .values_list("proc", flat=True)
                    .distinct()
                ),
            )
        finally:
            close_old_connections()

    gpu_active = detail_payload.get("gpu_active")
    gpu_utilization_max = detail_payload.get("gpu_utilization_max")
    gpu_utilization_mean = detail_payload.get("gpu_utilization_mean")
    gpu_count = detail_payload.get("gpu_count")
    xalt_payload = None
    fsio = detail_payload.get("fsio") or {}
    schema = detail_payload.get("schema") or {}
    proc_list = []

    tasks = [("proc_list", _fetch_proc_list)]
    if (not light_mode) and cfg.get_xalt_user() != "":
        tasks.append(("xalt", _fetch_xalt))

    if not light_mode and tasks:
        JOB_DETAIL_MAX_WAIT_SECONDS = 25  # Keep job_detail responsive vs proxy timeouts.
        executor = _get_small_executor()
        future_to_key = {executor.submit(fn): key for key, fn in tasks}
        results_by_key, remaining_keys = _collect_future_results_with_deadline(
            future_to_key,
            JOB_DETAIL_MAX_WAIT_SECONDS,
        )

        if remaining_keys:
            logging.getLogger(__name__).warning(
                "job_detail max wait exceeded for jid=%s (pending=%s)",
                job.jid,
                sorted(remaining_keys),
            )

        for key, result in results_by_key.items():
            try:
                if key == "xalt":
                    xalt_payload = result
                elif key == "proc_list":
                    proc_list = result or []
            except Exception:
                pass
    else:
        # Light mode: skip heavy parallel tasks (XALT, schema, fsio, etc.) so
        # the response returns quickly and the React UI can render first.
        gpu_active = gpu_utilization_max = gpu_utilization_mean = gpu_count = None
        xalt_payload = None
        fsio = {}
        schema = {}
        proc_list = []

    xalt_data = {
        "exec_path": xalt_payload["exec_path"] if xalt_payload else [],
        "cwd": xalt_payload["cwd"] if xalt_payload else [],
        "libset": xalt_payload["libset"] if xalt_payload else [],
    }

    # Build client/server log URLs with explicit timestamp format expected by Scribe.
    # Format: %Y-%m-%dT%H:%M:%S %Z%:z
    start_time, end_time = _job_times_as_local(job.start_time, job.end_time)
    time_format = "%Y-%m-%dT%H:%M:%S%:z"
    start_time.strftime(time_format)
    end_time.strftime(time_format)

    urlstring = "https://scribe.tacc.utexas.edu/en-US/app/search/search?q=search%20"
    if host_list:
        hoststring = urlstring + "%20host%3D" + host_list[0] + cfg.get_host_name_ext()
        for host in host_list[1:]:
            hoststring += "%20OR%20%20host%3D" + host + "*"
    else:
        hoststring = urlstring
    serverstring = urlstring + "%20mds*%20OR%20%20oss*"

    earliest_ts = _format_log_timestamp(j.start_time)
    latest_ts = _format_log_timestamp(j.end_time)

    hoststring += (
        "&earliest=" + earliest_ts
        + "&latest=" + latest_ts
        + "&display.prefs.events.count=50"
    )
    serverstring += (
        "&earliest=" + earliest_ts
        + "&latest=" + latest_ts
        + "&display.prefs.events.count=50"
    )

    metrics_list = build_job_metrics_display_list(job)

    payload = {
        "job_data": JobListSerializer(job).data,
        "host_list": host_list,
        "fsio": fsio,
        "xalt_data": xalt_data,
        "schema": schema,
        "client_url": hoststring,
        "server_url": serverstring,
        "gpu_active": gpu_active,
        "gpu_utilization_max": gpu_utilization_max,
        "gpu_utilization_mean": gpu_utilization_mean,
        "gpu_count": gpu_count,
        "metrics_list": metrics_list,
        "proc_list": proc_list,
        "derived_data_status": "ready" if detail_payload else "loading",
    }
    if request.session.get("is_staff", False):
        payload["staff_metrics_distinct_time_count"] = job.metrics_distinct_time_count

    return Response(payload)


@api_view(["GET"])
def job_plots(request, pk):
    """
    Job-level plots grouped by shared jid_table input.

    Returns Bokeh json_items and availability reasons for:
    - Summary plot
    - Roofline
    - GPU roofline

    Query params:
    - plot: omit or ``all`` for all three; or one of summary_plot, roofline, gpu_roofline.
    - zoom: ``1`` for zoom layout (single-plot requests only).
    - progressive: ``1`` with plot=all (no zoom): HTTP 200 with ``status`` partial/ready and
      only completed plot fields included while others are listed in ``loading_plots``;
      avoids 202 while still using one endpoint and shared background tasks.
    """
    err = _require_auth(request)
    if err is not None:
        return err

    job_cache_timeout = get_site_content_cache_timeout()
    job, err = _get_visible_job_or_error_response(
        request,
        pk,
        lambda: job_data.objects.filter(jid=pk).prefetch_related("metrics_data_set").first(),
    )
    if err is not None:
        return err

    plot_kind = (request.GET.get("plot") or "").strip().lower()
    zoom_mode = str(request.GET.get("zoom", "")).lower() in ("1", "true", "yes")
    if plot_kind and plot_kind not in JOB_PLOT_KINDS:
        return Response(
            {
                "error": (
                    "Invalid plot parameter. "
                    "Use {}.".format(", ".join(JOB_PLOT_KINDS))
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not plot_kind:
        plot_kind = "all"

    progressive = (
        not zoom_mode
        and plot_kind == "all"
        and str(request.GET.get("progressive", "")).lower() in ("1", "true", "yes")
    )

    live_dc = get_live_distinct_time_count_for_jid(job.jid)
    plot_fingerprint = compute_plot_input_fingerprint(job, live_dc)
    l2_layout = JOB_PLOT_LAYOUT_ZOOM_V3 if zoom_mode else JOB_PLOT_LAYOUT_NORMAL

    jt_holder = {"jt": None}

    def _get_jt():
        if jt_holder["jt"] is None:
            jt_holder["jt"] = jid_table.jid_table(job.jid)
        return jt_holder["jt"]

    def _run_job_plot_fetch(kind):
        """Build one (json_item, unavailable_reason) tuple from shared plot-kind specs."""
        spec = JOB_PLOT_KIND_SPECS[kind]
        plot_item, reason = None, None
        close_old_connections()
        wall_t0 = time.monotonic() if spec.wall_time else None
        try:
            try:
                plot_item, reason = compute_plot_item_for_kind(_get_jt(), kind, zoom_mode)
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "Failed to %s for jid %s: %s",
                    spec.log_fail_action,
                    job.jid,
                    e,
                    exc_info=True,
                )
                reason = str(e)
            return (plot_item, reason)
        finally:
            if spec.wall_time:
                logging.getLogger(__name__).debug(
                    "job_plots summary_plot jid=%s wall_s=%.3f",
                    job.jid,
                    time.monotonic() - wall_t0,
                )
            close_old_connections()

    fetchers = {kind: partial(_run_job_plot_fetch, kind) for kind in JOB_PLOT_KINDS}
    _job_plots_log = logging.getLogger(__name__)

    def _plot_data_cache_key(kind):
        # Include plot fingerprint so zoom-mode reuse cannot serve stale payloads.
        return make_cache_key("JOB_PLOTS_DATA", job.jid, kind, plot_fingerprint)

    def _finalize_job_plot_future(key, inflight_key, future):
        """If *future* is done, store its result in *cached_results* and caches; clear inflight."""
        if not future.done():
            return
        try:
            try:
                plot_item, unavailable_reason = future.result()
                cached_results[key] = {
                    "plot_item": plot_item,
                    "unavailable_reason": unavailable_reason,
                }
                size_key = "zoom_v3" if zoom_mode else "normal"
                cache_key = make_cache_key(
                    "JOB_PLOTS_JSON",
                    job.jid,
                    key,
                    size_key,
                    plot_fingerprint,
                )
                try:
                    cache.set(cache_key, cached_results[key], timeout=job_cache_timeout)
                    register_job_plot_cache_key(job.jid, cache_key)
                except Exception as e:
                    _job_plots_log.warning(
                        "job_plots L1 set failed jid=%s key=%s: %s",
                        job.jid,
                        key,
                        e,
                        exc_info=True,
                    )
                if plot_item is not None:
                    try:
                        data_cache_key = _plot_data_cache_key(key)
                        cache.set(data_cache_key, plot_item, timeout=job_cache_timeout)
                        register_job_plot_cache_key(job.jid, data_cache_key)
                    except Exception as e:
                        _job_plots_log.warning(
                            "job_plots L1 data set failed jid=%s key=%s: %s",
                            job.jid,
                            key,
                            e,
                            exc_info=True,
                        )
            except Exception as e:
                _job_plots_log.warning(
                    "job_plots task failed for jid=%s key=%s: %s",
                    job.jid,
                    key,
                    e,
                    exc_info=True,
                )
                cached_results[key] = {"plot_item": None, "unavailable_reason": str(e)}
        finally:
            with _job_plots_lock:
                _job_plot_inflight.pop(inflight_key, None)

    requested_keys = (
        [plot_kind]
        if plot_kind != "all"
        else list(JOB_PLOT_KINDS)
    )

    # Cache each plot separately so clients can poll each plot independently.
    cached_results = {}
    missing_keys = []
    for key in requested_keys:
        size_key = "zoom_v3" if zoom_mode else "normal"
        cache_key = make_cache_key(
            "JOB_PLOTS_JSON",
            job.jid,
            key,
            size_key,
            plot_fingerprint,
        )
        cached_entry = cache.get(cache_key)
        spec = JOB_PLOT_KIND_SPECS[key]
        stale_generic_reason = (
            isinstance(cached_entry, dict)
            and cached_entry.get("plot_item") is None
            and cached_entry.get("unavailable_reason") == spec.empty_fallback
        )
        if stale_generic_reason:
            missing_keys.append(key)
            continue
        if isinstance(cached_entry, dict):
            cached_results[key] = cached_entry
            continue
        l2_entry = load_cached_job_plot_entry(
            job.jid, key, l2_layout, plot_fingerprint
        )
        if l2_entry is not None and isinstance(l2_entry.get("plot_item"), dict):
            cached_results[key] = {
                "plot_item": l2_entry["plot_item"],
                "unavailable_reason": l2_entry.get("unavailable_reason"),
            }
            try:
                cache.set(cache_key, cached_results[key], timeout=job_cache_timeout)
                register_job_plot_cache_key(job.jid, cache_key)
            except Exception as e:
                _job_plots_log.warning(
                    "job_plots L1 set from L2 failed jid=%s key=%s: %s",
                    job.jid,
                    key,
                    e,
                    exc_info=True,
                )
            if cached_results[key]["plot_item"] is not None:
                try:
                    data_cache_key = _plot_data_cache_key(key)
                    cache.set(
                        data_cache_key,
                        cached_results[key]["plot_item"],
                        timeout=job_cache_timeout,
                    )
                    register_job_plot_cache_key(job.jid, data_cache_key)
                except Exception as e:
                    _job_plots_log.warning(
                        "job_plots L1 data set from L2 failed jid=%s key=%s: %s",
                        job.jid,
                        key,
                        e,
                        exc_info=True,
                    )
            continue
        missing_keys.append(key)

    # Reuse cached plot data payload (size-independent) to build zoom plot JSON
    # without recomputing expensive aggregates.
    if zoom_mode and missing_keys:
        still_missing = []
        for key in missing_keys:
            data_cache_key = _plot_data_cache_key(key)
            cached_plot_data = cache.get(data_cache_key)
            if isinstance(cached_plot_data, dict):
                cached_results[key] = {
                    "plot_item": _apply_zoom_layout_to_json_item(cached_plot_data),
                    "unavailable_reason": None,
                }
            else:
                still_missing.append(key)
        missing_keys = still_missing

    for key in missing_keys:
        inflight_key = (job.jid, key, "zoom_v3" if zoom_mode else "normal")
        with _job_plots_lock:
            _evict_stale_inflight_plot_tasks()
            inflight_meta = _job_plot_inflight.get(inflight_key) or {}
            future = inflight_meta.get("future")
            if future is None:
                executor = _get_small_executor()
                future = executor.submit(fetchers[key])
                _job_plot_inflight[inflight_key] = {
                    "future": future,
                    "created_at": time.monotonic(),
                }
        _finalize_job_plot_future(key, inflight_key, future)

    # Harvest completions that finish shortly after submit so one request can often
    # return the full payload (avoids an extra client poll round).
    pending_meta = {}
    for key in requested_keys:
        if key in cached_results:
            continue
        inflight_key = (job.jid, key, "zoom_v3" if zoom_mode else "normal")
        with _job_plots_lock:
            meta = _job_plot_inflight.get(inflight_key) or {}
            fut = meta.get("future")
        if fut is not None:
            pending_meta[fut] = (key, inflight_key)
    _harvest_timeout_s = 0.55 if progressive else 0.4
    if pending_meta:
        try:
            for fut in as_completed(pending_meta.keys(), timeout=_harvest_timeout_s):
                k, ik = pending_meta[fut]
                _finalize_job_plot_future(k, ik, fut)
        except FuturesTimeoutError:
            pass

    still_loading = [key for key in requested_keys if key not in cached_results]
    if still_loading:
        if progressive:
            body = {
                "status": "partial",
                "detail": "Some plots are still being generated. Retry this request shortly.",
                "retry_after_seconds": 2,
                "loading_plots": still_loading,
                "progressive": True,
            }
            for key in requested_keys:
                if key not in cached_results:
                    continue
                item_key, reason_key = JOB_PLOT_JSON_KEYS[key]
                body[item_key] = cached_results[key]["plot_item"]
                body[reason_key] = cached_results[key]["unavailable_reason"]
            return Response(body, status=status.HTTP_200_OK)
        return Response(
            {
                "status": "loading",
                "detail": "Requested plots are still being generated. Retry this request shortly.",
                "retry_after_seconds": 2,
                "loading_plots": still_loading,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    if plot_kind != "all":
        entry = cached_results[plot_kind]
        return Response(
            {
                "status": "ready",
                "plot": plot_kind,
                "plot_item": entry["plot_item"],
                "unavailable_reason": entry["unavailable_reason"],
            }
        )

    payload = {
        "mscript": "",
        "mdiv": "",
        "mplot_item": cached_results["summary_plot"]["plot_item"],
        "mplot_unavailable_reason": cached_results["summary_plot"]["unavailable_reason"],
        "rscript": "",
        "rdiv": "",
        "rplot_item": cached_results["roofline"]["plot_item"],
        "rplot_unavailable_reason": cached_results["roofline"]["unavailable_reason"],
        "grscript": "",
        "grdiv": "",
        "grplot_item": cached_results["gpu_roofline"]["plot_item"],
        "grplot_unavailable_reason": cached_results["gpu_roofline"]["unavailable_reason"],
    }
    if progressive:
        payload["status"] = "ready"
        payload["progressive"] = True
        payload["loading_plots"] = []
    return Response(payload)


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def type_detail(request, jid, type_name):
    """Type detail: Bokeh json_item (tplot_item), stats_data, schema."""
    err = _require_auth(request)
    if err is not None:
        return err

    job, err = _get_visible_job_or_error_response(
        request,
        jid,
        lambda: job_data.objects.filter(jid=jid).only("host_list", "start_time", "end_time").first(),
    )
    if err is not None:
        return err

    detail_payload = load_job_detail_artifact(
        jid,
        ARTIFACT_KIND_TYPE_DETAIL,
        type_name,
        compute_detail_input_fingerprint(job),
    )
    if not detail_payload:
        return Response({
            "type_name": type_name,
            "jobid": jid,
            "tplot_item": None,
            "tplot_unavailable_reason": "Type detail artifact not ready yet. Run update_metrics and retry.",
            "stats_data": [],
            "schema": [],
            "status": "loading",
        })
    detail_payload["status"] = "ready"
    return Response(detail_payload)


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def host_plot(request):
    """Return Bokeh plot_item for a single host and time range (GET host, end_time__gte, end_time__lte)."""
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "host_plot is restricted to admin-only access."},
            status=status.HTTP_403_FORBIDDEN,
        )

    site_ttl = get_site_content_cache_timeout()

    host_fqdn = (request.GET.get("host") or "").strip()
    start_time = request.GET.get("end_time__gte", "").strip()
    end_time = (request.GET.get("end_time__lte") or "now()").strip()

    if not host_fqdn or not start_time:
        return Response(
            {"error": "Missing host or end_time__gte"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        start_dt = timezone.now() - timedelta(days=1)
    if end_time == "now()" or not end_time:
        end_dt = timezone.now()
    else:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            end_dt = timezone.now()
    if start_dt.tzinfo is None:
        start_dt = timezone.make_aware(start_dt, dt_timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = timezone.make_aware(end_dt, dt_timezone.utc)
    start_dt = start_dt.astimezone(local_timezone)
    end_dt = end_dt.astimezone(local_timezone)

    def _host_plot_fn():
        try:
            ht = HostDataProvider(host_fqdn, start_dt, end_dt)
            sp = plots.SummaryPlot(ht)
            plot = sp.plot()
            return json_item(plot)
        except Exception:
            return None

    cache_key = make_cache_key(
        KEY_HOST_PLOT, host_fqdn, start_dt.isoformat(), end_dt.isoformat()
    )
    plot_item = cached_orm(cache_key, site_ttl, _host_plot_fn)

    return Response({
        "host": host_fqdn,
        "plot_item": plot_item,
        "plot_unavailable_reason": None if plot_item is not None else "No host plot data available for this host/time range.",
        "end_time__gte": start_dt.isoformat(),
        "end_time__lte": end_dt.isoformat(),
    })


def _get_xalt_jid_coverage(days=3, missing_limit=200, chunk_size=1000):
    """
    Staff-only: compute XALT coverage for JIDs in the last `days` in the main DB.

    Coverage means: does `xalt_run` contain at least one row where
    `job_id == job_data.jid`.
    """
    # If XALT DB isn't configured, return a stable shape for the UI.
    if cfg.get_xalt_user() == "":
        return {
            "error": "XALT database is not configured (missing xalt_user).",
            "window_days": days,
            "found_jids": [],
            "found_jids_limit": missing_limit,
            "found_jids_truncated": False,
            "missing_jids": [],
            "missing_jids_limit": missing_limit,
            "missing_jids_truncated": False,
        }

    now = timezone.now()
    since_dt = now - timedelta(days=days)

    def _xalt_fn():
        # Pull JIDs from the main DB for the time window.
        jids_qs = (
            job_data.objects.filter(end_time__gte=since_dt)
            .values_list("jid", flat=True)
            .distinct()
        )
        jids = list(jids_qs)
        total_jids = len(jids)

        if total_jids == 0:
            return {
                "window_days": days,
                "since": since_dt.isoformat(),
                "total_jids": 0,
                "jids_with_xalt_data": 0,
                "jids_with_xalt_runs_recent": 0,
                "jids_missing_xalt_data": 0,
                "found_jids": [],
                "found_jids_limit": missing_limit,
                "found_jids_truncated": False,
                "missing_jids": [],
                "missing_jids_limit": missing_limit,
                "missing_jids_truncated": False,
            }

        # Query XALT DB in chunks to avoid huge IN lists.
        present_counts = {}  # jid -> {runs_total, runs_recent}
        present_set = set()

        def _chunks(seq, size):
            for i in range(0, len(seq), size):
                yield seq[i : i + size]

        for chunk in _chunks(jids, chunk_size):
            if not chunk:
                continue
            qs = (
                run.objects.using("xalt")
                .filter(job_id__in=chunk)
                .values("job_id")
                .annotate(
                    runs_total=Count("run_id"),
                    runs_recent=Sum(
                        Case(
                            When(date__gte=since_dt, then=1),
                            default=0,
                            output_field=IntegerField(),
                        )
                    ),
                )
            )
            for row in qs:
                jid = row.get("job_id") or ""
                if not jid:
                    continue
                present_set.add(jid)
                present_counts[jid] = {
                    "runs_total": int(row.get("runs_total") or 0),
                    "runs_recent": int(row.get("runs_recent") or 0),
                }

        jids_with_xalt_data = len(present_set)
        jids_missing_xalt_data = total_jids - jids_with_xalt_data
        jids_with_xalt_runs_recent = sum(
            1
            for jid in present_set
            if (present_counts.get(jid) or {}).get("runs_recent", 0) > 0
        )

        # Include missing JIDs for triage, but truncate the list to keep the
        # response light.
        missing_jids = []
        for jid in sorted(jids):
            if jid in present_set:
                continue
            missing_jids.append(jid)
            if len(missing_jids) >= missing_limit:
                break

        missing_jids_truncated = jids_missing_xalt_data > len(missing_jids)

        # Also return a truncated list of JIDs that do have XALT data so
        # admins can quickly sanity-check the join.
        found_jids = []
        for jid in sorted(present_set):
            found_jids.append(jid)
            if len(found_jids) >= missing_limit:
                break

        found_jids_truncated = jids_with_xalt_data > len(found_jids)

        return {
            "window_days": days,
            "since": since_dt.isoformat(),
            "total_jids": total_jids,
            "jids_with_xalt_data": jids_with_xalt_data,
            "jids_with_xalt_runs_recent": jids_with_xalt_runs_recent,
            "jids_missing_xalt_data": jids_missing_xalt_data,
            "found_jids": found_jids,
            "found_jids_limit": missing_limit,
            "found_jids_truncated": found_jids_truncated,
            "missing_jids": missing_jids,
            "missing_jids_limit": missing_limit,
            "missing_jids_truncated": missing_jids_truncated,
        }

    try:
        close_old_connections()
        return cached_orm(KEY_ADMIN_XALT_STATS, TIMEOUT_ADMIN_STATS, _xalt_fn)
    except Exception as exc:
        return {
            "error": f"XALT coverage query failed: {exc}",
            "window_days": days,
            "since": since_dt.isoformat(),
            "total_jids": 0,
            "jids_with_xalt_data": 0,
            "jids_with_xalt_runs_recent": 0,
            "jids_missing_xalt_data": 0,
            "found_jids": [],
            "found_jids_limit": missing_limit,
            "found_jids_truncated": False,
            "missing_jids": [],
            "missing_jids_limit": missing_limit,
            "missing_jids_truncated": False,
        }
    finally:
        close_old_connections()


@api_view(["GET"])
def admin_monitor(request):
    """Staff-only: HPCPerfStats Monitor data (host timestamps, cache/Redis, RabbitMQ, TimescaleDB stats).

    Supports a lightweight, per-section API via the optional 'section' query
    param:
    - ?section=hosts      -> {"host_stats": [...]}
    - ?section=rabbitmq_hosts -> {"rabbitmq_host_stats": [...]}
    - ?section=cache      -> {"cache_stats": {...}}
    - ?section=rabbitmq   -> {"rabbitmq_stats": {...}}
    - ?section=timescaledb -> {"timescaledb_stats": {...}}
    - ?section=xalt      -> {"xalt_stats": {...}}
    - omitted/other       -> {"host_stats": [...], "rabbitmq_host_stats": [...],
                              "cache_stats": {...}, "rabbitmq_stats": {...},
                              "timescaledb_stats": {...}, "xalt_stats": {...}}
    """
    err = _require_staff(request)
    if err is not None:
        return err

    def _host_stats_fn():
        """Return per-host last_seen timestamps and age buckets for admin monitor.

        Uses host_data over the last 8 days, aggregating directly from the
        hypertable without a separate host list, to keep queries fast even on
        large installations.
        """
        now = timezone.now()
        time_bounds = now - timedelta(days=8)

        host_stats_local = []
        try:
            latest_qs = (
                host_data.objects.filter(time__gte=time_bounds)
                .values("host")
                .annotate(last_time=Max("time"))
            )
            with _pg_session_statement_timeout_for_admin_host_stats_query():
                for row in latest_qs:
                    host = row.get("host") or ""
                    last_time = row.get("last_time")
                    entry = _admin_monitor_host_stat_dict(host, last_time, now)
                    if entry is not None:
                        host_stats_local.append(entry)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to load latest host timestamps for admin_monitor: %s",
                exc,
                exc_info=True,
            )
            return []
        return host_stats_local

    section = (request.GET.get("section") or "").strip().lower()
    refresh = str(request.GET.get("refresh", "")).strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if refresh:
        keys_by_section = {
            "hosts": [KEY_ADMIN_HOST_STATS],
            "cache": [KEY_ADMIN_CACHE_STATS],
            "rabbitmq": [KEY_ADMIN_RMQ_STATS, KEY_ADMIN_RMQ_SNAPSHOT],
            "timescaledb": [KEY_ADMIN_TIMESCALE_STATS],
            "xalt": [KEY_ADMIN_XALT_STATS],
        }
        keys_to_clear = []
        if section in keys_by_section:
            keys_to_clear = keys_by_section[section]
        elif not section:
            for keys in keys_by_section.values():
                keys_to_clear.extend(keys)
        for cache_key in keys_to_clear:
            try:
                cache.delete(cache_key)
            except Exception:
                pass

    if section == "hosts":
        host_stats = cached_orm(KEY_ADMIN_HOST_STATS, TIMEOUT_ADMIN_STATS, _host_stats_fn)
        return Response({"host_stats": host_stats})
    if section == "rabbitmq_hosts":
        return Response({"rabbitmq_host_stats": _get_recent_rabbitmq_host_stats()})
    if section == "cache":
        return Response({"cache_stats": _get_cache_stats()})
    if section == "rabbitmq":
        return Response({"rabbitmq_stats": _get_rabbitmq_stats()})
    if section == "timescaledb":
        return Response({"timescaledb_stats": _get_timescaledb_stats()})
    if section == "xalt":
        return Response({"xalt_stats": _get_xalt_jid_coverage()})

    host_stats = cached_orm(KEY_ADMIN_HOST_STATS, TIMEOUT_ADMIN_STATS, _host_stats_fn)
    rabbitmq_host_stats = _get_recent_rabbitmq_host_stats()
    cache_stats = _get_cache_stats()
    rabbitmq_stats = _get_rabbitmq_stats()
    timescaledb_stats = _get_timescaledb_stats()

    return Response(
        {
            "host_stats": host_stats,
            "rabbitmq_host_stats": rabbitmq_host_stats,
            "cache_stats": cache_stats,
            "rabbitmq_stats": rabbitmq_stats,
            "timescaledb_stats": timescaledb_stats,
            "xalt_stats": _get_xalt_jid_coverage(),
        }
    )


@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def job_monitor(request):
    """Staff-only: aggregate job failure statistics per user over a recent window.

    The window is controlled by the optional ?days=N query parameter (integer).
    N is clamped to [1, 365]. If missing or invalid, defaults to 30 days.

    Returns rows of:
    - username
    - total_jobs: number of jobs run
    - failed_jobs: number of jobs with state OUT_OF_MEMORY or FAILED
    - failed_rate: percentage of failed jobs (0–100), sorted descending.
    GPU fields are intentionally omitted from this initial endpoint so the page
    can render quickly; per-user GPU stats are fetched asynchronously from
    job_monitor_gpu_for_user.
    """
    err = _require_staff(request)
    if err is not None:
        return err

    # Parse and clamp days window from query params.
    try:
        days_param = int(request.GET.get("days", "") or 30)
    except (TypeError, ValueError):
        days_param = 30
    window_days = max(1, min(days_param, 365))
    now = timezone.now()
    start_time = now - timedelta(days=window_days)

    base_qs = job_data.objects.filter(end_time__gte=start_time)
    stats_qs = (
        base_qs.values("username")
        .annotate(
            total_jobs=Count("jid"),
            failed_jobs=Count(
                "jid",
                filter=Q(state__in=["FAILED", "OUT_OF_MEMORY"]),
            ),
            timedout_jobs=Count(
                "jid",
                filter=Q(state="TIMEOUT"),
            ),
        )
        # Remove users that have not run more than window_days / 2 jobs.
        .filter(total_jobs__gt=(window_days / 2.0))
        .annotate(
            failed_rate=ExpressionWrapper(
                100.0 * F("failed_jobs") / F("total_jobs"),
                output_field=FloatField(),
            ),
            timedout_rate=ExpressionWrapper(
                100.0 * F("timedout_jobs") / F("total_jobs"),
                output_field=FloatField(),
            ),
        )
        .order_by("-failed_rate", "username")
    )

    rows = []
    for row in stats_qs:
        total = int(row.get("total_jobs") or 0)
        failed = int(row.get("failed_jobs") or 0)
        timedout = int(row.get("timedout_jobs") or 0)
        rate = float(row.get("failed_rate") or 0.0)
        timeout_rate = float(row.get("timedout_rate") or 0.0)
        username = row.get("username") or ""
        rows.append(
            {
                "username": username,
                "total_jobs": total,
                "failed_jobs": failed,
                "failed_rate": round(rate, 2),
                "timedout_jobs": timedout,
                "timedout_rate": round(timeout_rate, 2),
            }
        )

    return Response(
        {
            "window_days": window_days,
            "start_time": start_time.isoformat(),
            "end_time": now.isoformat(),
            "results": rows,
        }
    )


@api_view(["GET"])
def job_monitor_gpu_for_user(request):
    """Staff-only per-user GPU rollup for Job Monitor async row updates."""
    err = _require_staff(request)
    if err is not None:
        return err

    username = str((request.GET.get("username") or "").strip())
    if not username:
        return Response(
            {"error": "Missing required query param: username"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        days_param = int(request.GET.get("days", "") or 30)
    except (TypeError, ValueError):
        days_param = 30
    window_days = max(1, min(days_param, 365))
    now = timezone.now()
    start_time = now - timedelta(days=window_days)
    cache_key = make_cache_key("JOB_MONITOR_GPU_USER", window_days, username)
    site_ttl = get_site_content_cache_timeout()

    def _compute_user_gpu():
        gpu_count_total = None
        gpu_active_total = None
        md_base = metrics_data.objects.filter(
            jid__end_time__gte=start_time,
            jid__username=username,
        )
        if md_base.filter(metric="detail_gpu_count").exists():
            ag_a = md_base.filter(
                metric="detail_gpu_active", value__isnull=False
            ).aggregate(s=Sum("value"))
            ag_c = md_base.filter(
                metric="detail_gpu_count", value__isnull=False
            ).aggregate(s=Sum("value"))
            if ag_a["s"] is not None:
                gpu_active_total = int(ag_a["s"])
            if ag_c["s"] is not None:
                gpu_count_total = int(ag_c["s"])
        else:
            # Pre-backfill: no persisted GPU detail rows for this user/window.
            jobs_for_gpu = list(
                job_data.objects.filter(end_time__gte=start_time, username=username).only(
                    "jid", "username", "start_time", "end_time", "host_list"
                )
            )
            for job in jobs_for_gpu:
                try:
                    w_start, w_end, acct = jid_table.gpu_acct_window_for_job_data(job)
                    j = SimpleNamespace(
                        start_time=w_start, end_time=w_end, acct_host_list=acct
                    )
                    gpu_active, _gpu_max, _gpu_mean, per_job_gpu_count = (
                        _compute_job_gpu_stats(
                            job,
                            j,
                            site_ttl,
                            include_gpu_count=False,
                        )
                    )
                except Exception:
                    continue
                if per_job_gpu_count is not None:
                    gpu_count_total = (gpu_count_total or 0) + int(per_job_gpu_count)
                if gpu_active is not None:
                    gpu_active_total = (gpu_active_total or 0) + int(gpu_active)
        gpu_active_percentage = None
        if (
            gpu_count_total is not None
            and gpu_active_total is not None
            and float(gpu_count_total) > 0
        ):
            gpu_active_percentage = round(
                100.0 * float(gpu_active_total) / float(gpu_count_total),
                2,
            )
        has_data = gpu_count_total is not None or gpu_active_total is not None
        return {
            "username": username,
            "gpu_count_total": gpu_count_total,
            "gpu_active_total": gpu_active_total,
            "gpu_active_percentage": gpu_active_percentage,
            "has_data": has_data,
        }

    result = cached_orm(cache_key, site_ttl, _compute_user_gpu)
    return Response(result)


@api_view(["POST"])
def sacct_ingest(request):
    """Ingest pipe-delimited sacct output into job_data using sync_acct logic.

    Requires authentication (API key or session) and staff. Request body must be
    raw pipe-delimited sacct output (same format as sacct -P -o ...). Query
    param date=YYYY-MM-DD is required (the date of the data being ingested) to
    compute which jobs are already in the DB.
    """
    err = _require_staff(request)
    if err is not None:
        return err

    try:
        body = request.body.decode("utf-8", errors="replace")
    except Exception as e:
        return Response(
            {"error": "Invalid request body", "detail": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not body.strip():
        return Response({"inserted": 0, "date": request.GET.get("date", "")})

    date_str = (request.GET.get("date") or "").strip()
    if not date_str:
        return Response(
            {"error": "Missing required query param: date=YYYY-MM-DD"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        ingest_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return Response(
            {"error": "Invalid date; use date=YYYY-MM-DD"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    searchdate = ingest_date - timedelta(days=2)
    jobs_in_db = set(
        job_data.objects.filter(end_time__date__gte=searchdate)
        .values_list("jid", flat=True)
        .iterator(chunk_size=10000)
    )

    try:
        inserted = sync_acct_from_content(body, jobs_in_db)
    except Exception as e:
        if settings.DEBUG:
            raise
        return Response(
            {"error": "Ingest failed", "detail": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({"inserted": inserted, "date": date_str})
