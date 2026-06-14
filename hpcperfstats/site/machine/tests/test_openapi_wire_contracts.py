"""Lock SPA-facing API wire JSON to openapi_serializers (Orval Zod validation source).

Fix order when drift is found:
1. Hard Zod fails (parseApiResponse throws on real wire)
2. Silent strip routes (validation passes but strips payload keys)
3. Wire contract tests + Orval regen (openapi.yaml, npm run generate:api)
"""
from __future__ import annotations

import pytest

from hpcperfstats.site.machine.openapi_serializers import (
    AdminMonitorResponseSerializer,
    JobDetailResponseSerializer,
    JobListHistogramResponseSerializer,
    JobMonitorGpuResponseSerializer,
    JobMonitorResponseSerializer,
    JobPlotsResponseSerializer,
    TypeDetailResponseSerializer,
)

# --- Hard fail fixes (Zod threw on real Django wire) ---

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

JOB_DETAIL_WIRE = {
    "job_data": {"jid": "12345", "host_list": ["n001.cluster.example"]},
    "host_list": ["n001.cluster.example"],
    "proc_list": ["python", "mpirun"],
    "gpu_active": 1,
    "gpu_utilization_max": 95.0,
    "gpu_utilization_mean": 80.0,
    "gpu_count": 4,
    "metrics_list": [{"metric": "runtime", "label": "Runtime", "units": "s"}],
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
    "mplot_item": {"type": "plot"},
    "mplot_unavailable_reason": None,
    "rscript": "",
    "rdiv": "",
    "rplot_item": {"type": "plot"},
    "rplot_unavailable_reason": None,
    "grscript": "",
    "grdiv": "",
    "grplot_item": {"type": "plot"},
    "grplot_unavailable_reason": None,
    "status": "ready",
    "progressive": True,
    "loading_plots": [],
}

JOB_LIST_HISTOGRAM_WIRE = {
    "group": "metric",
    "metric": "runtime",
    "nj": 10,
    "title": "Runtime",
    "plot_item_thumb": {"type": "plot"},
    "plot_item_full": {"type": "plot"},
    "plot_unavailable_reason": None,
}

TYPE_DETAIL_WIRE = {
    "type_name": "cpu",
    "jobid": "12345",
    "tplot_item": {"type": "plot"},
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
        (JobDetailResponseSerializer, JOB_DETAIL_WIRE),
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
    ],
)
def test_openapi_silent_strip_wire_examples_match_serializers(serializer_cls, wire):
    _assert_wire_valid(serializer_cls, wire)
