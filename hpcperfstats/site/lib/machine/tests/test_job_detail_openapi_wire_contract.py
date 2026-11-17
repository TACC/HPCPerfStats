"""Lock job_detail JSON wire shape to OpenAPI serializers (SPA Zod validation source)."""

from __future__ import annotations

import pytest

from hpcperfstats.site.lib.machine.openapi_serializers import (
  JobDetailResponseSerializer,
)

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
  "gpu_inventory": [
    {
      "host": "c561-007",
      "dev": "0",
      "type": "nvidia_gpu",
      "util_max": 90.0,
      "util_mean": 40.0,
      "power_max_w": 250.0,
      "sample_count": 100,
    },
    {
      "host": "c561-007",
      "dev": "1",
      "type": "nvidia_gpu",
      "util_max": 5.0,
      "util_mean": 1.0,
      "power_max_w": 80.0,
      "sample_count": 100,
    },
  ],
  "xalt_data": {"exec_path": [], "cwd": [], "libset": []},
  "fsio": {},
  "schema": {},
  "derived_data_status": "ready",
  "is_shared_node": False,
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
  ser = JobDetailResponseSerializer(
    data=JOB_DETAIL_WIRE_NULL_STAFF_SAMPLE_COUNT
  )
  assert ser.is_valid(), ser.errors


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_staff_artifact_contract_matches_openapi_serializers():
  ser = JobDetailResponseSerializer(
    data=JOB_DETAIL_WIRE_STAFF_ARTIFACT_CONTRACT
  )
  assert ser.is_valid(), ser.errors


JOB_DETAIL_WIRE_SHARED_NODE = {
  **JOB_DETAIL_WIRE_BASE,
  "is_shared_node": True,
}


@pytest.mark.django_db(databases=[])
def test_job_detail_wire_with_shared_node_true_matches_openapi_serializers():
  """job_is_shared_node(jid)=True wire shape (shared-node help footer gate)."""
  ser = JobDetailResponseSerializer(data=JOB_DETAIL_WIRE_SHARED_NODE)
  assert ser.is_valid(), ser.errors
  assert ser.validated_data["is_shared_node"] is True


def _sentinel_metric_job():
  """One job whose persisted rows carry a staff-only and a visible cause."""
  from unittest.mock import MagicMock

  rows = []
  for metric, reason, codes in (
    ("avg_ibbw", "host_ib: PMU busy (2/2 hosts)", {"codes": {"-5": 2}}),
    (
      "avg_blockbw",
      "host_block: counter not present (2/2 hosts)",
      {"codes": {"-2": 2}},
    ),
  ):
    row = MagicMock()
    row.metric, row.type, row.units = metric, "host_ib", "MB/s"
    row.value, row.no_data_reason, row.no_data_codes = None, reason, codes
    rows.append(row)
  job = MagicMock()
  job.metrics_data_set.all.return_value = rows
  return job, rows


def _metrics_list(job, is_staff):
  """The api.job_detail expression for ``metrics_list``."""
  from hpcperfstats.analysis.metrics.lib.metrics import (
    build_job_metrics_display_list,
    filter_staff_only_job_metrics,
  )

  return filter_staff_only_job_metrics(
    build_job_metrics_display_list(job), is_staff
  )


def _reasons(metrics_list):
  return {r["metric"]: r["no_data_reason"] for r in metrics_list}


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize("order", [(False, True), (True, False)])
def test_staff_full_reason_regardless_of_request_order(order):
  """Researcher then staff (and the reverse) on the same job: staff always
  get the full causes, researchers only the visible one; the persisted
  ``no_data_reason`` stays the full text and the wire shape validates."""
  job, rows = _sentinel_metric_job()
  seen = {}
  for is_staff in order:
    out = _metrics_list(job, is_staff)
    seen[is_staff] = _reasons(out)
    ser = JobDetailResponseSerializer(
      data={**JOB_DETAIL_WIRE_BASE, "metrics_list": out}
    )
    assert ser.is_valid(), ser.errors
  assert seen[True]["avg_ibbw"] == "host_ib: PMU busy (2/2 hosts)"
  assert seen[False]["avg_ibbw"] is None
  assert seen[True]["avg_blockbw"] == seen[False]["avg_blockbw"]
  assert [r.no_data_reason for r in rows] == [
    "host_ib: PMU busy (2/2 hosts)",
    "host_block: counter not present (2/2 hosts)",
  ]


@pytest.mark.django_db(databases=[])
def test_sentinel_codes_never_reach_the_wire():
  """``no_data_codes`` is internal: no metrics_list row carries it."""
  job, _rows = _sentinel_metric_job()
  for is_staff in (True, False):
    assert all("no_data_codes" not in r for r in _metrics_list(job, is_staff))
