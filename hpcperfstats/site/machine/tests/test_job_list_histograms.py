"""Unit tests for metric-only job list histograms endpoint."""
from unittest.mock import ANY, MagicMock, patch

import pandas as pd
from django.test import RequestFactory, override_settings

class TestJobListHistogramsView:
    """Tests for the metric-only job_list_histograms API view."""

    def test_returns_401_when_not_authenticated(self):
        """job_list_histograms returns 401 when check_for_tokens is False."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=False):
            response = job_list_histograms(request)

        assert response.status_code == 401

    def test_rejects_legacy_queue_group(self):
        """group=queue is removed and returns a 400 with metric-only guidance."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Unknown group 'queue'."
        assert data["allowed_groups"] == ["metric"]

    def test_returns_missing_group_allowed_groups_metric_only(self):
        """Missing group payload should advertise only 'metric' support."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/")

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Missing 'group' parameter."
        assert data["allowed_groups"] == ["metric"]

    def test_returns_metric_no_jobs_shape_when_authenticated(self):
        """Metric group returns null payload fields with a no-jobs reason."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get(
            "/api/jobs/histograms/",
            {"group": "metric", "metric": "runtime"},
        )

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
            "hpcperfstats.site.machine.api._build_histogram_queryset",
            return_value=(MagicMock(), 0, {}, {}),
        ):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert data["group"] == "metric"
        assert data["metric"] == "runtime"
        assert data["nj"] == 0
        assert data["plot_item_thumb"] is None
        assert data["plot_item_full"] is None
        assert data["plot_unavailable_reason"] == "No jobs matched this query."


def test_metric_group_null_plot_items_when_job_hist_returns_none():
    """Metric group sets thumb/full to null and a reason when job_hist yields no figures."""
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/",
        {"group": "metric", "metric": "runtime"},
    )
    mock_df = pd.DataFrame({"runtime": [1.0], "nhosts": [2.0]}, index=["1"])

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "job-list-hist-metric-test",
            }
        }
    ), patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 2, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours"), ("nhosts", "# nodes")], ["1"]),
    ), patch("hpcperfstats.site.machine.api.job_hist", return_value=None):
        response = job_list_histograms(request)

    assert response.status_code == 200
    data = response.json()
    assert data["group"] == "metric"
    assert data["metric"] == "runtime"
    assert data["plot_item_thumb"] is None
    assert data["plot_item_full"] is None
    assert data["plot_unavailable_reason"] == (
        "No histogram data available for metric 'runtime' in this query."
    )


def test_build_histogram_queryset_matches_job_list_annotate_all():
    """Histograms must use the same queryset annotations as job_list (visibility + performance)."""
    from hpcperfstats.site.machine import api as api_module
    from hpcperfstats.site.machine.api import _build_histogram_queryset

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/", {"group": "metric", "metric": "runtime"})

    with patch.object(
        api_module,
        "_build_job_list_queryset_from_request",
        return_value=(MagicMock(), {}, {}, "-end_time"),
    ) as mock_build:
        _build_histogram_queryset(request)

    _args, kwargs = mock_build.call_args
    assert kwargs.get("annotate_all") is True


def test_metric_group_calls_build_histogram_dataframe_when_jobs_present():
    """group=metric builds the dataframe from the queryset."""
    from hpcperfstats.site.machine import api as api_module
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/",
        {"group": "metric", "metric": "runtime"},
    )
    mock_df = pd.DataFrame({"runtime": [1.0]}, index=["1"])

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "job-list-hist-metric-delegate-test",
            }
        }
    ), patch.object(
        api_module, "_build_histogram_queryset", return_value=(MagicMock(), 1, {}, {})
    ) as mock_qs_build, patch.object(
        api_module,
        "_build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours")], ["1"]),
    ) as mock_df_build, patch(
        "hpcperfstats.site.machine.api.check_for_tokens", return_value=True
    ), patch(
        "hpcperfstats.site.machine.api._job_list_metric_hist_pair",
        return_value=(MagicMock(), MagicMock()),
    ), patch(
        "hpcperfstats.site.machine.api._sanitize_hist_plot_item",
        return_value={"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"},
    ):
        job_list_histograms(request)

    mock_qs_build.assert_called_once_with(ANY)
    mock_df_build.assert_called_once()


def test_metric_group_invalid_json_payload_is_replaced_with_unavailable_reason():
    """Malformed metric json_item payloads are nulled out instead of returned."""
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/",
        {"group": "metric", "metric": "runtime"},
    )
    mock_df = pd.DataFrame({"runtime": [1.0]}, index=["1"])

    with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 2, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours")], ["1"]),
    ), patch(
        "hpcperfstats.site.machine.api._job_list_metric_hist_pair",
        return_value=(MagicMock(), MagicMock()),
    ), patch(
        "hpcperfstats.site.machine.api._sanitize_hist_plot_item",
        side_effect=[None, None],
    ):
        response = job_list_histograms(request)

    assert response.status_code == 200
    data = response.json()
    assert data["group"] == "metric"
    assert data["plot_item_thumb"] is None
    assert data["plot_item_full"] is None
    assert data["plot_unavailable_reason"] == (
        "No histogram data available for metric 'runtime' in this query."
    )


class TestJobListNoHistogramsInResponse:
    """Ensure job_list response no longer includes script/div."""

    def test_job_list_response_omits_script_and_div(self):
        """job_list returns JSON without 'script' or 'div' keys."""
        from hpcperfstats.site.machine.api import job_list

        factory = RequestFactory()
        request = factory.get("/api/jobs/")
        job_qs = MagicMock()
        job_qs.count.return_value = 0

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
            "hpcperfstats.site.machine.api._build_job_list_queryset_from_request",
            return_value=(job_qs, {}, {}, "-end_time"),
        ):
            response = job_list(request)

        # With empty DB we may get 404 (no data) or 200 (empty list)
        if response.status_code == 200:
            data = getattr(response, "data", None)
            if data is None:
                data = response.json()
            assert "script" not in data, "job_list must not return script (use histograms endpoint)"
            assert "div" not in data, "job_list must not return div (use histograms endpoint)"
            assert "job_list" in data
            assert "pagination" in data
