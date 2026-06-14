"""Lock job list histogram batch JSON wire shape to OpenAPI serializers."""
from __future__ import annotations

from hpcperfstats.site.machine.openapi_serializers import JobListHistogramBatchResponseSerializer

JOB_LIST_HISTOGRAM_BATCH_WIRE_EXAMPLE = {
    "nj": 20000,
    "histogram_nj": 5000,
    "histogram_sampled": True,
    "histograms": [
        {
            "group": "metric",
            "metric": "runtime",
            "nj": 20000,
            "histogram_nj": 5000,
            "histogram_sampled": True,
            "title": "Number of jobs by cpu hours",
            "plot_item_thumb": {
                "target_id": "hist-runtime-thumb",
                "root_id": "hist-runtime-thumb",
                "doc": {"version": "3.4.0", "roots": [{"type": "object", "name": "Figure", "id": "hist-runtime-thumb"}]},
            },
            "plot_item_full": {
                "target_id": "hist-runtime-full",
                "root_id": "hist-runtime-full",
                "doc": {"version": "3.4.0", "roots": [{"type": "object", "name": "Figure", "id": "hist-runtime-full"}]},
            },
            "plot_unavailable_reason": None,
        },
    ],
}


def test_job_list_histogram_batch_wire_example_matches_openapi_serializers():
    ser = JobListHistogramBatchResponseSerializer(data=JOB_LIST_HISTOGRAM_BATCH_WIRE_EXAMPLE)
    assert ser.is_valid(), ser.errors
