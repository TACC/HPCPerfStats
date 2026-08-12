"""
Django REST Framework API views for machine app. All data via JSON for React
SPA.

Attributes:
  HOST_PLOT_MAX_WINDOW_DAYS: Attribute.
  HOST_PLOT_STATEMENT_TIMEOUT_MS: Attribute.
  JOB_HIST_DISPLAY_NAMES: Attribute.
  JOB_LIST_HISTOGRAM_BATCH_METRICS_DEFAULT: Attribute.
  JOB_LIST_HISTOGRAM_NO_JOBS_REASON: Attribute.
  _DETAIL_GPU_METRIC_NAMES: Attribute.
  _JOB_LIST_API_CACHE_PATHS: Attribute.
  _JOB_LIST_MACHINE_BROWSE_PREFIXES: Attribute.
  _JOB_LIST_METRIC_FILTER_OPS_ALLOWED: Attribute.
  _JOB_LIST_QUERY_FIELD_EXCLUDES_BASE: Attribute.
  _JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM: Attribute.
  _JOB_PLOT_INFLIGHT_TTL_SECONDS: Attribute.
  _job_plot_inflight: Attribute.
  _job_plots_lock: Attribute.
  _small_executor: Attribute.
  logger: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone as dt_timezone

import hpcperfstats.dbload.lib.conf_parser as cfg
from bokeh.embed import json_item
from django.utils import timezone
from pandas import DataFrame
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, throttle_classes
from rest_framework.response import Response

from django.conf import settings
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.cache import cache
from django.db import connection, close_old_connections, transaction
from django.test import RequestFactory
from django.utils.cache import (
    _generate_cache_header_key,
    _generate_cache_key,
    get_cache_key,
)
import urllib.parse

logger = logging.getLogger(__name__)


class _JSONResponse(Response):
    """
    Response subclass with a json() helper for unit tests.
    
    Django's test client adds a json() method on response objects, but when
    calling views directly (as these tests do) we get the raw DRF Response.
    Providing json() keeps tests readable without changing production behavior.
    """

    def json(self) -> Any:
        """
        Return the JSON-serializable payload.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _JSONResponse().json()  # doctest: +SKIP
        """
        return self.data

from .bokeh_plot_layout import _apply_zoom_layout_to_json_item
from .bokeh_embed import new_spa_embedded_figure
from .cache_middleware import dynamic_cache_page
from .openapi_schema import (
    ADMIN_MONITOR_SCHEMA,
    DROP_STAFF_SCHEMA,
    HOME_OPTIONS_SCHEMA,
    HOST_PLOT_SCHEMA,
    INVALIDATE_CACHE_SCHEMA,
    JOB_DETAIL_SCHEMA,
    JOB_LIST_HISTOGRAMS_BATCH_SCHEMA,
    JOB_LIST_HISTOGRAMS_SCHEMA,
    JOB_LIST_FILTER_OPTIONS_SCHEMA,
    JOB_LIST_SCHEMA,
    JOB_MONITOR_GPU_SCHEMA,
    JOB_MONITOR_SCHEMA,
    JOB_PLOTS_SCHEMA,
    SACCT_INGEST_SCHEMA,
    SESSION_SCHEMA,
    TYPE_DETAIL_SCHEMA,
    USER_API_KEY_ROTATE_SCHEMA,
    USER_API_KEY_SCHEMA,
)
from .cache_utils import (
    KEY_ADMIN_CACHE_STATS,
    KEY_ADMIN_RMQ_STATS,
    KEY_ADMIN_RMQ_SNAPSHOT,
    KEY_ADMIN_TIMESCALE_STATS,
    KEY_ADMIN_HOST_STATS,
    KEY_ADMIN_XALT_STATS,
    KEY_ADMIN_TELEMETRY_HEALTH,
    KEY_DATES,
    KEY_METRICS_DISTINCT,
    KEY_QUEUES,
    KEY_STATES,
    KEY_GPU_AGG,
    KEY_GPU_COUNT,
    KEY_XALT,
    KEY_PROC_LIST,
    KEY_HOST_PLOT,
    cached_orm,
    cached_non_staff_visible_accounts,
    ensure_job_metrics_data_prefetched,
    get_site_content_cache_timeout,
    make_job_detail_cache_key,
    invalidate_home_options_query_cache,
    make_cache_key,
    register_job_plot_cache_key,
    TIMEOUT_ADMIN_STATS,
)
from .job_plot_artifacts import (
    JOB_PLOT_JSON_KEYS,
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_NORMAL,
    JOB_PLOT_LAYOUT_ZOOM_V3,
    compute_plot_input_fingerprint,
    load_cached_job_plot_entry,
)
from .job_detail_artifacts import (
    ARTIFACT_KIND_JOB_DETAIL,
    ARTIFACT_KIND_MULTIPRECISION_MIX,
    ARTIFACT_KIND_TYPE_DETAIL,
    compute_detail_input_fingerprint,
    load_job_detail_artifact,
)
from hpcperfstats.analysis.metrics.lib.metrics import (
    build_job_metrics_display_list,
    job_metrics_catalog_entries,
)
from hpcperfstats.dbload.sync_acct import (
    AccountingFileShrinkError,
    persist_accounting_daily_file,
    sync_acct_from_content,
)
from .models import ApiKey, job_data, metrics_data
from .host_data_latest import (
    format_host_data_newest_iso,
    latest_sample_time_by_host,
    latest_sample_time_by_host_in_window,
)
from .admin_monitor_telemetry_health import compute_telemetry_health
from .oauth2 import check_for_tokens
from .throttles import ExpensiveReadThrottle, StaffIngestThrottle
from .query_utils import (
    apply_job_list_header_acct_multi_filters,
    coerce_job_list_datetime_bounds,
    expand_month_date_to_range,
    get_job_list_order_by,
    normalize_job_list_query_params,
    parse_job_list_performance_sort_ranks,
    partition_job_list_acct_filters,
)
from .job_list_filter_summary import build_job_list_qname_and_filter_summary
from .job_list_filter_options import build_job_list_filter_options
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


def site_response_cache_timeout(request: Any) -> Any:
    """
    Per-request TTL for @dynamic_cache_page (site-aware).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> site_response_cache_timeout(None)  # doctest: +SKIP
    """
    return get_site_content_cache_timeout()

_JOB_LIST_QUERY_FIELD_EXCLUDES_BASE = ("page", "order_by", "performance_sort_rank", "state")
_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM = ("group", "metric", "metrics", "_histogram_embed_v")
_JOB_LIST_METRIC_FILTER_OPS_ALLOWED = frozenset({"gte", "lte"})
JOB_LIST_HISTOGRAM_BATCH_METRICS_DEFAULT = ("runtime", "nhosts", "queue_wait")
HOST_PLOT_MAX_WINDOW_DAYS = 7
# Longer than default portal statement_timeout for rare large host windows.
HOST_PLOT_STATEMENT_TIMEOUT_MS = 300000


@contextmanager
def _pg_host_plot_statement_timeout(
  timeout_ms: int = HOST_PLOT_STATEMENT_TIMEOUT_MS,
) -> Iterator[Any]:
  """
  Raise PostgreSQL session statement_timeout for host_plot host_data work.

  Args:
    timeout_ms (int): Statement timeout in milliseconds for the LOCAL SET.

  Yields:
    Iterator[Any]: Open transaction with LOCAL statement_timeout set.

  Examples:
    >>> with _pg_host_plot_statement_timeout():  # doctest: +SKIP
    ...     pass
  """
  if connection.vendor != "postgresql":
    yield
    return
  with transaction.atomic():
    with connection.cursor() as cursor:
      cursor.execute("SET LOCAL statement_timeout = %s", [int(timeout_ms)])
    yield


def _get_admin_host_stats_statement_timeout_ms() -> Any:
    """
    Statement timeout for Admin Monitor 3h GROUP BY fallback only.
    
    Primary path uses Redis inventory + LATERAL LIMIT 1 (no long timeout).
    Never floor at 600s — that matched the old 8-day GroupAggregate hang.
    
    Returns:
      Any: Open return polymorphism from
      ``_get_admin_host_stats_statement_timeout_ms``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> _get_admin_host_stats_statement_timeout_ms()  # doctest: +SKIP
    """
    try:
        default_ms = int(cfg.get_db_statement_timeout_ms())
    except Exception:
        default_ms = 60000
    # Bound the last-resort 3h aggregate: at least 45s, at most 60s.
    return min(max(default_ms, 45000), 60000)


@contextmanager
def _pg_session_statement_timeout_for_admin_host_stats_query() -> Iterator[Any]:
    """
    Increase statement timeout only while evaluating admin host stats query.
    
    Yields:
      Iterator[Any]: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _pg_session_statement_timeout_for_admin_host_stats_query()
    """
    if connection.vendor != "postgresql":
        yield
        return

    timeout_ms = _get_admin_host_stats_statement_timeout_ms()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
        yield


def _collect_future_results_with_deadline(
  future_to_key: Any,
  max_wait_seconds: int,
) -> Any:
    """
    Collect completed future results until `max_wait_seconds` elapses.
    
    Args:
      future_to_key (Any): Future to key passed to this helper.
      max_wait_seconds (int): Integer value for max wait seconds.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _collect_future_results_with_deadline(None, 0)  # doctest: +SKIP
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


def _evict_stale_inflight_plot_tasks() -> None:
    """
    Remove stale in-flight plot tasks to bound map growth.
    
    Do not evict entries solely because ``future.done()`` is true: ``job_plots``
    must run ``_finalize_job_plot_future`` first to persist results to the
      cache.
    Otherwise a plot that finishes after the previous request returns 202 can be
    dropped before the client polls again.
    
    Returns:
      None
    
    Examples:
      >>> _evict_stale_inflight_plot_tasks()  # doctest: +SKIP
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

def _get_small_executor() -> Any:
    """
    Internal helper to return the small executor.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _get_small_executor()  # doctest: +SKIP
    """
    global _small_executor
    if _small_executor is None:
        _small_executor = ThreadPoolExecutor(
            max_workers=cfg.get_api_small_executor_max_workers()
        )
    return _small_executor


def _gpu_agg_rows_for_job(j: Any) -> Any:
    """
    host_data GPU util rows for job window (test / non-request helper only).

    Must not be wired to user-facing Job Detail (artifact-only GPU path).

    Args:
      j (Any): Job record (Django ``job_data`` or job-like mapping).

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> _gpu_agg_rows_for_job(None)  # doctest: +SKIP
    """
    from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
        gpu_agg_rows_for_job_window,
    )

    return gpu_agg_rows_for_job_window(j)


_DETAIL_GPU_METRIC_NAMES = (
    "detail_gpu_active",
    "detail_gpu_util_max",
    "detail_gpu_util_mean",
    "detail_gpu_count",
)


def _gpu_detail_tuple_from_metrics(job: Any) -> Any:
    """
    Return (active, max%, mean%, count) from metrics_data if all four rows.
    
      exist.
    
      with values.
    
    Jobs not yet processed by update_metrics return ``None`` (Job Detail
    uses artifacts, not a host_data fallback).
    
    Args:
      job (Any): Job record (Django ``job_data`` or job-like mapping).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _gpu_detail_tuple_from_metrics(None)  # doctest: +SKIP
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


def _fsio_dict_from_metrics(job: Any) -> Any:
    """
    Return job_detail-shaped fsio dict from metrics_data (dual NFS+Lustre).
    
    Args:
      job (Any): Job record (Django ``job_data`` or job-like mapping).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _fsio_dict_from_metrics(None)  # doctest: +SKIP
    """
    by_m = {o.metric: o for o in job.metrics_data_set.all()}

    def _val(metric: Any) -> Any:
        """
        Internal helper to handle val.
        
        Args:
          metric (Any): Metric passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _val(None)  # doctest: +SKIP
        """
        row = by_m.get(metric)
        if row is None or row.value is None:
            return None
        return float(row.value)

    out = {}
    lr = _val("detail_fsio_llite_read_mb")
    lw = _val("detail_fsio_llite_write_mb")
    if lr is not None and lw is not None:
        out["llite"] = [
            lr,
            lw,
            _val("detail_fsio_llite_peak_mb_s"),
            _val("detail_fsio_llite_peak_iops"),
        ]
    nr = _val("detail_fsio_nfs_read_mb")
    nw = _val("detail_fsio_nfs_write_mb")
    if nr is not None and nw is not None:
        out["nfs"] = [
            nr,
            nw,
            _val("detail_fsio_nfs_peak_mb_s"),
            _val("detail_fsio_nfs_peak_iops"),
        ]
    return out or None


def _compute_job_gpu_stats(
  job: Any,
  j: Any,
  job_cache_timeout: int,
  include_gpu_count: bool = True,
) -> Any:
    """
    Compute per-job GPU stats from host_data (test / non-request helper only).

    Must not be wired to user-facing Job Detail (artifact-only GPU path).

    Args:
      job (Any): Job record (Django ``job_data`` or job-like mapping).
      j (Any): Job record (Django ``job_data`` or job-like mapping).
      job_cache_timeout (int): Integer value for job cache timeout.
      include_gpu_count (bool): Boolean flag for include gpu count.

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> _compute_job_gpu_stats(None, None, 0, True)  # doctest: +SKIP
    """
    from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
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
    Exists,
    IntegerField,
    OuterRef,
    Sum,
    Q,
    F,
    FloatField,
    When,
    ExpressionWrapper,
)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from hpcperfstats.analysis.metrics.lib.gen.jid_table import HostDataProvider
import hpcperfstats.analysis.metrics.lib.plot as plots
from hpcperfstats.site.xalt.models import join_run_object, lib, run


def _age_bucket(age: timedelta) -> str:
    """
    Map last-seen age to admin monitor bucket labels (Redis + DB host stats).
    
    Args:
      age (timedelta): Age.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _age_bucket(None)  # doctest: +SKIP
    """
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


def _admin_monitor_host_stat_dict(host: Any, last_time: Any, now: Any) -> Any:
    """
    Build one ``host_stats`` row for admin monitor, or ``None`` to skip this.
    
      host.
    
    Args:
      host (Any): Host passed to this helper.
      last_time (Any): Last time passed to this helper.
      now (Any): Now passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _admin_monitor_host_stat_dict(None, None, None)  # doctest: +SKIP
    """
    host = host or ""
    if not host or last_time is None or "." not in host:
        return None
    age = now - last_time
    return {
        "host": host,
        "last_time": last_time.isoformat() if last_time else None,
        "age_bucket": _age_bucket(age),
    }


def _format_log_timestamp(ts: Any) -> Any:
    """
    Format a datetime for client/server log URLs.
    
    Desired format: %Y-%m-%dT%H:%M:%S%:z
    Python's strftime does not support %:z directly, so we build the offset
    manually while preserving any existing tzinfo.
    
    Args:
      ts (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _format_log_timestamp(None)  # doctest: +SKIP
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


def _get_api_key_from_request(request: Any) -> Any:
    """
    Extract API key from Authorization or X-API-Key headers.
    
    Supported formats:
    - Authorization: Api-Key <key>
    - X-API-Key header
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _get_api_key_from_request(None)  # doctest: +SKIP
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


def _api_key_valid(key: str) -> Any:
    """
    Return ApiKey instance if key is valid and active, else None.
    
    Args:
      key (str): String for key.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _api_key_valid("x")  # doctest: +SKIP
    """
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


def _require_auth(request: Any) -> Any:
    """
    Return 401 JSON if not authenticated via OAuth2 session or API key.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _require_auth(None)  # doctest: +SKIP
    """
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


def _require_staff(request: Any) -> Any:
    """
    Return 401/403 Response if not authenticated or not staff; else None.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _require_staff(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _require_csrf_for_session_post(request: Any) -> Any:
    """
    Return 403 JSON when a browser session POST lacks X-CSRFToken.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _require_csrf_for_session_post(None)  # doctest: +SKIP
    """
    django_request = getattr(request, "_request", request)
    if django_request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    # CLI/API-key clients (e.g. hpcperfstats-tools sacct_gen) are not browser
    # sessions; CSRF does not apply when a valid API key is presented.
    api_key = _get_api_key_from_request(request)
    if api_key and _api_key_valid(api_key) is not None:
        return None
    if not django_request.META.get("HTTP_X_CSRFTOKEN"):
        return Response(
            {"detail": "CSRF token missing"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _apply_non_staff_job_visibility(queryset: Any, request: Any) -> Any:
    """
    Restrict non-staff visibility to own jobs and jobs in own-used accounts.
    
    Args:
      queryset (Any): Queryset passed to this helper.
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _apply_non_staff_job_visibility(None, None)  # doctest: +SKIP
    """
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

    site_ttl = get_site_content_cache_timeout()
    account_list = cached_non_staff_visible_accounts(username, site_ttl)
    if account_list:
        return queryset.filter(Q(username=username) | Q(account__in=account_list))
    return queryset.filter(username=username)


def _get_visible_job_or_error_response(
  request: Any,
  pk: Any,
  queryset_builder: Any,
) -> Any:
    """
    Return (job, None) if visible, else (None, Response).
    
    Args:
      request (Any): Request passed to this helper.
      pk (Any): Pk passed to this helper.
      queryset_builder (Any): Queryset builder passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _get_visible_job_or_error_response(None, None, None)  # doctest: +SKIP
    """
    site_ttl = get_site_content_cache_timeout()
    job = cached_orm(
        make_job_detail_cache_key(pk),
        site_ttl,
        queryset_builder,
    )
    if not job:
        return None, Response(
            {"error": "Job not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    ensure_job_metrics_data_prefetched(job)
    if not _apply_non_staff_job_visibility(job_data.objects.filter(jid=pk), request).exists():
        return None, Response(
            {"error": "Not allowed to view this job"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return job, None


def _job_times_as_local(start_time: Any, end_time: Any) -> Any:
    """
    Return start/end as timezone-aware datetimes in local timezone.
    
    Args:
      start_time (Any): Start time passed to this helper.
      end_time (Any): End time passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _job_times_as_local(None, None)  # doctest: +SKIP
    """
    if start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time, dt_timezone.utc)
    if end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time, dt_timezone.utc)
    return start_time.astimezone(local_timezone), end_time.astimezone(local_timezone)


def _apply_job_list_metric_filters(queryset: Any, cur_metrics: Any) -> Any:
    """
    Apply derived-metric filters without JOIN row inflation (EXISTS per metric).
    
    Args:
      queryset (Any): Queryset passed to this helper.
      cur_metrics (Any): Cur metrics passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _apply_job_list_metric_filters(None, None)  # doctest: +SKIP
    """
    for key, val in cur_metrics.items():
        if "__" not in key:
            logger.warning("Ignoring malformed metrics filter key %r", key)
            continue
        name, op = key.split("__", 1)
        if not name or not op:
            logger.warning("Ignoring malformed metrics filter key %r", key)
            continue
        if op not in _JOB_LIST_METRIC_FILTER_OPS_ALLOWED:
            logger.warning("Ignoring unsupported metrics filter key %r", key)
            continue
        metric_match = metrics_data.objects.filter(
            jid_id=OuterRef("jid"),
            metric=name,
            **{f"value__{op}": val},
        )
        queryset = queryset.filter(Exists(metric_match))
    return queryset


def _apply_job_list_performance_sort_rank_filter(
  queryset: Any,
  fields: Any,
) -> Any:
    """
    Filter annotated queryset by comma-separated performance_sort_rank values.
    
    Shared-label ranks 2–4 (Too few samples to complete) expand so selecting
    any one matches all three designations.
    
    Args:
      queryset (Any): Queryset passed to this helper.
      fields (Any): Fields passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _apply_job_list_performance_sort_rank_filter(None, None)
    """
    from hpcperfstats.site.lib.machine.job_list_performance import (
        expand_performance_sort_ranks_for_filter,
    )

    raw = (fields or {}).get("performance_sort_rank")
    if not raw:
        return queryset
    ranks = expand_performance_sort_ranks_for_filter(
        parse_job_list_performance_sort_ranks(raw)
    )
    if not ranks:
        return queryset
    if len(ranks) == 1:
        return queryset.filter(performance_sort_rank=ranks[0])
    return queryset.filter(performance_sort_rank__in=ranks)


def _apply_job_list_major_state_filter(queryset: Any, fields: Any) -> Any:
    """
    Filter queryset by comma-separated major terminal state group keys.
    
    Args:
      queryset (Any): Queryset passed to this helper.
      fields (Any): Fields passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _apply_job_list_major_state_filter(None, None)  # doctest: +SKIP
    """
    from .job_list_state_groups import major_state_q, parse_major_state_filter_keys

    keys = parse_major_state_filter_keys((fields or {}).get("state"))
    if not keys:
        return queryset
    return queryset.filter(major_state_q(keys))


def _build_job_list_queryset_from_request(
  request: Any,
  extra_excluded_fields: tuple[Any, ...] = (),
  annotate_all: bool = False,
  exclude_header_dimension: Any | None = None,
) -> Any:
    """
    Build filtered ordered queryset and parsed filter maps for job list.
    
      endpoints.
    
    Args:
      request (Any): Request passed to this helper.
      extra_excluded_fields (tuple[Any, ...]): Sequence for extra excluded
      fields.
      annotate_all (bool): Boolean flag for annotate all.
      exclude_header_dimension (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_job_list_queryset_from_request(None, [], True, None)
    """
    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    if exclude_header_dimension:
        fields = dict(fields)
        fields.pop(exclude_header_dimension, None)
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)
    excluded_fields = set(_JOB_LIST_QUERY_FIELD_EXCLUDES_BASE) | set(extra_excluded_fields)
    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics" and k not in excluded_fields
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    acct_data, header_multi_kwargs = apply_job_list_header_acct_multi_filters(acct_data)
    acct_kwargs, host_val = partition_job_list_acct_filters(acct_data)
    acct_kwargs = coerce_job_list_datetime_bounds(acct_kwargs)
    queryset = job_data.objects.filter(**acct_kwargs, **header_multi_kwargs)
    if host_val:
        queryset = queryset.filter(host_list__contains=[host_val])
    queryset = _apply_non_staff_job_visibility(queryset, request)
    if annotate_all or order_by.lstrip("-") == "performance_sort_rank":
        queryset = annotate_job_list_performance_fields(queryset)
    queryset = _apply_job_list_performance_sort_rank_filter(queryset, fields)
    queryset = _apply_job_list_major_state_filter(queryset, fields)
    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.startswith("metrics_")
    }
    queryset = _apply_job_list_metric_filters(queryset, cur_metrics)
    if order_by.lstrip("-") == "metrics_distinct_time_count":
        # Keep blank sample counts at the end for both sort directions so
        # "largest sample count first" behaves as users expect.
        sample_count_order = (
            F("metrics_distinct_time_count").desc(nulls_last=True)
            if order_by.startswith("-")
            else F("metrics_distinct_time_count").asc(nulls_last=True)
        )
        queryset = queryset.order_by(sample_count_order)
    elif order_by.lstrip("-") == "performance_sort_rank":
        # Designation ranks 1 and 4 share performance_sort_group; jid is a stable
        # secondary so they intermix as one bucket rather than sub-group by rank.
        group_order = (
            F("performance_sort_group").desc()
            if order_by.startswith("-")
            else F("performance_sort_group").asc()
        )
        queryset = queryset.order_by(group_order, "jid")
    else:
        queryset = queryset.order_by(order_by)
    return queryset, fields, cur_metrics, order_by


def _get_redis_cache_client() -> Any:
    """
    Best-effort unwrap of a redis-py client from Django's cache backend.
    
    Returns:
      Any: Open return polymorphism from ``_get_redis_cache_client``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _get_redis_cache_client()  # doctest: +SKIP
    """
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


def _full_page_cache_url_digests_for_request_paths(
  request: Any,
  paths: Any,
) -> Any:
    """
    Return MD5 hex digests of absolute URIs used in Django ``cache_page`` keys.
    
    Matches ``django.utils.cache._generate_cache_key`` / ``get_cache_key`` URL
    hashing (``md5(request.build_absolute_uri().encode("ascii"))``).
    
    Args:
      request (Any): Request passed to this helper.
      paths (Any): Iterable of filesystem paths as strings.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _full_page_cache_url_digests_for_request_paths(None, None)
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


def _delete_django_cache_page_entries_for_request(
  request: Any,
  paths: Any,
) -> Any:
    """
    Delete ``@cache_page`` / ``dynamic_cache_page`` entries via Django's cache.
    
      API.
    
    Drops the ``cache_header`` registry entry, then either the ``get_cache_key``
      page
    key or—when the registry is missing—the empty-``Vary`` page key Django would
      use.
    
    Args:
      request (Any): Request passed to this helper.
      paths (Any): Iterable of filesystem paths as strings.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _delete_django_cache_page_entries_for_request(None, None)
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


def _redis_delete_cache_page_keys_matching_digests(
  client: Any,
  digests: Any,
) -> Any:
    """
    Delete raw Redis keys for ``cache_page`` / ``cache_header`` rows matching.
    
      URL digests.
    
    Needed when responses ``Vary`` on headers (e.g. ``Cookie``): each variant
      has
    a distinct full key, so ``get_cache_key`` for a single synthetic request is
    not enough. Keys still embed the URL MD5 hex from Django's cache layer.
    
    Args:
      client (Any): Live handle (pool, client, or connection).
      digests (Any): Digests passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _redis_delete_cache_page_keys_matching_digests(None, None)
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


def _get_cache_stats() -> Any:
    """
    Return basic Redis/cache statistics for the HPCPerfStats Monitor.
    
    Returns:
      Any: Open return polymorphism from ``_get_cache_stats``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> _get_cache_stats()  # doctest: +SKIP
    """
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


def _get_timescaledb_stats() -> Any:
    """
    Return basic TimescaleDB/PostgreSQL statistics for the HPCPerfStats Monitor.
    
    Returns:
      Any: Open return polymorphism from ``_get_timescaledb_stats``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _get_timescaledb_stats()  # doctest: +SKIP
    """
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

            # Site-wide newest sample: ORDER BY time DESC LIMIT 1 (3h window).
            # Never use multi-day GROUP BY max(time) or max(range_start) here.
            try:
                cur.execute(
                    """
                    SELECT time
                    FROM host_data
                    WHERE time > now() - interval '3 hours'
                    ORDER BY time DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    stats["host_data_newest"] = format_host_data_newest_iso(row[0])
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


def _list_recent_host_fqdns_from_redis() -> Any:
    """
    FQDNs from Django-cache Redis ``recent_host:*`` keys (listend inventory).
    
    Returns:
      Any: Open return polymorphism from
      ``_list_recent_host_fqdns_from_redis``: concrete type depends on inputs
      and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> _list_recent_host_fqdns_from_redis()  # doctest: +SKIP
    """
    hosts = []

    def _decode_key(raw_key: Any) -> Any:
        """
        Internal helper to handle decode key.
        
        Args:
          raw_key (Any): Raw key passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _decode_key(None)  # doctest: +SKIP
        """
        if isinstance(raw_key, bytes):
            return raw_key.decode("utf-8", "replace")
        return str(raw_key)

    try:
        client = _get_redis_cache_client()
        if client is None or not hasattr(client, "scan_iter"):
            return hosts
        for key in client.scan_iter(match="recent_host:*", count=1000):
            key_str = _decode_key(key)
            if not key_str.startswith("recent_host:"):
                continue
            host = key_str.split("recent_host:", 1)[1]
            if host and "." in host:
                hosts.append(host)
    except Exception:
        return []
    return hosts


def _get_rabbitmq_stats() -> Any:
    """
    Return RabbitMQ statistics for the HPCPerfStats Monitor.
    
    Uses the RabbitMQ Management HTTP API (default
      ``http://{rmq_server}:15672``).
    Credentials/URL can be overridden via environment variables:
      - RABBITMQ_MANAGEMENT_URL
      - RABBITMQ_MANAGEMENT_USER (default: guest)
      - RABBITMQ_MANAGEMENT_PASSWORD (default: guest)
    
    The ~24h publish estimate is derived from deltas of the cumulative publish
    counter between Redis snapshots (``KEY_ADMIN_RMQ_SNAPSHOT``).
    
    Returns:
      Any: Open return polymorphism from ``_get_rabbitmq_stats``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _get_rabbitmq_stats()  # doctest: +SKIP
    """
    try:
        cached_stats = cache.get(KEY_ADMIN_RMQ_STATS)
        if isinstance(cached_stats, dict):
            return cached_stats
    except Exception:
        cached_stats = None

    stats = {}

    try:
        import requests  # type: ignore
    except Exception:
        stats["error"] = "Failed to import requests for RabbitMQ management stats"
        try:
            cache.set(KEY_ADMIN_RMQ_STATS, stats, timeout=TIMEOUT_ADMIN_STATS)
        except Exception:
            pass
        return stats

    try:
        rmq_host = cfg.get_rmq_server()
        rmq_queue = cfg.get_rmq_queue()
    except Exception:
        return stats

    base_url = os.environ.get("RABBITMQ_MANAGEMENT_URL", f"http://{rmq_host}:15672")
    user = os.environ.get("RABBITMQ_MANAGEMENT_USER", "guest")
    password = os.environ.get("RABBITMQ_MANAGEMENT_PASSWORD", "guest")
    auth = (user, password)
    queue_url = f"{base_url.rstrip('/')}/api/queues/%2F/{rmq_queue}"
    nodes_url = f"{base_url.rstrip('/')}/api/nodes"

    def _rate_from_details(msg_stats: Any, key: Any) -> Any:
        """
        Internal helper to handle rate from details.
        
        Args:
          msg_stats (Any): Msg stats passed to this helper.
          key (Any): Key passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _rate_from_details(None, None)  # doctest: +SKIP
        """
        details = msg_stats.get(f"{key}_details") or {}
        rate = details.get("rate")
        if rate is None:
            return None
        try:
            return float(rate)
        except (TypeError, ValueError):
            return None

    try:
        resp = requests.get(queue_url, auth=auth, timeout=5)
    except Exception as e:
        stats["error"] = f"Failed to connect to RabbitMQ management API: {e}"
    else:
        if resp.status_code != 200:
            stats["error"] = (
                f"RabbitMQ management API returned HTTP {resp.status_code}"
            )
        else:
            try:
                data = resp.json()
            except Exception as e:
                stats["error"] = (
                    f"Failed to decode RabbitMQ management API response: {e}"
                )
                data = {}

            if data:
                stats["queue"] = rmq_queue
                stats["messages"] = data.get("messages")
                stats["messages_ready"] = data.get("messages_ready")
                stats["messages_unacknowledged"] = data.get(
                    "messages_unacknowledged"
                )
                stats["consumers"] = data.get("consumers")
                stats["message_bytes"] = data.get("message_bytes")
                stats["message_bytes_ready"] = data.get("message_bytes_ready")
                stats["message_bytes_unacknowledged"] = data.get(
                    "message_bytes_unacknowledged"
                )

                msg_stats = data.get("message_stats") or {}
                publish_total = msg_stats.get("publish")
                deliver_total = msg_stats.get("deliver_get")
                ack_total = msg_stats.get("ack")
                redeliver_total = msg_stats.get("redeliver")
                if publish_total is not None:
                    stats["messages_published_total"] = publish_total
                if deliver_total is not None:
                    stats["messages_delivered_total"] = deliver_total
                if ack_total is not None:
                    stats["messages_acked_total"] = ack_total
                if redeliver_total is not None:
                    stats["messages_redelivered_total"] = redeliver_total

                publish_rate = _rate_from_details(msg_stats, "publish")
                deliver_rate = _rate_from_details(msg_stats, "deliver_get")
                ack_rate = _rate_from_details(msg_stats, "ack")
                redeliver_rate = _rate_from_details(msg_stats, "redeliver")
                if publish_rate is not None:
                    stats["messages_publish_rate"] = publish_rate
                if deliver_rate is not None:
                    stats["messages_deliver_rate"] = deliver_rate
                if ack_rate is not None:
                    stats["messages_ack_rate"] = ack_rate
                if redeliver_rate is not None:
                    stats["messages_redeliver_rate"] = redeliver_rate

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
                                prev_time = timezone.make_aware(
                                    prev_time, dt_timezone.utc
                                )
                            delta = now - prev_time
                            hours = delta.total_seconds() / 3600.0
                            if hours > 0 and publish_total is not None:
                                since_snapshot = max(
                                    0, int(publish_total - int(prev_publish))
                                )
                                stats["messages_published_since_snapshot"] = (
                                    since_snapshot
                                )
                                stats["snapshot_hours"] = round(hours, 2)
                                rate_per_hour = since_snapshot / hours
                                stats[
                                    "messages_published_last_24h_estimate"
                                ] = int(rate_per_hour * 24.0)
                    except Exception:
                        pass

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

    # Node memory / disk / alarms (best-effort; do not overwrite queue error).
    try:
        nodes_resp = requests.get(nodes_url, auth=auth, timeout=5)
        if nodes_resp.status_code == 200:
            nodes = nodes_resp.json()
            if isinstance(nodes, list) and nodes:
                running = [
                    n for n in nodes if isinstance(n, dict) and n.get("running")
                ]
                node = running[0] if running else (
                    nodes[0] if isinstance(nodes[0], dict) else None
                )
                if node is not None:
                    if node.get("name") is not None:
                        stats["node_name"] = node.get("name")
                    if node.get("mem_used") is not None:
                        stats["mem_used"] = node.get("mem_used")
                    if node.get("mem_limit") is not None:
                        stats["mem_limit"] = node.get("mem_limit")
                    if node.get("disk_free") is not None:
                        stats["disk_free"] = node.get("disk_free")
                    if node.get("disk_free_limit") is not None:
                        stats["disk_free_limit"] = node.get("disk_free_limit")
                    if node.get("erlang_version") is not None:
                        stats["erlang_version"] = node.get("erlang_version")
                    alarms = node.get("alarms")
                    if isinstance(alarms, list):
                        if alarms:
                            stats["alarms"] = ", ".join(str(a) for a in alarms)
                        else:
                            stats["alarms"] = "(none)"
    except Exception:
        pass

    try:
        cache.set(KEY_ADMIN_RMQ_STATS, stats, timeout=TIMEOUT_ADMIN_STATS)
    except Exception:
        pass

    return stats


def _get_recent_rabbitmq_host_stats() -> Any:
    """
    Return per-host last-seen timestamps sourced directly from Redis keys.
    
    Returns:
      Any: Open return polymorphism from ``_get_recent_rabbitmq_host_stats``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _get_recent_rabbitmq_host_stats()  # doctest: +SKIP
    """
    now = timezone.now()
    host_stats = []

    def _decode_key(raw_key: Any) -> Any:
        """
        Internal helper to handle decode key.
        
        Args:
          raw_key (Any): Raw key passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _decode_key(None)  # doctest: +SKIP
        """
        if isinstance(raw_key, bytes):
            return raw_key.decode("utf-8", "replace")
        return str(raw_key)

    try:
        client = _get_redis_cache_client()
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


@ensure_csrf_cookie
@SESSION_SCHEMA
@api_view(["GET"])
def session_info(request: Any) -> Any:
    """
    Return current session state for SPA (logged_in, username, is_staff).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> session_info(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err
    return Response({
        "logged_in": True,
        "username": request.session.get("username", ""),
        "is_staff": request.session.get("is_staff", False),
        "machine_name": cfg.get_host_name_ext(),
    })


@USER_API_KEY_SCHEMA
@api_view(["GET"])
def user_api_key_status(request: Any) -> Any:
    """
    Return API key visibility for the OAuth session (create key if none exists).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> user_api_key_status(None)  # doctest: +SKIP
    """
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


@USER_API_KEY_ROTATE_SCHEMA
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
def user_api_key_rotate(request: Any) -> Any:
    """
    Revoke active keys for this user and return a newly minted raw key.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> user_api_key_rotate(None)  # doctest: +SKIP
    """
    csrf_err = _require_csrf_for_session_post(request)
    if csrf_err is not None:
        return csrf_err
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


@DROP_STAFF_SCHEMA
@api_view(["POST"])
def drop_staff_for_session(request: Any) -> Any:
    """
    Remove staff access for the current authenticated session only.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> drop_staff_for_session(None)  # doctest: +SKIP
    """
    csrf_err = _require_csrf_for_session_post(request)
    if csrf_err is not None:
        return csrf_err
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


@INVALIDATE_CACHE_SCHEMA
@api_view(["POST"])
def invalidate_cache_for_page(request: Any) -> Any:
    """
    Invalidate cache entries associated with the provided page path.
    
    Deletes Django ``@cache_page`` / ``dynamic_cache_page`` rows via
      ``cache.delete``
    (correct key prefix/versioning), then raw Redis ``SCAN`` for keys whose
      names
    embed the same URL MD5 as Django's cache layer (covers ``Vary`` variants).
    
    For any ``/machine`` or ``/machine/...`` path, also targets ``/api/home/``
      and
    drops ``home_options`` ORM cache keys (dates, metrics list, queues, states,
    site newest job end). Works without a raw Redis client (LocMem): ORM +
      Django
    cache deletes still run.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> invalidate_cache_for_page(None)  # doctest: +SKIP
    """
    csrf_err = _require_csrf_for_session_post(request)
    if csrf_err is not None:
        return csrf_err
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

    _expand_path_variants_for_job_list_api_cache(path_variants, normalized_path)

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


def _normalize_home_metrics(metrics: Any) -> Any:
    """
    Ensure home_options metrics rows match HomeMetricOption OpenAPI shape.
    
    Args:
      metrics (Any): Metrics passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _normalize_home_metrics(None)  # doctest: +SKIP
    """
    out = []
    for row in metrics or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "type": str(row.get("type") or ""),
            "metric": str(row.get("metric") or ""),
            "units": str(row.get("units") or ""),
        })
    return out


def _normalize_home_string_list(values: Any) -> Any:
    """
    Coerce truthy queue/state labels to strings for SPA Zod validation.
    
    Args:
      values (Any): Values passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _normalize_home_string_list(None)  # doctest: +SKIP
    """
    return [str(v) for v in (values or []) if v]


@HOME_OPTIONS_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
def home_options(request: Any) -> Any:
    """
    Return options for search form: date_list, metrics, queues, states,.
    
      machine_name.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> home_options(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err

    site_ttl = get_site_content_cache_timeout()

    def _dates_fn() -> Any:
        """
        Internal helper to handle dates function.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _dates_fn()  # doctest: +SKIP
        """
        return sorted(job_data.objects.dates("end_time", "day"))

    def _metrics_fn() -> Any:
        """
        Internal helper to handle metrics function.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _metrics_fn()  # doctest: +SKIP
        """
        return job_metrics_catalog_entries()

    def _queues_fn() -> Any:
        """
        Internal helper to handle queues function.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _queues_fn()  # doctest: +SKIP
        """
        return list(
            job_data.objects.distinct("queue").values_list("queue", flat=True)
        )

    def _states_fn() -> Any:
        """
        Internal helper to handle states function.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _states_fn()  # doctest: +SKIP
        """
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
        "machine_name": str(cfg.get_host_name_ext() or ""),
        "year_list": year_list,
        "date_list": sorted(month_dict.items(), reverse=True),
        "metrics": _normalize_home_metrics(metrics),
        "queues": _normalize_home_string_list(queues),
        "states": _normalize_home_string_list(states),
    })


def _queue_histogram_display_label(raw_queue: Any) -> Any:
    """
    Stable label for one queue bucket (Bokeh FactorRange factors must be.
    
      unique).
    
    Args:
      raw_queue (Any): Raw queue passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _queue_histogram_display_label(None)  # doctest: +SKIP
    """
    s = (raw_queue or "").strip()
    return s if s else "(no queue)"


def _merge_queue_bar_rows(rows: Any, *, metric: Any) -> Any:
    """
    Merge ORM rows that map to the same display label (e.g. NULL vs '' → '(no.
    
      queue)').
    
    Args:
      rows (Any): Rows passed to this helper.
      metric (Any): Metric passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      ValueError: Raised when ``_merge_queue_bar_rows`` hits a ``ValueError``
      failure path.
    
    Examples:
      >>> _merge_queue_bar_rows(None, None)  # doctest: +SKIP
    """
    from collections import defaultdict

    if metric == "jobs":
        acc = defaultdict(int)
        for q, c in rows:
            acc[_queue_histogram_display_label(q)] += int(c or 0)
    elif metric == "node_hours":
        acc = defaultdict(float)
        for q, v in rows:
            acc[_queue_histogram_display_label(q)] += float(v or 0.0)
    else:
        raise ValueError(f"unknown queue bar metric: {metric!r}")
    return sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))


def _job_list_queue_bar_chart(
  job_list_qs: Any,
  width: int = 600,
  height: int = 400,
  *,
  metric: str = "jobs",
) -> Any:
    """
    Bokeh vbar of per-queue job count or summed node hours (full filtered job.
    
      list).
    
    Retained for unit tests and Playwright Bokeh embed fixtures even though the
      job
    list API no longer ships queue histograms in responses.
    
    Args:
      job_list_qs (Any): Job list qs passed to this helper.
      width (int): Integer value for width.
      height (int): Integer value for height.
      metric (str): String for metric.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _job_list_queue_bar_chart(None, 0, 0, "x")  # doctest: +SKIP
    """
    from bokeh.models import ColumnDataSource

    close_old_connections()
    try:
        if metric == "jobs":
            rows = list(
                job_list_qs.values("queue")
                .annotate(_bar_top=Count("jid", distinct=True))
                .order_by("-_bar_top")
                .values_list("queue", "_bar_top")
            )
            title = "Jobs by queue"
            y_label = "# jobs"
        else:
            rows = list(
                job_list_qs.values("queue")
                .annotate(_bar_top=Sum("node_hrs"))
                .order_by("-_bar_top")
                .values_list("queue", "_bar_top")
            )
            title = "Node hours by queue"
            y_label = "node hours"
        if not rows:
            return None
        merged = _merge_queue_bar_rows(rows, metric=metric)
        queue_names = [str(q) for q, _ in merged]
        tops = [float(v) for _, v in merged]
        source = ColumnDataSource(dict(x=queue_names, top=tops))
        max_top = max(tops) if tops else 0.0
        y_high = max(1.0, float(max_top) * 1.05) if max_top > 0 else 1.0
        p = new_spa_embedded_figure(
            x_range=queue_names,
            y_range=(0.0, y_high),
            height=height,
            width=width,
            title=title,
        )
        p.vbar(
            x="x",
            top="top",
            bottom=0,
            width=0.7,
            source=source,
            fill_color="#3182bd",
            line_color="#225ea8",
        )
        p.xaxis.axis_label = "queue"
        p.yaxis.axis_label = y_label
        p.xgrid.visible = False
        p.xaxis.major_label_orientation = (
            "vertical" if len(queue_names) > 5 else "horizontal"
        )
        return p
    finally:
        close_old_connections()


# Display titles for built-in job list histogram columns (column name -> UI title)
JOB_HIST_DISPLAY_NAMES = {
    "runtime": "Number of jobs by cpu hours",
    "nhosts": "Number of jobs by number of nodes",
    "queue_wait": "Number of jobs by queue wait time",
}
JOB_LIST_HISTOGRAM_NO_JOBS_REASON = "No jobs matched this query."
_JOB_LIST_API_CACHE_PATHS = (
    "/api/jobs/",
    "/api/jobs/filter_options/",
    "/api/jobs/histograms/",
    "/api/jobs/histograms/batch/",
)
_JOB_LIST_MACHINE_BROWSE_PREFIXES = (
    "/machine/jobs",
    "/machine/year/",
    "/machine/date/",
    "/machine/month/",
    "/machine/user/",
    "/machine/account/",
    "/machine/queue/",
    "/machine/host/",
)


def _build_histogram_queryset(request: Any) -> Any:
    """
    Build the base queryset and metric filters for histogram endpoints.
    
    Returns (job_list_qs, nj, fields, cur_metrics) where:
    - job_list_qs: filtered queryset ordered by jid (presentation order_by
      ignored)
    - nj: count of jobs in queryset
    - fields: normalized/expanded query params dict
    - cur_metrics: dict of metric_name__op -> value (from query params)
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_histogram_queryset(None)  # doctest: +SKIP
    """
    try:
        job_list_qs, fields, cur_metrics, _ = _build_job_list_queryset_from_request(
            request,
            extra_excluded_fields=_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM,
            annotate_all=True,
        )
        # Histogram bins are order-invariant; skip user order_by (expensive metric sorts).
        job_list_qs = job_list_qs.order_by("jid")
        nj = job_list_qs.count()
        return job_list_qs, nj, fields, cur_metrics
    except Exception:
        # Database unavailable or other error: behave as if no jobs matched.
        logger.exception("job_list_histograms: _build_histogram_queryset failed")
        return job_data.objects.none(), 0, {}, {}


def _build_histogram_dataframe(job_list_qs: Any, cur_metrics: Any) -> Any:
    """
    Build the DataFrame and histogram metric list used for metric-based.
    
      histograms.
    
    Returns (df, hist_metrics, jids_ordered) where:
    - df: pandas DataFrame indexed by jid with metric columns +
      runtime/nhosts/queue_wait
    - hist_metrics: list of (metric_name, units_label)
    - jids_ordered: list of jids in deterministic order
    
    Args:
      job_list_qs (Any): Job list qs passed to this helper.
      cur_metrics (Any): Cur metrics passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_histogram_dataframe(None, None)  # doctest: +SKIP
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


def _extract_bokeh_doc_root_ids(doc: Any) -> Any:
    """
    Return a set of root ids declared in a Bokeh json_item doc payload.
    
    Args:
      doc (Any): Doc passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _extract_bokeh_doc_root_ids(None)  # doctest: +SKIP
    """
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


def _is_valid_bokeh_json_item_payload(payload: Any) -> Any:
    """
    True when json_item has declared root ids and doc/root consistency.
    
    Args:
      payload (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _is_valid_bokeh_json_item_payload(None)  # doctest: +SKIP
    """
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


def _sanitize_hist_plot_item(plot: Any) -> Any:
    """
    Convert Bokeh plot to json_item and drop invalid payloads.
    
    Args:
      plot (Any): Plot passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _sanitize_hist_plot_item(None)  # doctest: +SKIP
    """
    if plot is None:
        return None
    try:
        payload = json_item(plot)
    except Exception:
        return None
    return payload if _is_valid_bokeh_json_item_payload(payload) else None


def _job_list_metric_hist_pair(
  df: Any,
  metric_name: Any,
  label: Any,
  display_title: Any,
  thumb_wh: Any,
  full_wh: Any,
) -> Any:
    """
    Return (thumb_figure, full_figure) for one metric via ``job_hist``.
    
    Args:
      df (Any): Df passed to this helper.
      metric_name (Any): Metric name passed to this helper.
      label (Any): Label passed to this helper.
      display_title (Any): Display title passed to this helper.
      thumb_wh (Any): Thumb wh passed to this helper.
      full_wh (Any): Full wh passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _job_list_metric_hist_pair(None, None, None, None, None, None)
    """
    tw, th = thumb_wh
    fw, fh = full_wh
    p_thumb = job_hist(
        df, metric_name, label, width=tw, height=th, title=display_title
    )
    p_full = job_hist(
        df, metric_name, label, width=fw, height=fh, title=display_title
    )
    return p_thumb, p_full


def _histogram_queryset_for_plotting(
  job_list_qs: Any,
  nj: Any,
  sample_size: Any | None = None,
) -> Any:
    """
    Return (plot_qs, histogram_nj, histogram_sampled) for dataframe.
    
      materialization.
    
    Uses the full matching job queryset (no sampling cap).
    
    Args:
      job_list_qs (Any): Job list qs passed to this helper.
      nj (Any): Nj passed to this helper.
      sample_size (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _histogram_queryset_for_plotting(None, None, None)  # doctest: +SKIP
    """
    del sample_size  # retained for call-site compatibility
    return job_list_qs, nj, False


def _histogram_response_meta(
  nj: Any,
  histogram_nj: Any,
  histogram_sampled: Any,
) -> Any:
    """
    Shared histogram envelope fields (batch + single metric responses).
    
    Args:
      nj (Any): Nj passed to this helper.
      histogram_nj (Any): Histogram nj passed to this helper.
      histogram_sampled (Any): Histogram sampled passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _histogram_response_meta(None, None, None)  # doctest: +SKIP
    """
    return {
        "nj": nj,
        "histogram_nj": histogram_nj,
        "histogram_sampled": histogram_sampled,
    }


def _parse_histogram_batch_metric_names(raw_metrics: Any) -> Any:
    """
    Parse comma-separated metric names for batch histogram endpoint.
    
    Args:
      raw_metrics (Any): Raw metrics passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_histogram_batch_metric_names(None)  # doctest: +SKIP
    """
    if not raw_metrics:
        return list(JOB_LIST_HISTOGRAM_BATCH_METRICS_DEFAULT)
    names = [part.strip() for part in str(raw_metrics).split(",") if part.strip()]
    return names or list(JOB_LIST_HISTOGRAM_BATCH_METRICS_DEFAULT)


def _histogram_metric_unavailable_stub(
  metric_name: Any,
  nj: Any,
  unavailable_reason: Any,
) -> Any:
    """
    Batch/single histogram envelope when plots are unavailable.
    
    Args:
      metric_name (Any): Metric name passed to this helper.
      nj (Any): Nj passed to this helper.
      unavailable_reason (Any): Unavailable reason passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _histogram_metric_unavailable_stub(None, None, None)  # doctest: +SKIP
    """
    display_title = JOB_HIST_DISPLAY_NAMES.get(metric_name, metric_name)
    return {
        "group": "metric",
        "metric": metric_name,
        "nj": nj,
        "title": display_title,
        "plot_item_thumb": None,
        "plot_item_full": None,
        "plot_unavailable_reason": unavailable_reason,
    }


def _build_histogram_batch_entries(
  metric_names: Any,
  nj: Any,
  plot_qs: Any,
  cur_metrics: Any,
) -> Any:
    """
    Return one histogram envelope per requested metric (never omit names).
    
    Args:
      metric_names (Any): Metric names passed to this helper.
      nj (Any): Nj passed to this helper.
      plot_qs (Any): Plot qs passed to this helper.
      cur_metrics (Any): Cur metrics passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_histogram_batch_entries(None, None, None, None)  # doctest: +SKIP
    """
    if nj == 0:
        return [
            _histogram_metric_unavailable_stub(
                metric_name, nj, JOB_LIST_HISTOGRAM_NO_JOBS_REASON
            )
            for metric_name in metric_names
        ]

    df, hist_metrics, _ = _build_histogram_dataframe(plot_qs, cur_metrics)
    histograms = []
    for metric_name in metric_names:
        payload = _build_metric_histogram_payload(df, hist_metrics, metric_name, nj)
        if payload is not None:
            histograms.append(payload)
            continue
        histograms.append(
            _histogram_metric_unavailable_stub(
                metric_name,
                nj,
                f"Metric '{metric_name}' is not available for this query.",
            )
        )
    return histograms


def _machine_path_targets_job_list_api_cache(normalized_path: str) -> Any:
    """
    True when SPA browse routes load GET /api/jobs/ or histogram batch APIs.
    
    Args:
      normalized_path (str): String for normalized path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _machine_path_targets_job_list_api_cache("x")  # doctest: +SKIP
    """
    if normalized_path == "/machine/jobs":
        return True
    if normalized_path.startswith("/machine/jobs/"):
        return False
    return any(
        normalized_path.startswith(prefix)
        for prefix in _JOB_LIST_MACHINE_BROWSE_PREFIXES
        if prefix != "/machine/jobs"
    )


def _expand_path_variants_for_job_list_api_cache(
  path_variants: Any,
  normalized_path: str,
) -> None:
    """
    Also purge job list + histogram API cache rows for job browse pages.
    
    Args:
      path_variants (Any): Path variants passed to this helper.
      normalized_path (str): String for normalized path.
    
    Returns:
      None
    
    Examples:
      >>> _expand_path_variants_for_job_list_api_cache(None, "x")
    """
    if not _machine_path_targets_job_list_api_cache(normalized_path):
        return
    for api_path in _JOB_LIST_API_CACHE_PATHS:
        path_variants.add(api_path.rstrip("/") or "/")
        path_variants.add(api_path if api_path.endswith("/") else f"{api_path}/")


def _build_metric_histogram_payload(
  df: Any,
  hist_metrics: Any,
  metric_name: Any,
  nj: Any,
  thumb_wh: tuple[Any, ...] = (280, 200),
  full_wh: tuple[Any, ...] = (600, 400),
) -> Any:
    """
    Build one metric histogram envelope (thumb + full Bokeh json_items).
    
    Args:
      df (Any): Df passed to this helper.
      hist_metrics (Any): Hist metrics passed to this helper.
      metric_name (Any): Metric name passed to this helper.
      nj (Any): Nj passed to this helper.
      thumb_wh (tuple[Any, ...]): Sequence for thumb wh.
      full_wh (tuple[Any, ...]): Sequence for full wh.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_metric_histogram_payload(None, None, None, None, [], [])
    """
    label = None
    for m, lbl in hist_metrics:
        if m == metric_name:
            label = lbl
            break
    if label is None:
        return None
    display_title = JOB_HIST_DISPLAY_NAMES.get(metric_name, metric_name)
    p_thumb, p_full = _job_list_metric_hist_pair(
        df,
        metric_name,
        label,
        display_title,
        thumb_wh,
        full_wh,
    )
    thumb_item = _sanitize_hist_plot_item(p_thumb)
    full_item = _sanitize_hist_plot_item(p_full)
    unavailable = None
    if thumb_item is None or full_item is None:
        unavailable = f"No histogram data available for metric '{metric_name}' in this query."
    return {
        "group": "metric",
        "metric": metric_name,
        "nj": nj,
        "title": display_title,
        "plot_item_thumb": thumb_item,
        "plot_item_full": full_item,
        "plot_unavailable_reason": unavailable,
    }


@JOB_LIST_HISTOGRAMS_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_list_histograms(request: Any) -> Any:
    """
    Return Bokeh histograms for the job list, loaded incrementally.
    
    This endpoint supports per-plot loading for metric histograms. The caller
    must provide a 'group' query parameter:
    
    - group=metric&metric=<name>: return a single metric histogram (thumb and
      full) for the given metric name.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_list_histograms(None)  # doctest: +SKIP
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
    plot_qs, histogram_nj, histogram_sampled = _histogram_queryset_for_plotting(job_list_qs, nj)
    hist_meta = _histogram_response_meta(nj, histogram_nj, histogram_sampled)
    if nj == 0:
        # Preserve a consistent shape even when no jobs match the filter.
        if group == "metric":
            metric_name = (request.GET.get("metric") or "").strip()
            return _JSONResponse(
                {
                    "group": "metric",
                    "metric": metric_name or None,
                    **hist_meta,
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

        df, hist_metrics, _ = _build_histogram_dataframe(plot_qs, cur_metrics)
        payload = _build_metric_histogram_payload(
            df, hist_metrics, metric_name, nj
        )
        if payload is None:
            return Response(
                {
                    "error": f"Metric '{metric_name}' is not available for this query.",
                    "available_metrics": [m for m, _ in hist_metrics],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload.update(hist_meta)
        return _JSONResponse(payload)

    return _JSONResponse(
        {
            "error": f"Unknown group '{group}'.",
            "allowed_groups": ["metric"],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


@JOB_LIST_HISTOGRAMS_BATCH_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_list_histograms_batch(request: Any) -> Any:
    """
    Return multiple metric histograms in one response (single dataframe build).
    
    Query param ``metrics`` is comma-separated (default:
      runtime,nhosts,queue_wait).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_list_histograms_batch(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err

    job_list_qs, nj, _fields, cur_metrics = _build_histogram_queryset(request)
    plot_qs, histogram_nj, histogram_sampled = _histogram_queryset_for_plotting(job_list_qs, nj)
    hist_meta = _histogram_response_meta(nj, histogram_nj, histogram_sampled)
    metric_names = _parse_histogram_batch_metric_names(request.GET.get("metrics"))
    histograms = _build_histogram_batch_entries(metric_names, nj, plot_qs, cur_metrics)
    return _JSONResponse({**hist_meta, "histograms": histograms})


def _include_filter_options(request: Any) -> Any:
    """
    When false, job_list skips faceted filter_options queryset work (SPA loads.
    
      options separately).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _include_filter_options(None)  # doctest: +SKIP
    """
    raw = request.GET.get("include_filter_options", "1")
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _job_list_filter_options_builder() -> Any:
    """
    Internal helper to handle job list filter options builder.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _job_list_filter_options_builder()  # doctest: +SKIP
    """
    def _options_builder(
      req: Any,
      exclude_header_dimension: Any | None = None,
    ) -> Any:
        """
        Internal helper to handle options builder.
        
        Args:
          req (Any): Req passed to this helper.
          exclude_header_dimension (Any | None): One of ``Any``, ``None``.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _options_builder(None, None)  # doctest: +SKIP
        """
        return _build_job_list_queryset_from_request(
            req,
            extra_excluded_fields=_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM,
            annotate_all=True,
            exclude_header_dimension=exclude_header_dimension,
        )

    return _options_builder


def _resolve_job_list_filter_options(request: Any) -> Any:
    """
    Internal helper to resolve the job list filter options.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _resolve_job_list_filter_options(None)  # doctest: +SKIP
    """
    try:
        return build_job_list_filter_options(request, _job_list_filter_options_builder())
    except Exception:
        logger.exception("job_list: build_job_list_filter_options failed")
        return None


@JOB_LIST_FILTER_OPTIONS_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_list_filter_options_view(request: Any) -> Any:
    """
    Faceted header-filter option values for the current job-list selection.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_list_filter_options_view(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err
    return Response({"filter_options": _resolve_job_list_filter_options(request)})


@JOB_LIST_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_list(request: Any) -> Any:
    """
    Paginated job list only (histograms via separate job_list_histograms.
    
      endpoint).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_list(None)  # doctest: +SKIP
    """
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

    qname, filter_summary = build_job_list_qname_and_filter_summary(fields)

    filter_options = None
    if _include_filter_options(request):
        filter_options = _resolve_job_list_filter_options(request)

    if nj == 0:
        return Response({
            "job_list": [],
            "nj": 0,
            "aggregates": {"total_node_hours": 0},
            "current_path": request.get_full_path() if "?" in request.get_full_path() else None,
            "qname": qname,
            "filter_summary": filter_summary,
            "filter_options": filter_options,
            "order_by": order_by,
            "pagination": {
                "page": 1,
                "num_pages": 1,
                "has_previous": False,
                "has_next": False,
                "previous_page_number": None,
                "next_page_number": None,
            },
        })

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
        "filter_summary": filter_summary,
        "filter_options": filter_options,
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


def _parse_job_detail_defer_set(request: Any) -> Any:
    """
    Parse ``defer=xalt,proc,multiprecision``; ``light=1`` implies defer.
    
      xalt+proc.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_job_detail_defer_set(None)  # doctest: +SKIP
    """
    defer_raw = (request.GET.get("defer") or "").strip()
    defer_set = {part.strip().lower() for part in defer_raw.split(",") if part.strip()}
    light_mode = str(request.GET.get("light", "")).lower() in ("1", "true", "yes")
    if light_mode:
        defer_set |= {"xalt", "proc"}
    return defer_set


def _job_for_detail_list_serializer(pk: Any, fallback_job: Any) -> Any:
    """
    Annotate a single job like job_list so ``performance.sort_rank`` is correct.

    Args:
      pk (Any): Job id.
      fallback_job (Any): Job instance if annotate returns nothing.

    Returns:
      Any: Annotated job_data row (or fallback).
    """
    annotated = annotate_job_list_performance_fields(
        job_data.objects.filter(jid=pk)
    ).first()
    return annotated if annotated is not None else fallback_job


@JOB_DETAIL_SCHEMA
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_detail(request: Any, pk: Any) -> Any:
    """
    Single job detail: metadata, host_list, fsio, xalt, schema, URLs (plots via.
    
      separate job_plots endpoint).
    
    Args:
      request (Any): Request passed to this helper.
      pk (Any): Pk passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_detail(None, None)  # doctest: +SKIP
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

    # Artifact / job_data only — do not construct jid_table (host_data) here.
    host_list = list(job.host_list or [])
    defer_set = _parse_job_detail_defer_set(request)

    def _fetch_xalt() -> Any:
        """
        Internal helper to fetch the xalt.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _fetch_xalt()  # doctest: +SKIP
        """
        close_old_connections()

        def _xalt_fn() -> Any:
            """
            Internal helper to handle xalt function.
            
            Returns:
              Any: Value produced by this call (type depends on inputs).
            
            Examples:
              >>> _xalt_fn()  # doctest: +SKIP
            """
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
    multiprecision_payload = load_job_detail_artifact(
        job.jid,
        ARTIFACT_KIND_MULTIPRECISION_MIX,
        "",
        compute_detail_input_fingerprint(job),
    ) or {}

    def _fetch_proc_list() -> Any:
        """
        Internal helper to fetch the proc list.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _fetch_proc_list()  # doctest: +SKIP
        """
        from .models import proc_data

        close_old_connections()
        try:
            value_fields = (
                "host",
                "proc",
                "device",
                "uid",
                "vm_peak",
                "vm_size",
                "vm_lck",
                "vm_hwm",
                "vm_rss",
                "vm_data",
                "vm_stk",
                "vm_exe",
                "vm_lib",
                "vm_pte",
                "vm_swap",
                "threads",
            )
            # One row per (host, proc) via unique_together; values() dicts (not flat names).
            return cached_orm(
                f"{KEY_PROC_LIST}:{job.jid}",
                job_cache_timeout,
                lambda: list(
                    proc_data.objects.filter(jid=job.jid)
                    .order_by("host", "proc")
                    .values(*value_fields)
                ),
            )
        finally:
            close_old_connections()

    gpu_active = detail_payload.get("gpu_active")
    gpu_utilization_max = detail_payload.get("gpu_utilization_max")
    gpu_utilization_mean = detail_payload.get("gpu_utilization_mean")
    gpu_count = detail_payload.get("gpu_count")
    gpu_inventory = detail_payload.get("gpu_inventory") or []
    if not isinstance(gpu_inventory, list):
        gpu_inventory = []
    xalt_payload = None
    fsio = detail_payload.get("fsio") or {}
    if not fsio:
        fsio = _fsio_dict_from_metrics(job) or {}
    schema = detail_payload.get("schema") or {}
    proc_list = []

    tasks = []
    if "proc" not in defer_set:
        tasks.append(("proc_list", _fetch_proc_list))
    if "xalt" not in defer_set and cfg.get_xalt_user() != "":
        tasks.append(("xalt", _fetch_xalt))

    if tasks:
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
        first_host = urllib.parse.quote(host_list[0] + cfg.get_host_name_ext(), safe="")
        hoststring = urlstring + "%20host%3D" + first_host
        for host in host_list[1:]:
            hoststring += "%20OR%20%20host%3D" + urllib.parse.quote(host + "*", safe="")
    else:
        hoststring = urlstring
    serverstring = urlstring + "%20mds*%20OR%20%20oss*"

    earliest_ts = _format_log_timestamp(job.start_time)
    latest_ts = _format_log_timestamp(job.end_time)

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
        "job_data": JobListSerializer(
            _job_for_detail_list_serializer(pk, job)
        ).data,
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
        "gpu_inventory": gpu_inventory,
        "metrics_list": metrics_list,
        "proc_list": proc_list,
        "derived_data_status": "ready" if detail_payload else "loading",
    }
    if "multiprecision" not in defer_set:
        payload["multiprecision_cpu_plot_item"] = multiprecision_payload.get("cpu_plot_item")
        payload["multiprecision_cpu_unavailable_reason"] = multiprecision_payload.get(
            "cpu_unavailable_reason"
        )
        payload["multiprecision_gpu_plot_item"] = multiprecision_payload.get("gpu_plot_item")
        payload["multiprecision_gpu_unavailable_reason"] = multiprecision_payload.get(
            "gpu_unavailable_reason"
        )
    if request.session.get("is_staff", False):
        payload["staff_metrics_distinct_time_count"] = job.metrics_distinct_time_count
        from .staff_artifact_contract import staff_artifact_contract_payload

        payload["staff_artifact_contract"] = staff_artifact_contract_payload(job.jid)

    return Response(payload)


@JOB_PLOTS_SCHEMA
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_plots(request: Any, pk: Any) -> Any:
    """
    Job-level plots from Redis L1 / ``job_plot_artifact`` L2 only.

    No request-time ``jid_table`` / ``host_data`` compute. Fingerprint uses
    persisted ``metrics_distinct_time_count`` only. Null ``plot_item`` with
    ``unavailable_reason`` on a fingerprint hit is terminal unavailable.

    Query params:
    - plot: omit or ``all`` for all three; or one of summary_plot, roofline,
      gpu_roofline.
    - zoom: ``1`` for zoom layout (single-plot requests only).
    - progressive: ``1`` with plot=all (no zoom): HTTP 200 with ``status``
      partial/ready while missing kinds are listed in ``loading_plots``.

    Args:
      request (Any): Request passed to this helper.
      pk (Any): Pk passed to this helper.

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> job_plots(None, None)  # doctest: +SKIP
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

    plot_fingerprint = compute_plot_input_fingerprint(job)
    l2_layout = JOB_PLOT_LAYOUT_ZOOM_V3 if zoom_mode else JOB_PLOT_LAYOUT_NORMAL
    _job_plots_log = logging.getLogger(__name__)

    def _plot_data_cache_key(kind: Any) -> Any:
        """
        Internal helper to handle plot data cache key.

        Args:
          kind (Any): Mode or kind token selecting a code path.

        Returns:
          Any: Value produced by this call (type depends on inputs).

        Examples:
          >>> _plot_data_cache_key(None)  # doctest: +SKIP
        """
        return make_cache_key("JOB_PLOTS_DATA", job.jid, kind, plot_fingerprint)

    requested_keys = (
        [plot_kind]
        if plot_kind != "all"
        else list(JOB_PLOT_KINDS)
    )

    # Artifact-only: L1 then L2. Null plot_item + reason is a warm hit.
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
        if isinstance(cached_entry, dict):
            cached_results[key] = cached_entry
            continue
        l2_entry = load_cached_job_plot_entry(
            job.jid, key, l2_layout, plot_fingerprint
        )
        if l2_entry is not None and isinstance(l2_entry, dict):
            cached_results[key] = {
                "plot_item": l2_entry.get("plot_item"),
                "unavailable_reason": l2_entry.get("unavailable_reason"),
                "bw_axis": l2_entry.get("bw_axis"),
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
            pi = cached_results[key]["plot_item"]
            if isinstance(pi, dict):
                try:
                    data_cache_key = _plot_data_cache_key(key)
                    cache.set(
                        data_cache_key,
                        {
                            "plot_item": pi,
                            "bw_axis": cached_results[key].get("bw_axis"),
                        },
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

    # Zoom: reuse size-independent L1 data payload when present (no live compute).
    if zoom_mode and missing_keys:
        still_missing = []
        for key in missing_keys:
            data_cache_key = _plot_data_cache_key(key)
            cached_plot_data = cache.get(data_cache_key)
            if isinstance(cached_plot_data, dict) and "plot_item" in cached_plot_data:
                raw_item = cached_plot_data.get("plot_item")
                if not isinstance(raw_item, dict):
                    still_missing.append(key)
                    continue
                cached_results[key] = {
                    "plot_item": _apply_zoom_layout_to_json_item(raw_item),
                    "unavailable_reason": None,
                    "bw_axis": cached_plot_data.get("bw_axis"),
                }
            elif isinstance(cached_plot_data, dict):
                # Legacy L1 data cache stored bare json_item (no envelope).
                cached_results[key] = {
                    "plot_item": _apply_zoom_layout_to_json_item(cached_plot_data),
                    "unavailable_reason": None,
                }
            else:
                still_missing.append(key)
        missing_keys = still_missing

    still_loading = list(missing_keys)
    if still_loading:
        if progressive:
            body = {
                "status": "partial",
                "detail": (
                    "Some plot artifacts are not ready yet. "
                    "Retry after update_metrics prewarm completes."
                ),
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
                if key == "gpu_roofline":
                    body["grplot_bw_axis"] = cached_results[key].get("bw_axis")
            return Response(body, status=status.HTTP_200_OK)
        return Response(
            {
                "status": "loading",
                "detail": (
                    "Requested plot artifacts are not ready yet. "
                    "Retry after update_metrics prewarm completes."
                ),
                "retry_after_seconds": 2,
                "loading_plots": still_loading,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    if plot_kind != "all":
        entry = cached_results[plot_kind]
        body = {
            "status": "ready",
            "plot": plot_kind,
            "plot_item": entry["plot_item"],
            "unavailable_reason": entry["unavailable_reason"],
        }
        if plot_kind == "gpu_roofline":
            body["grplot_bw_axis"] = entry.get("bw_axis")
        return Response(body)

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
        "grplot_bw_axis": cached_results["gpu_roofline"].get("bw_axis"),
    }
    if progressive:
        payload["status"] = "ready"
        payload["progressive"] = True
        payload["loading_plots"] = []
    return Response(payload)


@TYPE_DETAIL_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def type_detail(request: Any, jid: Any, type_name: Any) -> Any:
    """
    Type detail: Bokeh json_item (tplot_item), stats_data, schema.
    
    Args:
      request (Any): Request passed to this helper.
      jid (Any): Jid passed to this helper.
      type_name (Any): Type name passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> type_detail(None, None, None)  # doctest: +SKIP
    """
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
@HOST_PLOT_SCHEMA
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def host_plot(request: Any) -> Any:
    """
    Return Bokeh plot_item for a single host and time range (GET host,
    end_time__gte, end_time__lte).

    Documented ``host_data`` exception: authenticated users (not staff-only).
    Uses a longer session ``statement_timeout`` for large windows.

    Args:
      request (Any): Request passed to this helper.

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> host_plot(None)  # doctest: +SKIP
    """
    err = _require_auth(request)
    if err is not None:
        return err

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

    max_window = timedelta(days=HOST_PLOT_MAX_WINDOW_DAYS)
    if end_dt - start_dt > max_window:
        return Response(
            {
                "error": "time_range_too_large",
                "detail": f"Maximum host plot window is {HOST_PLOT_MAX_WINDOW_DAYS} days.",
                "max_window_days": HOST_PLOT_MAX_WINDOW_DAYS,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _host_plot_fn() -> Any:
        """
        Internal helper to handle host plot function.

        Returns:
          Any: Value produced by this call (type depends on inputs).

        Examples:
          >>> _host_plot_fn()  # doctest: +SKIP
        """
        try:
            with _pg_host_plot_statement_timeout():
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


def _get_xalt_jid_coverage(
  days: int = 3,
  missing_limit: int = 200,
  chunk_size: int = 1000,
) -> Any:
    """
    Staff-only: compute XALT coverage for JIDs in the last `days` in the main.
    
      DB.
    
    Coverage means: does `xalt_run` contain at least one row where
    `job_id == job_data.jid`.
    
    Args:
      days (int): Integer value for days.
      missing_limit (int): Integer value for missing limit.
      chunk_size (int): Integer value for chunk size.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _get_xalt_jid_coverage(0, 0, 0)  # doctest: +SKIP
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

    def _xalt_fn() -> Any:
        """
        Internal helper to handle xalt function.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _xalt_fn()  # doctest: +SKIP
        """
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

        def _chunks(seq: Any, size: int) -> Iterator[Any]:
            """
            Internal helper to handle chunks.
            
            Args:
              seq (Any): Seq passed to this helper.
              size (int): Integer value for size.
            
            Yields:
              Iterator[Any]: Value produced by this call (type depends on
              inputs).
            
            Examples:
              >>> _chunks(None, 0)  # doctest: +SKIP
            """
            for i in range(0, len(seq), size):
                yield seq[i : i + size]

        for chunk in _chunks(jids, chunk_size):
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


@ADMIN_MONITOR_SCHEMA
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def admin_monitor(request: Any) -> Any:
    """
    Staff-only: HPCPerfStats Monitor data (host timestamps, cache/Redis,
    RabbitMQ, TimescaleDB stats, telemetry health).
    
    Supports a lightweight, per-section API via the optional 'section' query
    param:
    - ?section=hosts      -> {"host_stats": [...]}
    - ?section=rabbitmq_hosts -> {"rabbitmq_host_stats": [...]}
    - ?section=cache      -> {"cache_stats": {...}}
    - ?section=rabbitmq   -> {"rabbitmq_stats": {...}}
    - ?section=timescaledb -> {"timescaledb_stats": {...}}
    - ?section=xalt      -> {"xalt_stats": {...}}
    - ?section=telemetry_health -> {"telemetry_health": {...}}
    - omitted/other       -> {"host_stats": [...], "rabbitmq_host_stats": [...],
                              "cache_stats": {...},
                                "rabbitmq_stats":
                                {...},
                              "timescaledb_stats":
                                {...}, "xalt_stats":
                                {...}, "telemetry_health": {...}}
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> admin_monitor(None)  # doctest: +SKIP
    """
    err = _require_staff(request)
    if err is not None:
        return err

    def _host_stats_fn() -> Any:
        """
        Return per-host last_seen timestamps and age buckets for admin monitor.
        
        Primary: Redis ``recent_host:*`` inventory + LATERAL ``ORDER BY time
          DESC
        LIMIT 1`` per host. Fallback when Redis is empty: 3h ``GROUP BY`` Max
        under a short statement timeout (never 8-day aggregate / 600s floor).
        
        Returns:
          Any: Open return polymorphism from ``_host_stats_fn``: concrete type
          depends on inputs and branch (mapping, scalar, handle, or
          ``None``-like empty).
        
        Examples:
          >>> _host_stats_fn()  # doctest: +SKIP
        """
        now = timezone.now()
        host_stats_local = []
        try:
            fqdns = _list_recent_host_fqdns_from_redis()
            if fqdns:
                latest_by_host = latest_sample_time_by_host(fqdns)
            else:
                logger.info(
                    "admin_monitor host_stats: Redis recent_host empty; "
                    "using 3h GROUP BY fallback"
                )
                with _pg_session_statement_timeout_for_admin_host_stats_query():
                    latest_by_host = latest_sample_time_by_host_in_window()
            for host, last_time in latest_by_host.items():
                entry = _admin_monitor_host_stat_dict(host or "", last_time, now)
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
            "telemetry_health": [KEY_ADMIN_TELEMETRY_HEALTH],
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
    if section == "telemetry_health":
        return Response(
            {"telemetry_health": compute_telemetry_health(force_refresh=refresh)}
        )

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
            "telemetry_health": compute_telemetry_health(force_refresh=refresh),
        }
    )


@JOB_MONITOR_SCHEMA
@dynamic_cache_page(site_response_cache_timeout)
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_monitor(request: Any) -> Any:
    """
    Staff-only: aggregate job failure statistics per user over a recent window.
    
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
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_monitor(None)  # doctest: +SKIP
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


@JOB_MONITOR_GPU_SCHEMA
@api_view(["GET"])
@throttle_classes([ExpensiveReadThrottle])
def job_monitor_gpu_for_user(request: Any) -> Any:
    """
    Staff-only GPU rollup for Job Monitor (single ``username`` or batch.
    
      ``usernames``).
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_monitor_gpu_for_user(None)  # doctest: +SKIP
    """
    err = _require_staff(request)
    if err is not None:
        return err

    usernames = _parse_job_monitor_gpu_usernames(request)
    if not usernames:
        return Response(
            {"error": "Missing required query param: username or usernames"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        days_param = int(request.GET.get("days", "") or 30)
    except (TypeError, ValueError):
        days_param = 30
    window_days = max(1, min(days_param, 365))
    now = timezone.now()
    start_time = now - timedelta(days=window_days)
    site_ttl = get_site_content_cache_timeout()

    if len(usernames) == 1:
        result = _compute_job_monitor_gpu_for_username(
            usernames[0], window_days, start_time, site_ttl
        )
        return Response(result)

    results = [
        _compute_job_monitor_gpu_for_username(username, window_days, start_time, site_ttl)
        for username in usernames
    ]
    return Response({"results": results})


def _parse_job_monitor_gpu_usernames(request: Any) -> Any:
    """
    Return username list from ``username`` or comma-separated ``usernames``.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_job_monitor_gpu_usernames(None)  # doctest: +SKIP
    """
    batch_raw = (request.GET.get("usernames") or "").strip()
    if batch_raw:
        return [part.strip() for part in batch_raw.split(",") if part.strip()]
    single = (request.GET.get("username") or "").strip()
    return [single] if single else []


def _compute_job_monitor_gpu_for_username(
  username: Any,
  window_days: Any,
  start_time: Any,
  site_ttl: Any,
) -> Any:
    """
    GPU rollup for one user from persisted metrics_data only (no host_data.
    
      fallback).
    
    Args:
      username (Any): Username passed to this helper.
      window_days (Any): Window days passed to this helper.
      start_time (Any): Start time passed to this helper.
      site_ttl (Any): Site ttl passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _compute_job_monitor_gpu_for_username(None, None, None, None)
    """

    def _compute_user_gpu() -> Any:
        """
        Internal helper to compute the user gpu.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> _compute_user_gpu()  # doctest: +SKIP
        """
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

    cache_key = make_cache_key("JOB_MONITOR_GPU_USER", window_days, username)
    return cached_orm(cache_key, site_ttl, _compute_user_gpu)


@SACCT_INGEST_SCHEMA
@api_view(["POST"])
@throttle_classes([StaffIngestThrottle])
def sacct_ingest(request: Any) -> Any:
    """
    Ingest pipe-delimited sacct output into job_data using sync_acct logic.
    
    Requires authentication (API key or session) and staff. Request body must be
    raw pipe-delimited sacct output (same format as sacct -P -o ...). Query
    param date=YYYY-MM-DD is required (the date of the data being ingested) to
    compute which jobs are already in the DB.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      Exception: Raised when ``sacct_ingest`` hits a ``Exception`` failure
      path.
    
    Examples:
      >>> sacct_ingest(None)  # doctest: +SKIP
    """
    csrf_err = _require_csrf_for_session_post(request)
    if csrf_err is not None:
        return csrf_err
    err = _require_staff(request)
    if err is not None:
        return err
    max_body_bytes = int(getattr(settings, "SACCT_INGEST_MAX_BODY_BYTES", 8 * 1024 * 1024))
    if len(request.body or b"") > max_body_bytes:
        return Response(
            {"error": "Request body exceeds ingest size limit"},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    try:
        body = request.body.decode("utf-8", errors="replace")
    except Exception as e:
        return Response(
            {"error": "Invalid request body", "detail": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )

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

    try:
        persist_accounting_daily_file(ingest_date, body)
    except AccountingFileShrinkError as e:
        return Response(
            {
                "error": "Accounting file would shrink",
                "date": date_str,
                "existing_lines": e.existing_lines,
                "incoming_lines": e.incoming_lines,
            },
            status=status.HTTP_409_CONFLICT,
        )
    except Exception as e:
        if settings.DEBUG:
            raise
        return Response(
            {"error": "Failed to write accounting file", "detail": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not body.strip():
        return Response(
            {"inserted": 0, "date": date_str, "file_written": True},
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

    return Response(
        {"inserted": inserted, "date": date_str, "file_written": True},
    )
