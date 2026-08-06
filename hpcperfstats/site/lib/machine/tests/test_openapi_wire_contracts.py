"""Lock SPA-facing API wire JSON to openapi_serializers (Orval Zod validation source).

Fix order when drift is found:
1. Hard Zod fails (parseApiResponse throws on real wire)
2. Silent strip routes (validation passes but strips payload keys)
3. Wire contract tests + Orval regen (openapi.yaml, npm run generate:api)
"""
from __future__ import annotations

import pytest

from hpcperfstats.site.lib.machine.openapi_serializers import (
    AdminMonitorResponseSerializer,
    JobDetailResponseSerializer,
    JobListHistogramResponseSerializer,
    JobMonitorGpuResponseSerializer,
    JobMonitorResponseSerializer,
    JobPlotsResponseSerializer,
    PublicClusterDashboardSerializer,
    TypeDetailResponseSerializer,
)

pytestmark = pytest.mark.machine_unit_mock

# Minimal Bokeh json_item document (matches frontend bokeh-fixtures.ts).
VALID_BOKEH_JSON_ITEM = {
    "doc": {
        "root_ids": ["p1001"],
        "roots": [{"id": "p1001", "type": "object", "name": "GridPlot"}],
    },
}

JOB_MONITOR_WIRE = {
    "window_days": 30,
    "start_time": "2024-01-01T00:00:00+00:00",
    "end_time": "2024-02-01T00:00:00+00:00",
    "results": [
        {
            "username": "alice",
            "total_jobs": 10,
            "failed_jobs": 1,
            "failed_rate": 10.0,
            "timedout_jobs": 0,
            "timedout_rate": 0.0,
        },
    ],
}

JOB_MONITOR_GPU_WIRE = {
    "username": "alice",
    "gpu_count_total": 4,
    "gpu_active_total": 2,
    "gpu_active_percentage": 50.0,
    "has_data": True,
}

JOB_MONITOR_GPU_BATCH_WIRE = {
    "results": [
        {
            "username": "alice",
            "gpu_count_total": 4,
            "gpu_active_total": 2,
            "gpu_active_percentage": 50.0,
            "has_data": True,
        },
        {
            "username": "bob",
            "gpu_count_total": 0,
            "gpu_active_total": 0,
            "gpu_active_percentage": 0.0,
            "has_data": False,
        },
    ],
}

PUB_DASHBOARD_META_WIRE = {
    "status": "ready",
    "machine_name": "test.cluster.example",
    "detail": None,
    "retry_hint": None,
    "schema_version": 1,
    "sections": {
        "expansion_factor": {
            "monthly_period_keys": ["2024-02", "2024-01"],
            "yearly_period_keys": ["2024", "2023"],
        },
    },
}

PUB_DASHBOARD_LAZY_PERIOD_WIRE = {
    "status": "ready",
    "machine_name": "test.cluster.example",
    "section": "expansion_factor",
    "grouping": "monthly",
    "period_key": "2024-01",
    "block": {
        "scheduler_expansion_factor_daily_means_in_month_count": 28,
        "histogram_bin_edges": [0.0, 1.0, 2.0],
        "histogram_counts": [5, 10, 3],
        "expansion_factor_definition": "(queue_wait_seconds + runtime_seconds) / (ncores * runtime_seconds)",
        "bokeh_histogram_json_item": VALID_BOKEH_JSON_ITEM,
    },
}

PUB_DASHBOARD_LEGACY_FULL_WIRE = {
    "status": "ready",
    "machine_name": "test.cluster.example",
    "expansion_factors": {"cpu": 1.2},
    "monthly_metrics": [{"title": "Jan", "bokeh_histogram_json_item": VALID_BOKEH_JSON_ITEM}],
}

JOB_DETAIL_WIRE = {
    "job_data": {"jid": "12345", "host_list": ["n001.cluster.example"]},
    "host_list": ["n001.cluster.example"],
    "proc_list": [
        {
            "host": "n001.cluster.example",
            "proc": "python",
            "device": "python/1234/0-31/0",
            "uid": 1000,
            "vm_rss": 102400,
            "vm_hwm": 204800,
            "vm_size": 512000,
            "threads": 4,
        },
        {
            "host": "n001.cluster.example",
            "proc": "mpirun",
            "device": "mpirun/5678/0-31/0",
            "uid": 1000,
            "vm_rss": 8192,
            "vm_hwm": 8192,
            "vm_size": 16384,
            "threads": 1,
        },
    ],
    "gpu_active": 1,
    "gpu_utilization_max": 95.0,
    "gpu_utilization_mean": 80.0,
    "gpu_count": 4,
    "gpu_inventory": [],
    "metrics_list": [
        {
            "type": "cpu",
            "metric": "avg_cpuusage",
            "units": "#cores",
            "value": 2.25,
            "no_data_reason": None,
        },
    ],
    "xalt_data": {"exec_path": [], "cwd": [], "libset": []},
    "fsio": {},
    "schema": {},
    "derived_data_status": "ready",
    "client_url": "https://example.test/logs",
    "server_url": "https://example.test/server-logs",
    "multiprecision_cpu_plot_item": None,
    "multiprecision_cpu_unavailable_reason": None,
    "multiprecision_gpu_plot_item": None,
    "multiprecision_gpu_unavailable_reason": None,
    "staff_metrics_distinct_time_count": None,
    "staff_artifact_contract": {
        "current_plot": 11,
        "current_detail": 8,
        "db_plot": [11],
        "db_detail": [8],
    },
}

# --- Silent strip fixes (Zod passed but deleted wire keys) ---

ADMIN_MONITOR_HOSTS_WIRE = {
    "host_stats": [
        {
            "host": "n001.cluster.example",
            "last_time": "2024-06-01T12:00:00+00:00",
            "age_bucket": "ok",
        },
    ],
}

JOB_PLOTS_WIRE = {
    "mscript": "",
    "mdiv": "",
    "mplot_item": VALID_BOKEH_JSON_ITEM,
    "mplot_unavailable_reason": None,
    "rscript": "",
    "rdiv": "",
    "rplot_item": VALID_BOKEH_JSON_ITEM,
    "rplot_unavailable_reason": None,
    "grscript": "",
    "grdiv": "",
    "grplot_item": VALID_BOKEH_JSON_ITEM,
    "grplot_unavailable_reason": None,
    "grplot_bw_axis": None,
    "status": "ready",
    "progressive": True,
    "loading_plots": [],
}

JOB_LIST_HISTOGRAM_WIRE = {
    "group": "metric",
    "metric": "runtime",
    "nj": 10,
    "title": "Runtime",
    "plot_item_thumb": VALID_BOKEH_JSON_ITEM,
    "plot_item_full": VALID_BOKEH_JSON_ITEM,
    "plot_unavailable_reason": None,
}

TYPE_DETAIL_WIRE = {
    "type_name": "cpu",
    "jobid": "12345",
    "tplot_item": VALID_BOKEH_JSON_ITEM,
    "tplot_unavailable_reason": None,
    "stats_data": [],
    "schema": [],
    "status": "ready",
}


def _assert_wire_valid(serializer_cls, wire):
    ser = serializer_cls(data=wire)
    assert ser.is_valid(), ser.errors


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize(
    "serializer_cls,wire",
    [
        (JobMonitorResponseSerializer, JOB_MONITOR_WIRE),
        (JobMonitorGpuResponseSerializer, JOB_MONITOR_GPU_WIRE),
        (JobMonitorGpuResponseSerializer, JOB_MONITOR_GPU_BATCH_WIRE),
        (JobDetailResponseSerializer, JOB_DETAIL_WIRE),
        (PublicClusterDashboardSerializer, PUB_DASHBOARD_META_WIRE),
        (PublicClusterDashboardSerializer, PUB_DASHBOARD_LAZY_PERIOD_WIRE),
    ],
)
def test_openapi_hard_fail_wire_examples_match_serializers(serializer_cls, wire):
    _assert_wire_valid(serializer_cls, wire)


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize(
    "serializer_cls,wire",
    [
        (AdminMonitorResponseSerializer, ADMIN_MONITOR_HOSTS_WIRE),
        (JobPlotsResponseSerializer, JOB_PLOTS_WIRE),
        (JobListHistogramResponseSerializer, JOB_LIST_HISTOGRAM_WIRE),
        (TypeDetailResponseSerializer, TYPE_DETAIL_WIRE),
        (PublicClusterDashboardSerializer, PUB_DASHBOARD_LEGACY_FULL_WIRE),
    ],
)
def test_openapi_silent_strip_wire_examples_match_serializers(serializer_cls, wire):
    _assert_wire_valid(serializer_cls, wire)
