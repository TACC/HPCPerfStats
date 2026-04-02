"""Unit tests for job list histograms endpoint and _job_list_histograms helper.

Tests the split histogram API (job_list_histograms) and that job_list no longer
returns script/div. Run with pytest; requires Django (site.machine.tests).
"""
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.test import RequestFactory, override_settings


@pytest.mark.django_db
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
        from hpcperfstats.site.machine.api import job_list_histograms, _build_histogram_queryset

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
        "hpcperfstats.site.machine.api.json_item",
        return_value={"stub": True},
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


def test_job_list_histograms_helper_returns_empty_figure_when_no_jobs():
    """_job_list_histograms returns valid script/div/plot_item (empty-state figure) when queryset count is 0."""
    from hpcperfstats.site.machine.api import _job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/")

    with patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 0, {}, {}),
    ):
        script, div, plot_item, histograms = _job_list_histograms(request)

    assert script != ""
    assert div != ""
    assert plot_item is not None
    assert histograms == []


def test_job_list_histograms_helper_returns_tuple_when_mocked():
    """_job_list_histograms returns (script, div, plot_item) when data and components are mocked."""
    from hpcperfstats.site.machine.api import _job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/")

    mock_qs = MagicMock()
    mock_df = pd.DataFrame(
        {"runtime": [1.0], "nhosts": [1.0], "queue_wait": [0.0]},
        index=["12345"],
    )
    mock_gp = MagicMock()

    with patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(mock_qs, 1, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours")], ["12345"]),
    ), patch(
        "hpcperfstats.site.machine.api.job_hist", return_value=MagicMock()
    ), patch(
        "hpcperfstats.site.machine.api.gridplot", return_value=mock_gp
    ), patch(
        "hpcperfstats.site.machine.api.components",
        return_value=("<script></script>", "<div></div>"),
    ), patch(
        "hpcperfstats.site.machine.api.json_item", return_value={"doc": {}, "root_id": "x"}
    ):
        script, div, plot_item, histograms = _job_list_histograms(request)

    assert isinstance(script, str)
    assert isinstance(div, str)
    assert script == "<script></script>"
    assert div == "<div></div>"
    assert plot_item is not None
    assert isinstance(histograms, list)


def test_job_list_histograms_uses_build_histogram_helpers():
    """_job_list_histograms delegates queryset and dataframe construction to shared helpers."""
    from hpcperfstats.site.machine import api as api_module
    from hpcperfstats.site.machine.api import _job_list_histograms

    factory = RequestFactory()
    request = factory.get("/api/jobs/histograms/")

    with patch.object(
        api_module, "_build_histogram_queryset", return_value=(MagicMock(), 0, {}, {})
    ) as mock_qs_build, patch.object(api_module, "_build_histogram_dataframe") as mock_df_build:
        _job_list_histograms(request)

    mock_qs_build.assert_called_once_with(request)
    mock_df_build.assert_not_called()


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
