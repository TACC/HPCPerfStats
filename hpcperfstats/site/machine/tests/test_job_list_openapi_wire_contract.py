"""Lock job_list JSON wire shape to OpenAPI serializers (SPA Zod validation source)."""
from __future__ import annotations

import pytest

from hpcperfstats.site.machine.openapi_serializers import JobListResponseSerializer

pytestmark = pytest.mark.machine_unit_mock

# Representative GET /api/jobs/ payload from JobListSerializer + job_list view fields.
JOB_LIST_WIRE_EXAMPLE = {
    "nj": 5,
    "job_list": [
        {
            "jid": "12345",
            "submit_time": "2024-06-01T12:00:00",
            "start_time": "2024-06-01T12:05:00",
            "end_time": "2024-06-01T14:00:00",
            "runtime": 6900.0,
            "username": "alice",
            "account": "proj",
            "queue": "normal",
            "state": "COMPLETED",
            "host_list": ["n001.cluster.example", "n002.cluster.example"],
            "performance": {
                "label": "Summary available",
                "tone": "success",
                "aria_label": "Performance: Summary available",
                "sort_rank": 0,
            },
            "color": "#28a745",
        },
    ],
    "filter_summary": ["Queue: normal", "User: alice"],
    "filter_options": {
        "usernames": ["alice"],
        "accounts": ["proj"],
        "queues": ["normal"],
        "states": ["COMPLETED"],
        "performance_statuses": [{"sort_rank": 0, "label": "Summary available"}],
        "truncated": {
            "usernames": False,
            "accounts": False,
            "queues": False,
            "states": False,
        },
    },
    "qname": "Filtered jobs",
    "order_by": "-end_time",
    "current_path": "/api/jobs/?queue=normal&username=alice",
    "aggregates": {"total_node_hours": 100.5},
    "pagination": {
        "page": 1,
        "num_pages": 1,
        "has_previous": False,
        "has_next": False,
        "previous_page_number": None,
        "next_page_number": None,
    },
}


# Nullable job_data columns emit JSON null in JobListSerializer output.
JOB_LIST_NULLABLE_WIRE_EXAMPLE = {
    **JOB_LIST_WIRE_EXAMPLE,
    "nj": 1,
    "job_list": [
        {
            "jid": "99999",
            "submit_time": "2024-06-01T12:00:00",
            "start_time": "2024-06-01T12:05:00",
            "end_time": "2024-06-01T14:00:00",
            "runtime": 6900.0,
            "username": "bob",
            "account": None,
            "queue": None,
            "state": None,
            "QOS": None,
            "jobname": None,
            "sample_count": None,
            "host_list": ["n003.cluster.example"],
            "performance": {
                "label": "Summary available",
                "tone": "success",
                "aria_label": "Performance: Summary available",
                "sort_rank": 0,
            },
            "color": "#6c757d",
        },
    ],
}


@pytest.mark.django_db(databases=[])
def test_job_list_wire_example_matches_openapi_serializers():
    ser = JobListResponseSerializer(data=JOB_LIST_WIRE_EXAMPLE)
    assert ser.is_valid(), ser.errors


@pytest.mark.django_db(databases=[])
def test_job_list_nullable_wire_fields_match_openapi_serializers():
    ser = JobListResponseSerializer(data=JOB_LIST_NULLABLE_WIRE_EXAMPLE)
    assert ser.is_valid(), ser.errors
