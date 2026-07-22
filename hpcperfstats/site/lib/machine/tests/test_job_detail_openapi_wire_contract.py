"""Lock job_detail JSON wire shape to OpenAPI serializers (SPA Zod validation source)."""
from __future__ import annotations

import pytest

from hpcperfstats.site.lib.machine.openapi_serializers import JobDetailResponseSerializer

pytestmark = pytest.mark.machine_unit_mock

# Representative GET /api/jobs/{pk}/ payload from job_detail view assembly.
JOB_DETAIL_WIRE_BASE = {
    "job_data": {
        "jid": "737412",
        "submit_time": "2024-06-01T12:00:00",
        "start_time": "2024-06-01T12:05:00",
        "end_time": "2024-06-01T14:00:00",
        "runtime": 6900.0,
        "username": "alice",
        "account": "proj",
        "queue": "normal",
        "state": "COMPLETED",
        "host_list": ["n001.cluster.example"],
    },
    "host_list": ["n001.cluster.example"],
    "proc_list": ["python", "mpirun"],
    "gpu_active": 1,
    "gpu_utilization_max": 95.0,
    "gpu_utilization_mean": 80.0,
    "gpu_count": 4,
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

# build_job_metrics_display_list emits numeric value from metrics_data.value (FloatField).
JOB_DETAIL_WIRE_WITH_METRICS = {
    **JOB_DETAIL_WIRE_BASE,
    "metrics_list": [
        {
            "type": "cpu",
            "metric": "avg_cpuusage",
            "units": "#cores",
            "value": 2.25,
            "no_data_reason": None,
        },
    ],
}

# Rows with null value + explanatory no_data_reason (common on job detail).
JOB_DETAIL_WIRE_NULL_METRICS = {
    **JOB_DETAIL_WIRE_BASE,
    "metrics_list": [
        {
            "type": "mem",
            "metric": "mem_hwm",
            "units": "GiB",
            "value": None,
            "no_data_reason": "No usable memory telemetry for high-water mark",
        },
    ],
}


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_numeric_metric_value_matches_openapi_serializers():
    ser = JobDetailResponseSerializer(data=JOB_DETAIL_WIRE_WITH_METRICS)
    assert ser.is_valid(), ser.errors


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_null_metric_value_matches_openapi_serializers():
    ser = JobDetailResponseSerializer(data=JOB_DETAIL_WIRE_NULL_METRICS)
    assert ser.is_valid(), ser.errors


JOB_DETAIL_WIRE_NULL_STAFF_SAMPLE_COUNT = {
    **JOB_DETAIL_WIRE_BASE,
    "staff_metrics_distinct_time_count": None,
}


JOB_DETAIL_WIRE_STAFF_ARTIFACT_CONTRACT = {
    **JOB_DETAIL_WIRE_BASE,
    "staff_metrics_distinct_time_count": 1250,
    "staff_artifact_contract": {
        "current_plot": 11,
        "current_detail": 8,
        "db_plot": [10, 11],
        "db_detail": [],
    },
}


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_null_staff_metrics_distinct_time_count_matches_openapi_serializers():
    ser = JobDetailResponseSerializer(data=JOB_DETAIL_WIRE_NULL_STAFF_SAMPLE_COUNT)
    assert ser.is_valid(), ser.errors


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_staff_artifact_contract_matches_openapi_serializers():
    ser = JobDetailResponseSerializer(data=JOB_DETAIL_WIRE_STAFF_ARTIFACT_CONTRACT)
    assert ser.is_valid(), ser.errors
