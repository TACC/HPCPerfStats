"""Regression: job list queryset must tolerate search params and stray GET keys."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from hpcperfstats.site.lib.machine.api import _build_job_list_queryset_from_request
from hpcperfstats.site.lib.machine.models import job_data, metrics_data


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


@pytest.mark.django_db
def test_job_list_queryset_sample_count_desc_sorts_nulls_last():
    """Descending sample_count keeps blank values at the end of the list."""
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    now = timezone.now()
    job_data.objects.create(
        jid="joblist-sample-sort-100",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u3",
        host_list=["s1.example.com"],
        metrics_distinct_time_count=100,
    )
    job_data.objects.create(
        jid="joblist-sample-sort-10",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u3",
        host_list=["s1.example.com"],
        metrics_distinct_time_count=10,
    )
    job_data.objects.create(
        jid="joblist-sample-sort-null",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="u3",
        host_list=["s1.example.com"],
        metrics_distinct_time_count=None,
    )

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/",
        {
            "host": "s1.example.com",
            "order_by": "-sample_count",
            "page": "1",
        },
    )
    request.session = {"username": "admin", "is_staff": True}

    qs, _fields, _cur_metrics, order_by = _build_job_list_queryset_from_request(
        request,
        extra_excluded_fields=("group", "metric", "_histogram_embed_v"),
        annotate_all=True,
    )
    assert order_by == "-metrics_distinct_time_count"
    values = list(qs.values_list("metrics_distinct_time_count", flat=True))
    assert values[:2] == [100, 10]
    assert values[-1] is None


def _create_extended_search_jobs():
    now = timezone.now()
    match = job_data.objects.create(
        jid="extended-search-match",
        submit_time=now - timedelta(hours=3),
        start_time=now - timedelta(hours=2),
        end_time=now,
        runtime=7200.0,
        node_hrs=4.0,
        nhosts=2,
        ncores=128,
        username="alice",
        account="project-alpha",
        queue="normal",
        state="COMPLETED",
        host_list=["n001.cluster.example"],
    )
    other = job_data.objects.create(
        jid="extended-search-other",
        submit_time=now - timedelta(days=31, hours=2),
        start_time=now - timedelta(days=31, hours=1),
        end_time=now - timedelta(days=30),
        runtime=30.0,
        node_hrs=0.5,
        nhosts=1,
        ncores=64,
        username="bob",
        account="project-beta",
        queue="debug",
        state="FAILED",
        host_list=["n002.cluster.example"],
    )
    return now, match, other


@pytest.mark.django_db
def test_job_list_queryset_filters_every_extended_search_accounting_parameter():
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    now, match, other = _create_extended_search_jobs()
    factory = RequestFactory()
    cases = [
        ("jid", match.jid, match.jid),
        ("host", "n001.cluster.example", match.jid),
        ("username", "alice", match.jid),
        ("account__icontains", "alpha", match.jid),
        ("state", "COMPLETED", match.jid),
        ("queue", "normal", match.jid),
        ("end_time__gte", (now - timedelta(days=1)).date().isoformat(), match.jid),
        ("end_time__lte", (now - timedelta(days=10)).date().isoformat(), other.jid),
        ("runtime__gte", "3600", match.jid),
        ("runtime__lte", "60", other.jid),
        ("nhosts__gte", "2", match.jid),
        ("nhosts__lte", "1", other.jid),
        ("node_hrs__gte", "2", match.jid),
        ("node_hrs__lte", "1", other.jid),
    ]

    for param, value, expected_jid in cases:
        request = factory.get("/api/jobs/", {param: value})
        request.session = {"username": "admin", "is_staff": True}
        qs, _fields, _cur_metrics, _order = _build_job_list_queryset_from_request(request)
        assert list(qs.values_list("jid", flat=True)) == [expected_jid], param


@pytest.mark.django_db
def test_job_list_queryset_filters_derived_metrics_from_prewarmed_metrics_data():
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    _now, match, other = _create_extended_search_jobs()
    metrics_data.objects.create(
        jid=match,
        type="pmc",
        metric="avg_freq",
        units="GHz",
        value=2.5,
    )
    metrics_data.objects.create(
        jid=other,
        type="pmc",
        metric="avg_freq",
        units="GHz",
        value=0.5,
    )
    factory = RequestFactory()
    cases = [
        ("metrics_avg_freq__gte", "2.0", match.jid),
        ("metrics_avg_freq__lte", "1.0", other.jid),
    ]

    for param, value, expected_jid in cases:
        request = factory.get("/api/jobs/", {param: value})
        request.session = {"username": "admin", "is_staff": True}
        qs, _fields, cur_metrics, _order = _build_job_list_queryset_from_request(request)
        assert cur_metrics == {param.split("_", 1)[1]: value}
        assert list(qs.values_list("jid", flat=True)) == [expected_jid], param


@pytest.mark.django_db
def test_job_list_queryset_multi_metric_filters_do_not_inflate_count():
    """Two metric filters must not duplicate rows (count equals distinct matching jids)."""
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    now = timezone.now()
    high_freq = job_data.objects.create(
        jid="joblist-metric-dup-high",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="metric-dup-user",
        host_list=["m1.example.com"],
    )
    low_freq = job_data.objects.create(
        jid="joblist-metric-dup-low",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=60.0,
        username="metric-dup-user",
        host_list=["m1.example.com"],
    )
    metrics_data.objects.create(
        jid=high_freq,
        type="pmc",
        metric="avg_freq",
        units="GHz",
        value=3.0,
    )
    metrics_data.objects.create(
        jid=high_freq,
        type="pmc",
        metric="avg_cpi",
        units="",
        value=0.5,
    )
    metrics_data.objects.create(
        jid=low_freq,
        type="pmc",
        metric="avg_freq",
        units="GHz",
        value=1.0,
    )
    metrics_data.objects.create(
        jid=low_freq,
        type="pmc",
        metric="avg_cpi",
        units="",
        value=2.0,
    )

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/",
        {
            "username": "metric-dup-user",
            "metrics_avg_freq__gte": "2.0",
            "metrics_avg_cpi__lte": "1.0",
        },
    )
    request.session = {"username": "admin", "is_staff": True}

    qs, _fields, cur_metrics, _order = _build_job_list_queryset_from_request(request)

    assert cur_metrics == {"avg_freq__gte": "2.0", "avg_cpi__lte": "1.0"}
    assert qs.count() == 1
    assert list(qs.values_list("jid", flat=True)) == ["joblist-metric-dup-high"]


@pytest.mark.django_db
def test_job_list_queryset_ignores_unsupported_derived_metric_operators():
    if connection.vendor != "postgresql":
        pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific in this project")

    _create_extended_search_jobs()
    factory = RequestFactory()
    request = factory.get("/api/jobs/", {"metrics_avg_freq__contains": "2"})
    request.session = {"username": "admin", "is_staff": True}

    qs, _fields, cur_metrics, _order = _build_job_list_queryset_from_request(request)

    assert cur_metrics == {"avg_freq__contains": "2"}
    assert qs.count() == 2


@pytest.mark.machine_unit_mock
def test_bare_metrics_batch_param_does_not_break_queryset_build():
    """Histogram batch ``metrics=runtime,...`` must not collide with metric filter parsing."""
    from hpcperfstats.site.lib.machine import api
    from hpcperfstats.site.lib.machine.api import _JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {
            "metrics": "runtime,nhosts,queue_wait",
            "end_time__date": "2026-06",
        },
    )
    request.session = {"username": "admin", "is_staff": True}

    chain = MagicMock()
    chain.count.return_value = 2
    chain.filter.return_value = chain
    chain.order_by.return_value = chain

    with patch.object(api.job_data.objects, "filter", return_value=chain), patch.object(
        api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
    ), patch.object(
        api, "normalize_job_list_query_params", side_effect=lambda f: f
    ), patch.object(
        api, "expand_month_date_to_range", side_effect=lambda f: f
    ), patch.object(
        api, "get_job_list_order_by", return_value="-end_time"
    ), patch.object(
        api, "partition_job_list_acct_filters", return_value=({}, None)
    ), patch.object(
        api, "annotate_job_list_performance_fields", return_value=chain
    ):
        qs, _fields, cur_metrics, _order = api._build_job_list_queryset_from_request(
            request,
            extra_excluded_fields=_JOB_LIST_QUERY_FIELD_EXCLUDES_HISTOGRAM,
            annotate_all=True,
        )
        _hist_qs, nj, _fields2, cur_metrics2 = api._build_histogram_queryset(request)

    assert cur_metrics == {}
    assert cur_metrics2 == {}
    assert nj == 2
    assert nj == qs.count()
    assert nj == _hist_qs.count()
