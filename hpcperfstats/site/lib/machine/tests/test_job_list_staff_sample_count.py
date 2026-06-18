"""Staff-only job_list field: sample_count."""
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.utils import timezone

from hpcperfstats.site.lib.machine.models import job_data


def _create_job_for_sample_count(jid, sample_count):
    now = timezone.now()
    return job_data.objects.create(
        jid=jid,
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u1",
        host_list=["n1.example.com"],
        metrics_distinct_time_count=sample_count,
    )


@pytest.mark.django_db
def test_job_list_includes_sample_count_for_staff():
    from hpcperfstats.site.lib.machine import api

    job = _create_job_for_sample_count("job-list-staff-1", 1234)
    request = RequestFactory().get("/api/job-list/")
    request.session = {"username": "u1", "is_staff": True}

    with patch.object(api, "check_for_tokens", return_value=True), patch.object(
        api,
        "_build_job_list_queryset_from_request",
        return_value=(job_data.objects.filter(pk=job.pk), {}, None, "-end_time"),
    ):
        response = api.job_list(request)

    assert response.status_code == 200
    assert response.data["job_list"][0]["sample_count"] == 1234


@pytest.mark.django_db
def test_job_list_omits_sample_count_for_non_staff():
    from hpcperfstats.site.lib.machine import api

    job = _create_job_for_sample_count("job-list-staff-2", 99)
    request = RequestFactory().get("/api/job-list/")
    request.session = {"username": "u1", "is_staff": False}

    with patch.object(api, "check_for_tokens", return_value=True), patch.object(
        api,
        "_build_job_list_queryset_from_request",
        return_value=(job_data.objects.filter(pk=job.pk), {}, None, "-end_time"),
    ):
        response = api.job_list(request)

    assert response.status_code == 200
    assert "sample_count" not in response.data["job_list"][0]
