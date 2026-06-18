"""Tests for deferred job list filter_options loading."""
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.site.lib.machine import api

pytestmark = pytest.mark.machine_unit_mock


def test_include_filter_options_parses_zero():
    request = RequestFactory().get("/api/jobs/", {"include_filter_options": "0"})
    assert api._include_filter_options(request) is False


def test_include_filter_options_defaults_true():
    request = RequestFactory().get("/api/jobs/")
    assert api._include_filter_options(request) is True


@patch("hpcperfstats.site.lib.machine.api._require_auth", return_value=None)
@patch("hpcperfstats.site.lib.machine.api._resolve_job_list_filter_options")
@patch("hpcperfstats.site.lib.machine.api._build_job_list_queryset_from_request")
def test_job_list_skips_filter_options_when_include_zero(
    mock_build_qs,
    mock_resolve_options,
    _mock_auth,
):
    chain = MagicMock()
    chain.count.return_value = 0
    mock_build_qs.return_value = (chain, {}, {}, "-end_time")

    request = RequestFactory().get("/api/jobs/", {"include_filter_options": "0"})
    response = api.job_list(request)

    assert response.status_code == 200
    assert response.data["filter_options"] is None
    mock_resolve_options.assert_not_called()


@patch("hpcperfstats.site.lib.machine.api._require_auth", return_value=None)
@patch("hpcperfstats.site.lib.machine.api._resolve_job_list_filter_options")
def test_job_list_filter_options_view_returns_options(mock_resolve, _mock_auth):
    mock_resolve.return_value = {"usernames": ["alice"], "accounts": [], "queues": []}

    request = RequestFactory().get("/api/jobs/filter_options/")
    response = api.job_list_filter_options_view(request)

    assert response.status_code == 200
    assert response.data["filter_options"]["usernames"] == ["alice"]
    mock_resolve.assert_called_once()
