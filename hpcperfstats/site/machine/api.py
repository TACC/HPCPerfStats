"""Django REST Framework API views for machine app. All data via JSON for React SPA."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone as dt_timezone

import hpcperfstats.conf_parser as cfg
from bokeh.embed import components, json_item
from bokeh.layouts import gridplot
from bokeh.plotting import figure
from django.utils import timezone
from pandas import DataFrame, to_timedelta
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from django.views.decorators.cache import cache_page

import os

from .cache_utils import (
    KEY_ADMIN_CACHE_STATS,
    KEY_ADMIN_RMQ_STATS,
    KEY_ADMIN_RMQ_SNAPSHOT,
    KEY_ADMIN_TIMESCALE_STATS,
    KEY_ADMIN_HOST_STATS,
    KEY_DATES,
    KEY_METRICS_DISTINCT,
    KEY_QUEUES,
    KEY_STATES,
    KEY_ALL_HOSTS,
    KEY_HOST_LAST,
    KEY_GPU_QS,
    KEY_XALT,
    KEY_TYPE_DETAIL_HOSTS,
    KEY_JOB,
    KEY_PROC_LIST,
    KEY_HOST_PLOT,
    cached_orm,
    make_cache_key,
    TIMEOUT_ADMIN_STATS,
    TIMEOUT_MEDIUM,
    TIMEOUT_SHORT,
    TIMEOUT_LONG,
)
from hpcperfstats.dbload.sync_acct import sync_acct_from_content
from .models import ApiKey, host_data, job_data, metrics_data
from .oauth2 import check_for_tokens
from .query_utils import (
    expand_month_date_to_range,
    get_job_list_order_by,
    normalize_job_list_query_params,
)
from .serializers import JobListSerializer
from .views import (
    job_hist,
    local_timezone,
    libset_c,
    xalt_data_c,
)

# Shared thread pools (capped total threads per process).
_host_last_executor = None
_small_executor = None   # dashboard, queue histograms, job_detail, job_plots (≤8 tasks)
_metric_hist_executor = None  # per-metric histograms in job list (up to 8)

def _get_host_last_executor():
    global _host_last_executor
    if _host_last_executor is None:
        _host_last_executor = ThreadPoolExecutor(max_workers=16)
    return _host_last_executor

def _get_small_executor():
    global _small_executor
    if _small_executor is None:
        _small_executor = ThreadPoolExecutor(max_workers=8)
    return _small_executor

def _get_metric_hist_executor():
    global _metric_hist_executor
    if _metric_hist_executor is None:
        _metric_hist_executor = ThreadPoolExecutor(max_workers=8)
    return _metric_hist_executor
from django.db.models import (
    Count,
    Exists,
    OuterRef,
    Sum,
    Q,
    F,
    FloatField,
    ExpressionWrapper,
    Max,
)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils import timezone as dj_timezone_utils
from datetime import datetime, timedelta
from numpy import isnan
from math import ceil

import hpcperfstats.analysis.gen.jid_table as jid_table
from hpcperfstats.analysis.gen.jid_table import HostDataProvider
import hpcperfstats.analysis.plot as plots
from hpcperfstats.site.xalt.models import join_run_object, lib, run


def _get_api_key_from_request(request):
    """Extract API key from Authorization header or query params.

    Supported formats:
    - Authorization: Api-Key <key>
    - X-API-Key header
    - api_key query parameter
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
    qp = request.GET.get("api_key")
    if qp:
        return qp.strip() or None
    return None


def _api_key_valid(key: str):
    """Return ApiKey instance if key is valid and active, else None."""
    if not key:
        return None
    try:
        api_key_obj = ApiKey.objects.get(key=key, is_active=True)
    except ApiKey.DoesNotExist:
        return None
    # Best-effort last-used update; ignore errors.
    try:
        api_key_obj.last_used_at = dj_timezone_utils.now()
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
        session.setdefault("access_token", f"api-key:{api_key_obj.key}")
        return None

    return Response(
        {"detail": "Authentication required", "login_url": "/login_prompt"},
        status=status.HTTP_401_UNAUTHORIZED,
    )


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
            now = dj_timezone_utils.now()
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


@cache_page(TIMEOUT_MEDIUM)
@api_view(["GET"])
def home_options(request):
    """Return options for search form: date_list, metrics, queues, states, machine_name."""
    err = _require_auth(request)
    if err is not None:
        return err

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
        cached_orm, KEY_DATES, TIMEOUT_MEDIUM, _dates_fn
    )
    metrics_future = executor.submit(
        cached_orm, KEY_METRICS_DISTINCT, TIMEOUT_LONG, _metrics_fn
    )
    queues_future = executor.submit(
        cached_orm, KEY_QUEUES, TIMEOUT_MEDIUM, _queues_fn
    )
    states_future = executor.submit(
        cached_orm, KEY_STATES, TIMEOUT_MEDIUM, _states_fn
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


@cache_page(TIMEOUT_SHORT)
@api_view(["GET"])
def search_dispatch(request):
    """
    Dispatch search: if jid -> return redirect url; if host+times -> return host plot data; else return job list (index).
    """
    err = _require_auth(request)
    if err is not None:
        return err

    if request.GET.get("jid"):
        jid = request.GET["jid"]
        job_jid = cached_orm(
            f"{KEY_JOB}:{jid}",
            TIMEOUT_SHORT,
            lambda: job_data.objects.filter(jid=jid).values_list("jid", flat=True).first(),
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


def _job_list_histograms_empty_figure():
    """Return a single Bokeh figure for empty histogram state (no jobs or no plottable metrics)."""
    empty = figure(
        height=400,
        width=600,
        title="No histogram data available for this job list.",
        toolbar_location=None,
    )
    return gridplot([[empty]], toolbar_location=None)


def _build_histogram_queryset(request):
    """
    Build the base queryset and metric filters for histogram endpoints.

    Returns (job_list_qs, nj, fields, cur_metrics) where:
    - job_list_qs: filtered and ordered queryset
    - nj: count of jobs in queryset
    - fields: normalized/expanded query params dict
    - cur_metrics: dict of metric_name__op -> value (from query params)
    """
    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics"
        and k
        not in (
            "page",
            "order_by",
            # Histogram grouping/query-only parameters, not model fields:
            # - group: which histogram group to load ("queue" or "metric")
            # - metric: metric name when group == "metric"
            "group",
            "metric",
        )
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    job_list_qs = job_data.objects.filter(**acct_data)
    if order_by.lstrip("-") == "has_metrics":
        job_list_qs = job_list_qs.annotate(
            has_metrics=Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
        )
    job_list_qs = job_list_qs.order_by(order_by)

    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.split("_", 1)[0] == "metrics"
    }
    for key, val in cur_metrics.items():
        name, op = key.split("__")
        mquery = {
            "metrics_data__metric": name,
            "metrics_data__value__" + op: val,
        }
        job_list_qs = job_list_qs.filter(**mquery)

    nj = job_list_qs.count()
    return job_list_qs, nj, fields, cur_metrics


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
    df["queue_wait"] = (
        to_timedelta(df["start_time"] - df["submit_time"]).dt.total_seconds() / 3600
    )
    df["runtime"] = df["runtime"] / 3600
    # Fixed histograms use actual df column names; display titles mapped for UI
    hist_metrics += [("runtime", "hours"), ("nhosts", "# nodes"), ("queue_wait", "hours")]
    # Keep df numeric for histograms; do not run clean_dataframe here (it would
    # replace NaN with '' and break job_hist). job_hist filters to finite values.
    # Only plot metrics that exist as columns (df has filter metrics + runtime/nhosts/queue_wait)
    hist_metrics = [(m, label) for m, label in hist_metrics if m in df.columns]
    return df, hist_metrics, jids_ordered


def _job_list_queue_histogram(job_list_qs, width=600, height=400):
    """Build a Bokeh bar chart of job count per queue from the full filtered job list (non-paginated)."""
    from bokeh.models import ColumnDataSource, HoverTool

    queue_counts = list(
        job_list_qs.values("queue")
        .annotate(count=Count("jid"))
        .order_by("-count")
        .values_list("queue", "count")
    )
    if not queue_counts:
        return None
    queue_names = [q if q else "(no queue)" for q, _ in queue_counts]
    counts = [c for _, c in queue_counts]
    source = ColumnDataSource(dict(x=queue_names, top=counts))
    p = figure(
        x_range=queue_names,
        height=height,
        width=width,
        title="Jobs by queue",
        toolbar_location=None,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    p.add_tools(
        HoverTool(tooltips=[("queue", "@x"), ("jobs", "@top")], point_policy="snap_to_data")
    )
    p.xaxis.axis_label = "queue"
    p.yaxis.axis_label = "# jobs"
    p.vbar(x="x", top="top", source=source, width=0.7)
    p.xgrid.visible = False
    p.xaxis.major_label_orientation = "vertical" if len(queue_names) > 5 else "horizontal"
    return p


def _job_list_queue_cpu_hours_histogram(job_list_qs, width=600, height=400):
    """Build a Bokeh bar chart of node hours (sum of node_hrs) per queue from the full filtered job list (non-paginated)."""
    from bokeh.models import ColumnDataSource, HoverTool

    queue_runtime = list(
        job_list_qs.values("queue")
        .annotate(total_node_hours=Sum("node_hrs"))
        .order_by("-total_node_hours")
        .values_list("queue", "total_node_hours")
    )
    if not queue_runtime:
        return None
    queue_names = [q if q else "(no queue)" for q, _ in queue_runtime]
    node_hours = [(nh or 0.0) for _, nh in queue_runtime]
    source = ColumnDataSource(dict(x=queue_names, top=node_hours))
    p = figure(
        x_range=queue_names,
        height=height,
        width=width,
        title="Node hours by queue",
        toolbar_location=None,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    p.add_tools(
        HoverTool(
            tooltips=[("queue", "@x"), ("node hours", "@top{0,0.00}")],
            point_policy="snap_to_data",
        )
    )
    p.xaxis.axis_label = "queue"
    p.yaxis.axis_label = "node hours"
    p.vbar(x="x", top="top", source=source, width=0.7)
    p.xgrid.visible = False
    p.xaxis.major_label_orientation = "vertical" if len(queue_names) > 5 else "horizontal"
    return p


def _job_list_histograms(request):
    """Build Bokeh script/div and json_item for job list histograms. Returns (script, div, plot_item)."""
    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics"
        and k
        not in (
            "page",
            "order_by",
            # Histogram grouping/query-only parameters, not model fields:
            "group",
            "metric",
        )
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    job_list_qs = job_data.objects.filter(**acct_data)
    if order_by.lstrip("-") == "has_metrics":
        job_list_qs = job_list_qs.annotate(
            has_metrics=Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
        )
    job_list_qs = job_list_qs.order_by(order_by)

    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.split("_", 1)[0] == "metrics"
    }
    for key, val in cur_metrics.items():
        name, op = key.split("__")
        mquery = {
            "metrics_data__metric": name,
            "metrics_data__value__" + op: val,
        }
        job_list_qs = job_list_qs.filter(**mquery)

    nj = job_list_qs.count()
    df_fields = list(set(name for name, _ in (key.split("__") for key in cur_metrics)))

    if nj == 0:
        gp = _job_list_histograms_empty_figure()
        script, div = components(gp)
        return script, div, json_item(gp), []

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

    jid_dict = {"jid": jids_ordered}
    for name in df_fields:
        jid_to_val = {jid: val for jid, val in metric_dict.get(name, [])}
        jid_dict[name] = [jid_to_val.get(jid, None) for jid in jids_ordered]
    df = DataFrame(jid_dict).set_index("jid")
    hist_metrics = list(hist_metrics_set)
    df = df.join(job_df)
    df["queue_wait"] = (
        to_timedelta(df["start_time"] - df["submit_time"]).dt.total_seconds() / 3600
    )
    df["runtime"] = df["runtime"] / 3600
    # Fixed histograms use actual df column names; display titles mapped for UI
    hist_metrics += [("runtime", "hours"), ("nhosts", "# nodes"), ("queue_wait", "hours")]
    # Keep df numeric for histograms; do not run clean_dataframe here (it would
    # replace NaN with '' and break job_hist). job_hist filters to finite values.
    # Only plot metrics that exist as columns (df has filter metrics + runtime/nhosts/queue_wait)
    hist_metrics = [(m, label) for m, label in hist_metrics if m in df.columns]

    THUMB_WIDTH, THUMB_HEIGHT = 280, 200
    FULL_WIDTH, FULL_HEIGHT = 600, 400

    def _build_grid_plot_list():
        """Build list of figures for the main grid (each figure must be used in only one document)."""
        pl = [
            job_hist(df, metric, label, title=JOB_HIST_DISPLAY_NAMES.get(metric, metric))
            for metric, label in hist_metrics
        ]
        pl = [p for p in pl if p is not None]
        q_full = _job_list_queue_histogram(job_list_qs, width=FULL_WIDTH, height=FULL_HEIGHT)
        if q_full is not None:
            pl.append(q_full)
        q_cpu_full = _job_list_queue_cpu_hours_histogram(
            job_list_qs, width=FULL_WIDTH, height=FULL_HEIGHT
        )
        if q_cpu_full is not None:
            pl.append(q_cpu_full)
        return pl

    def _one_metric_histograms(m, lbl):
        """Build thumb and full job_hist figures for one metric. Returns (display_title, p_thumb, p_full)."""
        display_title = JOB_HIST_DISPLAY_NAMES.get(m, m)
        p_thumb = job_hist(
            df, m, lbl,
            width=THUMB_WIDTH, height=THUMB_HEIGHT,
            title=display_title,
        )
        p_full = job_hist(
            df, m, lbl,
            width=FULL_WIDTH, height=FULL_HEIGHT,
            title=display_title,
        )
        return (display_title, p_thumb, p_full)

    script = ""
    div = ""
    plot_item = None
    histograms = []
    try:
        # Build queue histograms in parallel
        executor = _get_small_executor()
        queue_thumb_f = executor.submit(
            _job_list_queue_histogram,
            job_list_qs, width=THUMB_WIDTH, height=THUMB_HEIGHT,
        )
        queue_full_f = executor.submit(
            _job_list_queue_histogram,
            job_list_qs, width=FULL_WIDTH, height=FULL_HEIGHT,
        )
        queue_cpu_thumb_f = executor.submit(
            _job_list_queue_cpu_hours_histogram,
            job_list_qs, width=THUMB_WIDTH, height=THUMB_HEIGHT,
        )
        queue_cpu_full_f = executor.submit(
            _job_list_queue_cpu_hours_histogram,
            job_list_qs, width=FULL_WIDTH, height=FULL_HEIGHT,
        )
        queue_thumb = queue_thumb_f.result()
        queue_full = queue_full_f.result()
        queue_cpu_thumb = queue_cpu_thumb_f.result()
        queue_cpu_full = queue_cpu_full_f.result()

        if queue_thumb is not None and queue_full is not None:
            histograms.append({
                "title": "Jobs by queue",
                "plot_item_thumb": json_item(queue_thumb),
                "plot_item_full": json_item(queue_full),
            })
        if queue_cpu_thumb is not None and queue_cpu_full is not None:
            histograms.append({
                "title": "Node hours by queue",
                "plot_item_thumb": json_item(queue_cpu_thumb),
                "plot_item_full": json_item(queue_cpu_full),
            })

        plot_list_1 = _build_grid_plot_list()
        if plot_list_1:
            # Build per-metric thumb/full figures in parallel
            metric_hist_by_key = {}
            executor = _get_metric_hist_executor()
            futures = {
                executor.submit(_one_metric_histograms, m, lbl): (m, lbl)
                for m, lbl in hist_metrics
            }
            for fut in as_completed(futures):
                m, lbl = futures[fut]
                try:
                    display_title, p_thumb, p_full = fut.result()
                    if p_thumb is not None and p_full is not None:
                        metric_hist_by_key[(m, lbl)] = (display_title, p_thumb, p_full)
                except Exception:
                    pass
            for metric, label in hist_metrics:
                entry = metric_hist_by_key.get((metric, label))
                if entry is not None:
                    display_title, p_thumb, p_full = entry
                    histograms.append({
                        "title": display_title,
                        "plot_item_thumb": json_item(p_thumb),
                        "plot_item_full": json_item(p_full),
                    })
            # Two separate gridplots: Bokeh models can belong to only one document
            plot_list_2 = _build_grid_plot_list()
            gp1 = gridplot(plot_list_1, ncols=2)
            gp2 = gridplot(plot_list_2, ncols=2)
            script, div = components(gp1)
            plot_item = json_item(gp2)
        else:
            gp1 = _job_list_histograms_empty_figure()
            gp2 = _job_list_histograms_empty_figure()
            script, div = components(gp1)
            plot_item = json_item(gp2)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to generate job list histograms: %s", e, exc_info=True
        )
        gp1 = _job_list_histograms_empty_figure()
        gp2 = _job_list_histograms_empty_figure()
        script, div = components(gp1)
        plot_item = json_item(gp2)
    return script, div, plot_item, histograms


@cache_page(TIMEOUT_MEDIUM)
@api_view(["GET"])
def job_list_histograms(request):
    """
    Return Bokeh histograms for the job list, loaded incrementally.

    This endpoint now supports grouped, per-plot loading instead of building
    all plots at once. The caller must provide a 'group' query parameter:

    - group=queue: return queue-based histograms ("Jobs by queue" and
      "Node hours by queue") as JSON items.
    - group=metric&metric=<name>: return a single metric histogram (thumb and
      full) for the given metric name.

    Example:
      /api/jobs/histograms/?end_time__date=2024-01-01&group=queue
      /api/jobs/histograms/?end_time__date=2024-01-01&group=metric&metric=runtime
    """
    err = _require_auth(request)
    if err is not None:
        return err
    group = (request.GET.get("group") or "").strip()
    if not group:
        return Response(
            {
                "error": "Missing 'group' parameter.",
                "allowed_groups": ["queue", "metric"],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    job_list_qs, nj, fields, cur_metrics = _build_histogram_queryset(request)
    if nj == 0:
        # Preserve a consistent shape even when no jobs match the filter.
        if group == "queue":
            return Response(
                {
                    "group": "queue",
                    "nj": 0,
                    "plots": [],
                }
            )
        if group == "metric":
            metric_name = (request.GET.get("metric") or "").strip()
            return Response(
                {
                    "group": "metric",
                    "metric": metric_name or None,
                    "nj": 0,
                    "plot_item_thumb": None,
                    "plot_item_full": None,
                }
            )
        return Response(
            {
                "error": f"Unknown group '{group}'.",
                "allowed_groups": ["queue", "metric"],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    THUMB_WIDTH, THUMB_HEIGHT = 280, 200
    FULL_WIDTH, FULL_HEIGHT = 600, 400

    if group == "queue":
        # Build queue histograms in parallel, but only for this group.
        executor = _get_small_executor()
        queue_thumb_f = executor.submit(
            _job_list_queue_histogram,
            job_list_qs,
            THUMB_WIDTH,
            THUMB_HEIGHT,
        )
        queue_full_f = executor.submit(
            _job_list_queue_histogram,
            job_list_qs,
            FULL_WIDTH,
            FULL_HEIGHT,
        )
        queue_cpu_thumb_f = executor.submit(
            _job_list_queue_cpu_hours_histogram,
            job_list_qs,
            THUMB_WIDTH,
            THUMB_HEIGHT,
        )
        queue_cpu_full_f = executor.submit(
            _job_list_queue_cpu_hours_histogram,
            job_list_qs,
            FULL_WIDTH,
            FULL_HEIGHT,
        )
        queue_thumb = queue_thumb_f.result()
        queue_full = queue_full_f.result()
        queue_cpu_thumb = queue_cpu_thumb_f.result()
        queue_cpu_full = queue_cpu_full_f.result()

        plots = []
        if queue_thumb is not None and queue_full is not None:
            plots.append(
                {
                    "key": "jobs_by_queue",
                    "title": "Jobs by queue",
                    "plot_item_thumb": json_item(queue_thumb),
                    "plot_item_full": json_item(queue_full),
                }
            )
        if queue_cpu_thumb is not None and queue_cpu_full is not None:
            plots.append(
                {
                    "key": "cpu_hours_by_queue",
                    "title": "Node hours by queue",
                    "plot_item_thumb": json_item(queue_cpu_thumb),
                    "plot_item_full": json_item(queue_cpu_full),
                }
            )
        return Response(
            {
                "group": "queue",
                "nj": nj,
                "plots": plots,
            }
        )

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
        p_thumb = job_hist(
            df,
            metric_name,
            label,
            width=THUMB_WIDTH,
            height=THUMB_HEIGHT,
            title=display_title,
        )
        p_full = job_hist(
            df,
            metric_name,
            label,
            width=FULL_WIDTH,
            height=FULL_HEIGHT,
            title=display_title,
        )

        return Response(
            {
                "group": "metric",
                "metric": metric_name,
                "nj": nj,
                "title": display_title,
                "plot_item_thumb": json_item(p_thumb) if p_thumb is not None else None,
                "plot_item_full": json_item(p_full) if p_full is not None else None,
            }
        )

    return Response(
        {
            "error": f"Unknown group '{group}'.",
            "allowed_groups": ["queue", "metric"],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


@cache_page(TIMEOUT_MEDIUM)
@api_view(["GET"])
def job_list(request):
    """Paginated job list only (histograms via separate job_list_histograms endpoint)."""
    err = _require_auth(request)
    if err is not None:
        return err

    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics"
        and k
        not in (
            "page",
            "order_by",
            # Histogram grouping/query-only parameters, not model fields:
            "group",
            "metric",
        )
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    job_list_qs = job_data.objects.filter(**acct_data).annotate(
        has_metrics=Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
    )
    job_list_qs = job_list_qs.order_by(order_by)

    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.split("_", 1)[0] == "metrics"
    }
    for key, val in cur_metrics.items():
        name, op = key.split("__")
        mquery = {
            "metrics_data__metric": name,
            "metrics_data__value__" + op: val,
        }
        job_list_qs = job_list_qs.filter(**mquery)
    nj = job_list_qs.count()

    if nj == 0:
        return Response(
            {"error": "No data found for this search request"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Aggregate over full filtered list (non-paginated) for listing-page metrics
    # Use node_hrs directly from the database to compute total node hours.
    agg = job_list_qs.aggregate(total_node_hours=Sum("node_hrs"))
    total_node_hours = agg.get("total_node_hours") or 0.0

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

    return Response({
        "job_list": JobListSerializer(page.object_list, many=True).data,
        "nj": nj,
        "aggregates": {
            "total_node_hours": round(total_node_hours, 4),
        },
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


@cache_page(TIMEOUT_SHORT)
@api_view(["GET"])
def job_detail(request, pk):
    """Single job detail: metadata, host_list, fsio, xalt, schema, URLs (plots via separate job_plots endpoint)."""
    err = _require_auth(request)
    if err is not None:
        return err

    job = cached_orm(
        f"{KEY_JOB}:{pk}",
        TIMEOUT_SHORT,
        lambda: job_data.objects.filter(jid=pk)
        .prefetch_related("metrics_data_set")
        .first(),
    )
    if not job:
        return Response(
            {"error": "Job not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not request.session.get("is_staff", False):
        if job.username != request.session.get("username"):
            return Response(
                {"error": "Not allowed to view this job"},
                status=status.HTTP_403_FORBIDDEN,
            )

    j = jid_table.jid_table(job.jid)
    host_list = j.acct_host_list

    def _fetch_gpu():
        gpu_active, gpu_max, gpu_mean = None, None, None
        try:
            gpu_list = cached_orm(
                f"{KEY_GPU_QS}:{job.jid}",
                TIMEOUT_SHORT,
                lambda: list(
                    host_data.objects.filter(
                        jid=job.jid,
                        type="nvidia_gpu",
                        event="utilization",
                    )
                    .values("type", "event", "value")
                    .order_by("time")
                ),
            )
            gpu_data = DataFrame(gpu_list) if gpu_list else DataFrame()
            if not gpu_data.empty and len(gpu_data) > 2:
                gpu_data = gpu_data.iloc[1:-1]
                gpu_max = float(gpu_data["value"].max())
                gpu_mean = float(gpu_data["value"].mean())
                if not isnan(gpu_max):
                    gpu_active = ceil(gpu_max / 100.0)
        except Exception:
            pass
        return (gpu_active, gpu_max, gpu_mean)

    def _fetch_xalt():
        def _xalt_fn():
            xalt_data = xalt_data_c()
            runs = list(
                run.objects.using("xalt")
                .filter(job_id=job.jid)
                .only("exec_path", "cwd", "run_id")
            )
            run_ids = [r.run_id for r in runs]
            joins = list(
                join_run_object.objects.using("xalt")
                .filter(run_id__in=run_ids)
                .only("run_id", "obj_id")
            ) if run_ids else []
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
        return cached_orm(f"{KEY_XALT}:{job.jid}", TIMEOUT_SHORT, _xalt_fn)

    def _fetch_fsio():
        fsio = {}
        try:
            llite_df = j.get_llite_delta_by_event()
            if not llite_df.empty and "delta_sum" in llite_df.columns:
                llite_df = llite_df.copy()
                llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
                read_row = llite_df[llite_df["event"] == "read_bytes"]
                write_row = llite_df[llite_df["event"] == "write_bytes"]
                read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
                write_val = float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
                fsio["llite"] = [read_val, write_val]
        except Exception:
            pass
        return fsio

    def _fetch_schema():
        try:
            return j.schema
        except Exception:
            return {}

    def _fetch_proc_list():
        from .models import proc_data
        return cached_orm(
            f"{KEY_PROC_LIST}:{job.jid}",
            TIMEOUT_SHORT,
            lambda: list(
                proc_data.objects.filter(jid=job.jid)
                .values_list("proc", flat=True)
                .distinct()
            ),
        )

    gpu_active = gpu_utilization_max = gpu_utilization_mean = None
    xalt_payload = None
    fsio = {}
    schema = {}
    proc_list = []

    tasks = [
        ("gpu", _fetch_gpu),
        ("fsio", _fetch_fsio),
        ("schema", _fetch_schema),
        ("proc_list", _fetch_proc_list),
    ]
    if cfg.get_xalt_user() != "":
        tasks.append(("xalt", _fetch_xalt))

    executor = _get_small_executor()
    future_to_key = {executor.submit(fn): key for key, fn in tasks}
    for future in as_completed(future_to_key):
        key = future_to_key[future]
        try:
            result = future.result()
            if key == "gpu":
                gpu_active, gpu_utilization_max, gpu_utilization_mean = result
            elif key == "xalt":
                xalt_payload = result
            elif key == "fsio":
                fsio = result
            elif key == "schema":
                schema = result
            elif key == "proc_list":
                proc_list = result or []
        except Exception:
            pass

    xalt_data = {
        "exec_path": xalt_payload["exec_path"] if xalt_payload else [],
        "cwd": xalt_payload["cwd"] if xalt_payload else [],
        "libset": xalt_payload["libset"] if xalt_payload else [],
    }

    urlstring = "https://scribe.tacc.utexas.edu/en-US/app/search/search?q=search%20"
    hoststring = urlstring + "%20host%3D" + host_list[0] + cfg.get_host_name_ext()
    serverstring = urlstring + "%20mds*%20OR%20%20oss*"
    for host in host_list[1:]:
        hoststring += "%20OR%20%20host%3D" + host + "*"
    hoststring += "&earliest=" + str(j.start_time) + "&latest=" + str(j.end_time) + "&display.prefs.events.count=50"
    serverstring += "&earliest=" + str(j.start_time) + "&latest=" + str(j.end_time) + "&display.prefs.events.count=50"

    metrics_list = [
        {"type": o.type, "metric": o.metric, "units": o.units, "value": o.value}
        for o in job.metrics_data_set.all()
    ]

    return Response({
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
        "metrics_list": metrics_list,
        "proc_list": proc_list,
    })


@cache_page(TIMEOUT_SHORT)
@api_view(["GET"])
def job_plots(request, pk):
    """
    Job-level plots grouped by shared jid_table input.

    Returns Bokeh json_items and availability reasons for:
    - Summary plot
    - Heatmap
    - Roofline
    """
    err = _require_auth(request)
    if err is not None:
        return err

    job = cached_orm(
        f"{KEY_JOB}:{pk}",
        TIMEOUT_SHORT,
        lambda: job_data.objects.filter(jid=pk)
        .prefetch_related("metrics_data_set")
        .first(),
    )
    if not job:
        return Response(
            {"error": "Job not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    j = jid_table.jid_table(job.jid)

    def _fetch_summary_plot():
        mplot_item, reason = None, None
        try:
            plot_json = plots.SummaryPlot(j).plot()
            mplot_item = json_item(plot_json)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to generate summary plot for jid %s: %s", job.jid, e, exc_info=True
            )
            reason = str(e)
        return (mplot_item, reason)

    def _fetch_heatmap():
        hplot_item, reason = None, None
        try:
            hm_fig_json = plots.plot_from_jid_table(j)
            if hm_fig_json is not None:
                hplot_item = json_item(hm_fig_json)
            else:
                reason = plots.MSG_NO_HOST_MSR_DATA
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to generate heatmap for jid %s: %s", job.jid, e, exc_info=True
            )
            reason = str(e)
        return (hplot_item, reason)

    def _fetch_roofline():
        rplot_item, reason = None, None
        try:
            roof_fig_json = plots.plot_roofline_from_jid_table(j)
            if roof_fig_json is not None:
                rplot_item = json_item(roof_fig_json)
            else:
                reason = plots.MSG_NO_ROOFLINE_DATA
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to generate roofline for jid %s: %s", job.jid, e, exc_info=True
            )
            reason = str(e)
        return (rplot_item, reason)

    mplot_item = hplot_item = rplot_item = None
    mplot_unavailable_reason = hplot_unavailable_reason = rplot_unavailable_reason = None

    tasks = [
        ("summary_plot", _fetch_summary_plot),
        ("heatmap", _fetch_heatmap),
        ("roofline", _fetch_roofline),
    ]
    executor = _get_small_executor()
    future_to_key = {executor.submit(fn): key for key, fn in tasks}
    for future in as_completed(future_to_key):
        key = future_to_key[future]
        try:
            result = future.result()
            if key == "summary_plot":
                mplot_item, mplot_unavailable_reason = result
            elif key == "heatmap":
                hplot_item, hplot_unavailable_reason = result
            elif key == "roofline":
                rplot_item, rplot_unavailable_reason = result
        except Exception:
            pass

    return Response(
        {
            "mscript": "",
            "mdiv": "",
            "mplot_item": mplot_item,
            "mplot_unavailable_reason": mplot_unavailable_reason,
            "hscript": "",
            "hdiv": "",
            "hplot_item": hplot_item,
            "hplot_unavailable_reason": hplot_unavailable_reason,
            "rscript": "",
            "rdiv": "",
            "rplot_item": rplot_item,
            "rplot_unavailable_reason": rplot_unavailable_reason,
        }
    )


@cache_page(TIMEOUT_SHORT)
@api_view(["GET"])
def type_detail(request, jid, type_name):
    """Type detail: Bokeh tscript/tdiv, stats_data, schema."""
    err = _require_auth(request)
    if err is not None:
        return err

    job = cached_orm(
        f"{KEY_JOB}:{jid}",
        TIMEOUT_SHORT,
        lambda: job_data.objects.filter(jid=jid)
        .only("host_list", "start_time", "end_time")
        .first(),
    )
    if not job:
        return Response({"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND)

    acct_host_list = [h + "." + cfg.get_host_name_ext() for h in (job.host_list or [])]
    start_time = job.start_time
    end_time = job.end_time
    if start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time, dt_timezone.utc)
    if end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time, dt_timezone.utc)
    start_time = start_time.astimezone(local_timezone)
    end_time = end_time.astimezone(local_timezone)

    from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider
    provider = TypeDetailDataProvider(jid, type_name, start_time, end_time, acct_host_list)

    def _type_hosts_fn():
        return list(
            host_data.objects.filter(
                jid=jid,
                type=type_name,
                time__gte=start_time,
                time__lte=end_time,
                host__in=acct_host_list,
            )
            .values_list("host", flat=True)
            .distinct()
        )

    _st = start_time.isoformat() if start_time else ""
    _et = end_time.isoformat() if end_time else ""
    data_host_list = cached_orm(
        f"{KEY_TYPE_DETAIL_HOSTS}:{jid}:{type_name}:{_st}:{_et}",
        TIMEOUT_SHORT,
        _type_hosts_fn,
    )
    if len(data_host_list) == 0:
        return Response({
            "type_name": type_name,
            "jobid": jid,
            "tscript": "",
            "tdiv": "",
            "tplot_item": None,
            "stats_data": [],
            "schema": [],
        })

    sp = plots.DevPlot(provider, data_host_list)
    df, plot_comp = sp.plot()
    _, plot_json = sp.plot()
    tscript, tdiv = components(plot_comp)
    tplot_item = json_item(plot_json)
    schema = [
        c for c in df.columns
        if c not in ("host", "time", "index")
    ] if not df.empty else []

    stats_data = []
    if not df.empty and "time" in df.columns and len(df) > 0 and schema:
        df = df.copy()
        df["dt"] = df["time"].sub(df["time"].iloc[0]).astype("timedelta64[s]")
        df1 = df.groupby("dt")[schema].mean().reset_index()
        for t in range(len(df1)):
            vals = df1.loc[df1.index[t], schema].values.flatten().tolist()
            vals = [float(x) if hasattr(x, "__float__") else x for x in vals]
            stats_data.append([str(df1["dt"].iloc[t]), vals])

    return Response({
        "type_name": type_name,
        "jobid": jid,
        "tscript": tscript,
        "tdiv": tdiv,
        "tplot_item": tplot_item,
        "stats_data": stats_data,
        "schema": schema,
    })


@cache_page(TIMEOUT_SHORT)
@api_view(["GET"])
def host_plot(request):
    """Return Bokeh plot_item for a single host and time range (GET host, end_time__gte, end_time__lte)."""
    err = _require_auth(request)
    if err is not None:
        return err

    host_fqdn = (request.GET.get("host") or "").strip()
    start_time = request.GET.get("end_time__gte", "").strip()
    end_time = (request.GET.get("end_time__lte") or "now()").strip()

    if not host_fqdn or not start_time:
        return Response(
            {"error": "Missing host or end_time__gte"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from datetime import datetime
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
    plot_item = cached_orm(cache_key, TIMEOUT_SHORT, _host_plot_fn)

    return Response({
        "host": host_fqdn,
        "plot_item": plot_item,
        "end_time__gte": start_dt.isoformat(),
        "end_time__lte": end_dt.isoformat(),
    })


@api_view(["GET"])
def admin_monitor(request):
    """Staff-only: HPCPerfStats Monitor data (host timestamps, cache/Redis, RabbitMQ, TimescaleDB stats).

    Supports a lightweight, per-section API via the optional 'section' query param:
    - ?section=hosts      -> {"host_stats": [...]}
    - ?section=cache      -> {"cache_stats": {...}}
    - ?section=rabbitmq   -> {"rabbitmq_stats": {...}}
    - ?section=timescaledb -> {"timescaledb_stats": {...}}
    - omitted/other       -> {"host_stats": [...], "cache_stats": {...}, "rabbitmq_stats": {...}, "timescaledb_stats": {...}}
    """
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    def _all_hosts_fn():
        qs = job_data.objects.distinct("host_list").values_list("host_list", flat=True)
        flat = [host for sublist in qs for host in sublist]
        return list(set(flat))

    all_hosts = cached_orm(KEY_ALL_HOSTS, TIMEOUT_MEDIUM, _all_hosts_fn)
    all_hosts = [h for h in all_hosts if not (str(h) or "").startswith("None")]
    all_hosts = sorted(all_hosts)

    def _host_stats_fn():
        now = timezone.now()
        time_bounds = now - timedelta(days=8)

        latest_qs = (
            host_data.objects.filter(time__gte=time_bounds)
            .values("host")
            .annotate(last_time=Max("time"))
        )
        latest_by_host = {row["host"]: row["last_time"] for row in latest_qs}

        host_stats_local = []
        for host in all_hosts:
            last_time = latest_by_host.get(host)
            if last_time is None:
                host_stats_local.append(
                    {"host": host, "last_time": None, "age_bucket": "gt_week"}
                )
                continue
            age = now - last_time
            if age > timedelta(weeks=1):
                bucket = "gt_week"
            elif age > timedelta(days=1):
                bucket = "gt_day"
            elif age > timedelta(hours=1):
                bucket = "gt_hour"
            elif age > timedelta(minutes=10):
                bucket = "gt_10min"
            else:
                bucket = "ok"
            host_stats_local.append(
                {
                    "host": host,
                    "last_time": last_time.isoformat() if last_time else None,
                    "age_bucket": bucket,
                }
            )
        return host_stats_local

    host_stats = cached_orm(KEY_ADMIN_HOST_STATS, TIMEOUT_ADMIN_STATS, _host_stats_fn)

    cache_stats = _get_cache_stats()
    rabbitmq_stats = _get_rabbitmq_stats()
    timescaledb_stats = _get_timescaledb_stats()

    section = (request.GET.get("section") or "").strip().lower()
    if section == "hosts":
        return Response({"host_stats": host_stats})
    if section == "cache":
        return Response({"cache_stats": cache_stats})
    if section == "rabbitmq":
        return Response({"rabbitmq_stats": rabbitmq_stats})
    if section == "timescaledb":
        return Response({"timescaledb_stats": timescaledb_stats})
    return Response(
        {
            "host_stats": host_stats,
            "cache_stats": cache_stats,
            "rabbitmq_stats": rabbitmq_stats,
            "timescaledb_stats": timescaledb_stats,
        }
    )


@cache_page(TIMEOUT_SHORT)
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
    """
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Parse and clamp days window from query params.
    try:
        days_param = int(request.GET.get("days", "") or 30)
    except (TypeError, ValueError):
        days_param = 30
    window_days = max(1, min(days_param, 365))
    now = dj_timezone_utils.now()
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
        rows.append(
            {
                "username": row.get("username") or "",
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


@api_view(["POST"])
def sacct_ingest(request):
    """Ingest pipe-delimited sacct output into job_data using sync_acct logic.

    Requires authentication (API key or session) and staff. Request body must be
    raw pipe-delimited sacct output (same format as sacct -P -o ...). Query
    param date=YYYY-MM-DD is required (the date of the data being ingested) to
    compute which jobs are already in the DB.
    """
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

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
