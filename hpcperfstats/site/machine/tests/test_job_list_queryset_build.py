"""Regression: job list queryset must tolerate host= alias, sort order_by, and stray GET keys."""

import pytest
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from hpcperfstats.site.machine.api import _build_job_list_queryset_from_request
from hpcperfstats.site.machine.models import job_data


@pytest.mark.django_db
def test_job_list_queryset_host_param_with_order_by_no_fielderror():
    """SPA /machine/host/:host/ sends host=; ORM must use host_list__contains, not host=."""
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    now = timezone.now()
    job_data.objects.create(
        jid="joblist-host-sort-1",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u1",
        host_list=["n1.cluster.example"],
    )

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/",
        {
            "host": "n1.cluster.example",
            "order_by": "-username",
            "page": "1",
        },
    )
    request.session = {"username": "admin", "is_staff": True}

    qs, _fields, _cur_metrics, order_by = _build_job_list_queryset_from_request(
        request,
        extra_excluded_fields=("group", "metric", "_histogram_embed_v"),
        annotate_all=True,
    )
    assert order_by == "-username"
    assert qs.count() >= 1


@pytest.mark.django_db
def test_job_list_queryset_stray_get_key_does_not_break_count():
    """Unknown query keys must not be passed to job_data.filter (FieldError)."""
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    now = timezone.now()
    job_data.objects.create(
        jid="joblist-stray-sort-1",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u2",
        host_list=["h1.example.com"],
    )

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/",
        {
            "end_time__date": str(now.date()),
            "order_by": "username",
            "page": "1",
            "utm_source": "newsletter",
        },
    )
    request.session = {"username": "admin", "is_staff": True}

    qs, _fields, _cur_metrics, _order = _build_job_list_queryset_from_request(
        request,
        extra_excluded_fields=("group", "metric", "_histogram_embed_v"),
        annotate_all=True,
    )
    assert qs.count() >= 1
