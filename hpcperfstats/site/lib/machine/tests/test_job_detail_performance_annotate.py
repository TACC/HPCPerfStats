"""job_detail must annotate performance before JobListSerializer (rank 0 gate)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.site.lib.machine import cache_utils as cu

pytestmark = pytest.mark.django_db(databases=[])


def test_job_for_detail_list_serializer_uses_annotate():
    """Helper must call annotate_job_list_performance_fields for the jid."""
    from hpcperfstats.site.lib.machine import api

    fallback = MagicMock(name="fallback")
    annotated = MagicMock(name="annotated")
    ann_qs = MagicMock()
    ann_qs.first.return_value = annotated
    filter_qs = MagicMock()

    with patch.object(api.job_data.objects, "filter", return_value=filter_qs) as filt:
        with patch.object(
            api, "annotate_job_list_performance_fields", return_value=ann_qs
        ) as ann:
            out = api._job_for_detail_list_serializer("jid-1", fallback)

    filt.assert_called_once_with(jid="jid-1")
    ann.assert_called_once_with(filter_qs)
    assert out is annotated


def test_job_for_detail_list_serializer_falls_back_when_annotate_empty():
    from hpcperfstats.site.lib.machine import api

    fallback = MagicMock(name="fallback")
    ann_qs = MagicMock()
    ann_qs.first.return_value = None
    with patch.object(api.job_data.objects, "filter", return_value=MagicMock()):
        with patch.object(
            api, "annotate_job_list_performance_fields", return_value=ann_qs
        ):
            out = api._job_for_detail_list_serializer("jid-1", fallback)
    assert out is fallback


def test_job_detail_serializes_annotated_job_row():
    """Deep-link job_data must come from _job_for_detail_list_serializer."""
    from hpcperfstats.site.lib.machine import api

    jid = "test-perf-annotate-jid"
    factory = RequestFactory()
    request = factory.get(f"/api/jobs/{jid}/")
    request.session = {"username": "u1", "is_staff": False}

    t0 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    job_mock = MagicMock()
    job_mock.jid = jid
    job_mock.username = "u1"
    job_mock.start_time = t0
    job_mock.end_time = t0
    job_mock.host_list = ["n1.example.com"]
    job_mock.metrics_data_set.all.return_value = []

    def cached_se(key, timeout, fn):
        if key.startswith(f"{cu.KEY_JOB}:"):
            return job_mock
        if key.startswith(f"{cu.KEY_GPU_AGG}:"):
            return None
        if key.startswith(f"{cu.KEY_GPU_COUNT}:"):
            return None
        if key.startswith(f"{cu.KEY_PROC_LIST}:"):
            return []
        return fn()

    vis = MagicMock()
    vis.exists.return_value = True
    detail_payload = {
        "host_list": ["n1.example.com"],
        "schema": {},
        "fsio": {},
        "gpu_active": None,
        "gpu_utilization_max": None,
        "gpu_utilization_mean": None,
        "gpu_count": None,
    }
    multiprecision_payload = {
        "cpu_plot_item": None,
        "cpu_unavailable_reason": "missing",
        "gpu_plot_item": None,
        "gpu_unavailable_reason": "missing",
    }
    annotated_job = MagicMock(name="annotated_job")
    annotated_job.jid = jid
    serializer_mock = MagicMock()
    serializer_mock.return_value.data = {
        "jid": jid,
        "performance": {
            "label": "Metrics & Plots available",
            "tone": "success",
            "aria_label": "Metrics & Plots available",
            "sort_rank": 0,
        },
    }

    with ThreadPoolExecutor(max_workers=4) as executor:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(api, "_get_small_executor", return_value=executor)
            )
            stack.enter_context(patch.object(api, "_require_auth", return_value=None))
            stack.enter_context(
                patch.object(api, "_apply_non_staff_job_visibility", return_value=vis)
            )
            stack.enter_context(
                patch.object(api, "get_site_content_cache_timeout", return_value=3600)
            )
            stack.enter_context(
                patch.object(api, "build_job_metrics_display_list", return_value=[])
            )
            stack.enter_context(patch.object(api.cfg, "get_xalt_user", return_value=""))
            stack.enter_context(
                patch.object(api.cfg, "get_host_name_ext", return_value="")
            )
            stack.enter_context(patch.object(api, "cached_orm", side_effect=cached_se))
            stack.enter_context(
                patch.object(
                    api,
                    "load_job_detail_artifact",
                    side_effect=[detail_payload, multiprecision_payload],
                )
            )
            stack.enter_context(patch.object(api, "local_timezone", timezone.utc))
            list_helper = stack.enter_context(
                patch.object(
                    api,
                    "_job_for_detail_list_serializer",
                    return_value=annotated_job,
                )
            )
            stack.enter_context(patch.object(api, "JobListSerializer", serializer_mock))
            response = api.job_detail(request, jid)

    assert response.status_code == 200
    list_helper.assert_called_once()
    assert list_helper.call_args.args[0] == jid
    assert list_helper.call_args.args[1] is job_mock
    serializer_mock.assert_called_once_with(annotated_job)
    assert response.data["job_data"]["performance"]["sort_rank"] == 0
