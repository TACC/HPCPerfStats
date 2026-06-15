"""Apply drf-spectacular ``@extend_schema`` metadata to machine API views."""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from . import openapi_serializers as os


def _auth_responses():
    return {
        401: OpenApiResponse(response=os.ErrorDetailSerializer, description="Authentication required"),
        403: OpenApiResponse(response=os.ErrorDetailSerializer, description="Forbidden"),
    }


def _common_error_responses():
    """Document frequent non-success statuses (envelope unchanged: error/detail keys)."""
    return {
        404: OpenApiResponse(response=os.ErrorDetailSerializer, description="Not found"),
        409: OpenApiResponse(response=os.ErrorDetailSerializer, description="Conflict"),
        413: OpenApiResponse(response=os.ErrorDetailSerializer, description="Payload too large"),
        429: OpenApiResponse(response=os.ErrorDetailSerializer, description="Too many requests"),
        500: OpenApiResponse(response=os.ErrorDetailSerializer, description="Server error"),
    }


def _async_loading_responses():
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
    ],
    responses={
        200: os.JobListResponseSerializer,
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
            description="Lazy section (expansion_factor)",
        ),
        OpenApiParameter(name="grouping", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(name="period", type=str, location=OpenApiParameter.QUERY),
        OpenApiParameter(
            name="full",
            type=int,
            location=OpenApiParameter.QUERY,
            description="1 to return legacy full bundle",
        ),
    ],
    responses={200: os.PublicClusterDashboardSerializer},
)
