"""Unit tests for job list histograms endpoint (split `?group=` API).

Tests `job_list_histograms` and that `job_list` no longer returns script/div.
Run with pytest; requires Django (site.machine.tests).
"""
from concurrent.futures import Future
from unittest.mock import ANY, MagicMock, patch

import pandas as pd
import pytest
from django.test import RequestFactory, override_settings

# Histogram view tests mock the ORM; job_list shape test below needs the default DB.
pytestmark = pytest.mark.django_db(databases=[])


class TestJobListHistogramsView:
    """Tests for the job_list_histograms API view."""

    def test_returns_401_when_not_authenticated(self):
        """job_list_histograms returns 401 when check_for_tokens is False."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=False):
            response = job_list_histograms(request)

        assert response.status_code == 401

    def test_returns_200_with_queue_group_payload_when_authenticated(self):
        """job_list_histograms returns 200 and JSON with queue plots when authenticated."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert data["group"] == "queue"
        assert "nj" in data
        assert "plots" in data
        assert isinstance(data["plots"], list)

    def test_returns_200_with_queue_group_when_db_unavailable(self):
        """job_list_histograms returns 200 and queue payload even when DB layer raises."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
            "hpcperfstats.site.machine.api._build_histogram_queryset",
            return_value=(MagicMock(), 0, {}, {}),
        ):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert data["group"] == "queue"
        assert data["nj"] == 0
        assert isinstance(data["plots"], list)

    def test_histograms_endpoint_uses_same_query_params_as_job_list(self):
        """job_list_histograms accepts the same GET params as job list (e.g. page ignored for histograms)."""
        from hpcperfstats.site.machine.api import job_list_histograms

        factory = RequestFactory()
        request = factory.get("/api/jobs/histograms/", {"page": "2", "group": "queue"})

        with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True):
            response = job_list_histograms(request)

        assert response.status_code == 200
        data = response.json()
        assert data["group"] == "queue"


class _SyncExecutor:
    """Runs submitted callables immediately so tests avoid a real thread pool."""

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        fut.set_result(fn(*args, **kwargs))
        return fut


def test_queue_group_plots_payload_shape_and_order():
    """Queue group returns two plots with stable keys, titles, and null reasons when charts exist."""
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/", {"group": "queue"})

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "job-list-hist-queue-test",
            }
        }
    ), patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 4, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._get_small_executor",
        return_value=_SyncExecutor(),
    ), patch(
        "hpcperfstats.site.machine.api._job_list_queue_bar_chart",
        return_value=MagicMock(),
    ) as mock_bar, patch(
        "hpcperfstats.site.machine.api._sanitize_hist_plot_item",
        return_value={"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"},
    ):
        response = job_list_histograms(request)

    assert response.status_code == 200
    data = response.json()
    assert data["group"] == "queue"
    assert data["nj"] == 4
    keys = [p["key"] for p in data["plots"]]
    assert keys == ["jobs_by_queue", "cpu_hours_by_queue"]
    assert data["plots"][0]["title"] == "Jobs by queue"
    assert data["plots"][1]["title"] == "Node hours by queue"
    assert data["plots"][0]["plot_unavailable_reason"] is None
    assert data["plots"][1]["plot_unavailable_reason"] is None
    assert mock_bar.call_count == 4


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
    request = factory.get("/api/jobs/histograms/", {"group": "queue"})

    with patch.object(
        api_module,
        "_build_job_list_queryset_from_request",
        return_value=(MagicMock(), {}, {}, "-end_time"),
    ) as mock_build:
        _build_histogram_queryset(request)

    _args, kwargs = mock_build.call_args
    assert kwargs.get("annotate_all") is True


def test_queue_group_with_jobs_does_not_call_build_histogram_dataframe():
    """group=queue only needs the job queryset, not the histogram dataframe."""
    from hpcperfstats.site.machine import api as api_module
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/", {"group": "queue"})

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "job-list-hist-queue-delegate-test",
            }
        }
    ), patch.object(
        api_module, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
    ) as mock_qs_build, patch.object(
        api_module, "_build_histogram_dataframe"
    ) as mock_df_build, patch(
        "hpcperfstats.site.machine.api.check_for_tokens", return_value=True
    ), patch(
        "hpcperfstats.site.machine.api._get_small_executor",
        return_value=_SyncExecutor(),
    ), patch(
        "hpcperfstats.site.machine.api._job_list_queue_bar_chart",
        return_value=MagicMock(),
    ), patch(
        "hpcperfstats.site.machine.api._sanitize_hist_plot_item",
        return_value={"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"},
    ):
        job_list_histograms(request)

    mock_qs_build.assert_called_once_with(ANY)
    mock_df_build.assert_not_called()


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


def test_queue_group_invalid_json_payload_is_replaced_with_unavailable_reason():
    """Malformed Bokeh json_item payloads are nulled out instead of returned to SPA."""
    from hpcperfstats.site.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/", {"group": "queue"})

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "job-list-hist-invalid-json-test",
            }
        }
    ), patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 2, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._get_small_executor",
        return_value=_SyncExecutor(),
    ), patch(
        "hpcperfstats.site.machine.api._job_list_queue_bar_chart",
        return_value=MagicMock(),
    ), patch(
        "hpcperfstats.site.machine.api._sanitize_hist_plot_item",
        side_effect=[None, None, {"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"}, {"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"}],
    ):
        response = job_list_histograms(request)

    assert response.status_code == 200
    data = response.json()
    first_plot = data["plots"][0]
    assert first_plot["plot_item_thumb"] is None
    assert first_plot["plot_item_full"] is None
    assert first_plot["plot_unavailable_reason"] == "No queue histogram data available for this query."


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
