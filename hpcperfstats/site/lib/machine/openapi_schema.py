"""
Apply drf-spectacular ``@extend_schema`` metadata to machine API views.

Attributes:
  ADMIN_MONITOR_SCHEMA: Attribute.
  DROP_STAFF_SCHEMA: Attribute.
  HOME_OPTIONS_SCHEMA: Attribute.
  HOST_PLOT_SCHEMA: Attribute.
  INVALIDATE_CACHE_SCHEMA: Attribute.
  JOB_DETAIL_SCHEMA: Attribute.
  JOB_LIST_FILTER_OPTIONS_SCHEMA: Attribute.
  JOB_LIST_HISTOGRAMS_BATCH_SCHEMA: Attribute.
  JOB_LIST_HISTOGRAMS_SCHEMA: Attribute.
  JOB_LIST_SCHEMA: Attribute.
  JOB_MONITOR_GPU_SCHEMA: Attribute.
  JOB_MONITOR_SCHEMA: Attribute.
  JOB_PLOTS_SCHEMA: Attribute.
  PUBLIC_CLUSTER_DASHBOARD_SCHEMA: Attribute.
  SACCT_INGEST_SCHEMA: Attribute.
  SESSION_SCHEMA: Attribute.
  TYPE_DETAIL_SCHEMA: Attribute.
  USER_API_KEY_ROTATE_SCHEMA: Attribute.
  USER_API_KEY_SCHEMA: Attribute.
"""
from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from . import openapi_serializers as os


def _auth_responses() -> Any:
    """
    Internal helper to handle auth responses.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _auth_responses()  # doctest: +SKIP
    """
    return {
        401: OpenApiResponse(response=os.ErrorDetailSerializer, description="Authentication required"),
        403: OpenApiResponse(response=os.ErrorDetailSerializer, description="Forbidden"),
    }


def _common_error_responses() -> Any:
    """
    Document frequent non-success statuses (envelope unchanged: error/detail.
    
      keys).
    
    Returns:
      Any: Open return polymorphism from ``_common_error_responses``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _common_error_responses()  # doctest: +SKIP
    """
    return {
        404: OpenApiResponse(response=os.ErrorDetailSerializer, description="Not found"),
        409: OpenApiResponse(response=os.ErrorDetailSerializer, description="Conflict"),
        413: OpenApiResponse(response=os.ErrorDetailSerializer, description="Payload too large"),
        429: OpenApiResponse(response=os.ErrorDetailSerializer, description="Too many requests"),
        500: OpenApiResponse(response=os.ErrorDetailSerializer, description="Server error"),
    }


def _async_loading_responses() -> Any:
    """
    Internal helper to handle async loading responses.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _async_loading_responses()  # doctest: +SKIP
    """
    return {
        202: OpenApiResponse(
            response=os.JobPlotsResponseSerializer,
            description="Plots still generating; retry after retry_after_seconds",
        ),
    }


SESSION_SCHEMA = extend_schema(
    tags=["session"],
    responses={200: os.SessionInfoSerializer, **_auth_responses()},
)

USER_API_KEY_SCHEMA = extend_schema(
    tags=["session"],
    responses={200: os.UserApiKeySerializer, **_auth_responses()},
)

USER_API_KEY_ROTATE_SCHEMA = extend_schema(
    tags=["session"],
    request=None,
    responses={200: os.UserApiKeySerializer, **_auth_responses()},
)

DROP_STAFF_SCHEMA = extend_schema(
    tags=["session"],
    request=None,
    responses={200: os.DropStaffResponseSerializer, **_auth_responses()},
)

INVALIDATE_CACHE_SCHEMA = extend_schema(
    tags=["admin"],
    request=os.InvalidateCacheRequestSerializer,
    responses={
        200: os.InvalidateCacheResponseSerializer,
        400: OpenApiResponse(response=os.ErrorDetailSerializer),
        **_auth_responses(),
    },
)

HOME_OPTIONS_SCHEMA = extend_schema(
    tags=["home"],
    responses={200: os.HomeOptionsSerializer, **_auth_responses()},
)

JOB_LIST_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(name="page", type=int, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="order_by", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="username",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated usernames (OR within dimension).",
        ),
        OpenApiParameter(
            name="account",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated project/account names (exact match, OR).",
        ),
        OpenApiParameter(
            name="queue",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated queue names (OR).",
        ),
        OpenApiParameter(
            name="state",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated major terminal status group keys (OR) — completed, failed, canceled, preempted, timeout.",
        ),
        OpenApiParameter(
            name="performance_sort_rank",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated performance status ranks 0–5 (OR).",
        ),
        OpenApiParameter(name="host", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="end_time__date", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="include_filter_options",
            type=int,
            location=OpenApiParameter.QUERY,
            description="When 0, omit filter_options from the response (SPA loads them via GET /api/jobs/filter_options/). Default 1.",
        ),
    ],
    responses={
        200: os.JobListResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_LIST_FILTER_OPTIONS_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(name="page", type=int, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="order_by", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="username",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated usernames (OR within dimension).",
        ),
        OpenApiParameter(
            name="account",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated project/account names (exact match, OR).",
        ),
        OpenApiParameter(
            name="queue",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated queue names (OR).",
        ),
        OpenApiParameter(
            name="state",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated major terminal status group keys (OR).",
        ),
        OpenApiParameter(
            name="performance_sort_rank",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated performance status ranks 0–5 (OR).",
        ),
        OpenApiParameter(name="host", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="end_time__date", type=str, location=OpenApiParameter.QUERY),
    ],
    responses={
        200: os.JobListFilterOptionsResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_LIST_HISTOGRAMS_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(name="group", type=str, location=OpenApiParameter.QUERY, required=True),
        OpenApiParameter(name="metric", type=str, location=OpenApiParameter.QUERY),
    ],
    responses={
        200: os.JobListHistogramResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_LIST_HISTOGRAMS_BATCH_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(
            name="metrics",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated metric names (default: runtime,nhosts,queue_wait)",
        ),
        # Same browse/filter query keys as job_list — SPA spreads listApiParams into the batch URL.
        OpenApiParameter(name="end_time__date", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="end_time__date__gte", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="end_time__date__lte", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="username", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="account", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="queue", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="host", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="state", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="performance_sort_rank", type=str, location=OpenApiParameter.QUERY),
    ],
    responses={
        200: os.JobListHistogramBatchResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_DETAIL_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(name="light", type=int, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="defer",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated: xalt, proc, multiprecision",
        ),
    ],
    responses={
        200: os.JobDetailResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_PLOTS_SCHEMA = extend_schema(
    tags=["jobs"],
    parameters=[
        OpenApiParameter(name="plot", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="zoom", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="progressive", type=str, location=OpenApiParameter.QUERY),
    ],
    responses={
        200: os.JobPlotsResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
        **_async_loading_responses(),
    },
)

TYPE_DETAIL_SCHEMA = extend_schema(
    tags=["jobs"],
    responses={
        200: os.TypeDetailResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

HOST_PLOT_SCHEMA = extend_schema(
    summary="Host utilization plot",
    tags=["hosts"],
    parameters=[
        OpenApiParameter(name="start", type=str, location=OpenApiParameter.QUERY, exclude=True),
        OpenApiParameter(name="end", type=str, location=OpenApiParameter.QUERY, exclude=True),
        OpenApiParameter(name="host", type=str, location=OpenApiParameter.QUERY, required=True),
        OpenApiParameter(name="end_time__gte", type=str, location=OpenApiParameter.QUERY, required=True),
        OpenApiParameter(name="end_time__lte", type=str, location=OpenApiParameter.QUERY, required=False),
    ],
    responses={200: os.HostPlotResponseSerializer, **_auth_responses()},
)

ADMIN_MONITOR_SCHEMA = extend_schema(
    tags=["admin"],
    parameters=[
        OpenApiParameter(name="section", type=str, location=OpenApiParameter.QUERY, required=True),
        OpenApiParameter(name="refresh", type=str, location=OpenApiParameter.QUERY),
    ],
    responses={
        200: os.AdminMonitorResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

JOB_MONITOR_SCHEMA = extend_schema(
    tags=["monitor"],
    parameters=[
        OpenApiParameter(name="days", type=int, location=OpenApiParameter.QUERY),
    ],
    responses={200: os.JobMonitorResponseSerializer, **_auth_responses()},
)

JOB_MONITOR_GPU_SCHEMA = extend_schema(
    tags=["monitor"],
    parameters=[
        OpenApiParameter(name="username", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="usernames",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Comma-separated usernames for batch GPU rollup",
        ),
        OpenApiParameter(name="days", type=int, location=OpenApiParameter.QUERY),
    ],
    responses={200: os.JobMonitorGpuResponseSerializer, **_auth_responses()},
)

SACCT_INGEST_SCHEMA = extend_schema(
    tags=["admin"],
    parameters=[
        OpenApiParameter(name="date", type=str, location=OpenApiParameter.QUERY),
    ],
    request=None,
    responses={
        200: os.SacctIngestResponseSerializer,
        **_auth_responses(),
        **_common_error_responses(),
    },
)

PUBLIC_CLUSTER_DASHBOARD_SCHEMA = extend_schema(
    tags=["public"],
    auth=[],
    parameters=[
        OpenApiParameter(
            name="section",
            type=str,
            location=OpenApiParameter.QUERY,
            description=(
                "Lazy section. When present, grouping and period are required. "
                "Only expansion_factor is supported."
            ),
            enum=["expansion_factor"],
        ),
        OpenApiParameter(
            name="grouping",
            type=str,
            location=OpenApiParameter.QUERY,
            description=(
                "Lazy grouping. Required with section/period. "
                "monthly expects YYYY-MM; yearly expects YYYY."
            ),
            enum=["monthly", "yearly"],
        ),
        OpenApiParameter(
            name="period",
            type=str,
            location=OpenApiParameter.QUERY,
            description=(
                "Lazy period key. Required with section/grouping. "
                "Format YYYY-MM for monthly or YYYY for yearly."
            ),
            pattern=r"^(\d{4}|\d{4}-(0[1-9]|1[0-2]))$",
        ),
        OpenApiParameter(
            name="full",
            type=int,
            location=OpenApiParameter.QUERY,
            description="1 to return legacy full bundle",
        ),
    ],
    responses={
        200: os.PublicClusterDashboardSerializer,
        400: OpenApiResponse(
            response=os.ErrorDetailSerializer,
            description="Invalid or incomplete lazy query parameters",
        ),
        404: OpenApiResponse(
            response=os.ErrorDetailSerializer,
            description="Valid period syntax but artifact not available",
        ),
        429: OpenApiResponse(
            response=os.ErrorDetailSerializer,
            description="Too many requests",
        ),
    },
)
