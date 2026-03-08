"""Unit tests for job list histograms endpoint and _job_list_histograms helper.

Tests the split histogram API (job_list_histograms) and that job_list no longer
returns script/div. Run with pytest; requires Django (site.machine.tests).
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.mark.django_db
class TestJobListHistogramsView:
    """Tests for the job_list_histograms API view."""

    def test_returns_401_when_not_authenticated(self):
        """job_list_histograms returns 401 when check_for_tokens is False."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/")

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=False):
            response = job_list_histograms(request)

        assert response.status_code == 401

    def test_returns_200_with_script_and_div_when_authenticated(self):
        """job_list_histograms returns 200 and JSON with script/div when authenticated."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/")

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert "script" in data
        assert "div" in data
        assert isinstance(data["script"], str)
        assert isinstance(data["div"], str)

    def test_histograms_endpoint_uses_same_query_params_as_job_list(self):
        """job_list_histograms accepts the same GET params as job list (e.g. page ignored for histograms)."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"page": "2"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert "script" in data and "div" in data


def test_job_list_histograms_helper_returns_empty_when_no_jobs():
    """_job_list_histograms returns ('', '') when queryset count is 0."""
    from hpcperfstats.site.machine.api import _job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/")

    mock_qs = MagicMock()
    mock_qs.count.return_value = 0

    with patch("hpcperfstats.site.machine.api.job_data") as mock_job_data:
        mock_job_data.objects.filter.return_value.order_by.return_value = mock_qs
        script, div = _job_list_histograms(request)

    assert script == ""
    assert div == ""


def test_job_list_histograms_helper_returns_tuple_of_strings():
    """_job_list_histograms returns (script, div) as two strings when data and components are mocked."""
    from hpcperfstats.site.machine.api import _job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/")

    base_ts = datetime(2025, 1, 15, 12, 0, 0)
    mock_qs = MagicMock()
    mock_qs.count.return_value = 1
    mock_qs.values.return_value = [
        {
            "jid": "12345",
            "start_time": base_ts,
            "submit_time": base_ts,
            "runtime": 3600.0,
            "nhosts": 1,
        }
    ]

    with patch("hpcperfstats.site.machine.api.job_data") as mock_job_data, patch(
        "hpcperfstats.site.machine.api.metrics_data"
    ) as mock_metrics_data, patch(
        "hpcperfstats.site.machine.api.job_hist", return_value=MagicMock()
    ), patch(
        "hpcperfstats.site.machine.api.gridplot", return_value=MagicMock()
    ), patch(
        "hpcperfstats.site.machine.api.components",
        return_value=("<script></script>", "<div></div>"),
    ):
        mock_job_data.objects.filter.return_value.order_by.return_value = mock_qs
        mock_metrics_data.objects.filter.return_value.values.return_value = []
        script, div = _job_list_histograms(request)

    assert isinstance(script, str)
    assert isinstance(div, str)
    assert script == "<script></script>"
    assert div == "<div></div>"


@pytest.mark.django_db
class TestJobListNoHistogramsInResponse:
    """Ensure job_list response no longer includes script/div."""

    def test_job_list_response_omits_script_and_div(self):
        """job_list returns JSON without 'script' or 'div' keys."""
        from hpcperfstats.site.machine.api import job_list

        factory = RequestFactory()
        request = factory.get("/api/jobs/")

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list(request)

        # With empty DB we may get 404 (no data) or 200 (empty list)
        if response.status_code == 200:
            data = response.json()
            assert "script" not in data, "job_list must not return script (use histograms endpoint)"
            assert "div" not in data, "job_list must not return div (use histograms endpoint)"
            assert "job_list" in data
            assert "pagination" in data
