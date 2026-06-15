"""Regression tests for comma-separated job list header filter params."""
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.site.machine.query_utils import (
    apply_job_list_header_acct_multi_filters,
    job_list_multi_value_orm_kwargs,
    parse_job_list_multi_value_field,
    parse_job_list_performance_sort_ranks,
)

pytestmark = pytest.mark.machine_unit_mock


def test_parse_job_list_multi_value_field_dedupes_and_trims():
    assert parse_job_list_multi_value_field(" alice, bob ,alice ") == ["alice", "bob"]


def test_job_list_multi_value_orm_kwargs_single_vs_in():
    assert job_list_multi_value_orm_kwargs("queue", ["normal"]) == {"queue": "normal"}
    assert job_list_multi_value_orm_kwargs("queue", ["normal", "debug"]) == {
        "queue__in": ["normal", "debug"],
    }


def test_apply_job_list_header_acct_multi_filters_pops_keys():
    remaining, kwargs = apply_job_list_header_acct_multi_filters(
        {"username": "alice,bob", "queue": "normal", "runtime__gte": "1"}
    )
    assert "username" not in remaining
    assert "queue" not in remaining
    assert remaining["runtime__gte"] == "1"
    assert kwargs == {
        "username__in": ["alice", "bob"],
        "queue": "normal",
    }


def test_parse_job_list_performance_sort_ranks():
    assert parse_job_list_performance_sort_ranks("0,2,99,x,0") == [0, 2]


def test_build_job_list_queryset_applies_major_state_filter():
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/jobs/", {"state": "canceled,completed"})
    request.session = {"username": "admin", "is_staff": True}

    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain

    with patch.object(api.job_data.objects, "filter", return_value=chain) as mock_filter, patch.object(
        api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
    ), patch.object(
        api, "normalize_job_list_query_params", side_effect=lambda f: f
    ), patch.object(
        api, "expand_month_date_to_range", side_effect=lambda f: f
    ), patch.object(
        api, "get_job_list_order_by", return_value="-end_time"
    ), patch.object(
        api, "annotate_job_list_performance_fields", return_value=chain
    ):
        api._build_job_list_queryset_from_request(request, annotate_all=True)

    mock_filter.assert_called_once()
    assert chain.filter.called
    assert "state__istartswith" in str(chain.filter.call_args)


def test_build_job_list_queryset_applies_timeout_state_filter():
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/jobs/", {"state": "timeout"})
    request.session = {"username": "admin", "is_staff": True}

    chain = MagicMock()
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
        api, "annotate_job_list_performance_fields", return_value=chain
    ):
        api._build_job_list_queryset_from_request(request, annotate_all=True)

    assert chain.filter.called
    assert "TIMEOUT" in str(chain.filter.call_args)


def test_build_job_list_queryset_applies_multi_username_filter():
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/jobs/", {"username": "alice,bob"})
    request.session = {"username": "admin", "is_staff": True}

    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain

    with patch.object(api.job_data.objects, "filter", return_value=chain) as mock_filter, patch.object(
        api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
    ), patch.object(
        api, "normalize_job_list_query_params", side_effect=lambda f: f
    ), patch.object(
        api, "expand_month_date_to_range", side_effect=lambda f: f
    ), patch.object(
        api, "get_job_list_order_by", return_value="-end_time"
    ), patch.object(
        api, "annotate_job_list_performance_fields", return_value=chain
    ):
        api._build_job_list_queryset_from_request(request, annotate_all=True)

    mock_filter.assert_called_once()
    assert mock_filter.call_args.kwargs == {
        "username__in": ["alice", "bob"],
    }
