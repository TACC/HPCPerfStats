"""Unit tests for metric-only job list histograms endpoint."""
from unittest.mock import ANY, MagicMock, patch

import pandas as pd
import pytest
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


def test_job_list_histograms_batch_returns_multiple_metrics():
    from hpcperfstats.site.machine.api import job_list_histograms_batch

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"metrics": "runtime,nhosts"},
    )
    request.session = {"username": "admin", "is_staff": True}

    mock_df = MagicMock()
    with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 2, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours"), ("nhosts", "# nodes")], ["j1", "j2"]),
    ), patch(
        "hpcperfstats.site.machine.api._build_metric_histogram_payload",
        side_effect=[
            {"metric": "runtime", "nj": 2},
            {"metric": "nhosts", "nj": 2},
        ],
    ):
        response = job_list_histograms_batch(request)

    assert response.status_code == 200
    data = response.json()
    assert data["nj"] == 2
    assert len(data["histograms"]) == 2


@pytest.mark.machine_unit_mock
def test_batch_nj_zero_returns_stub_per_requested_metric():
    from hpcperfstats.site.machine.api import (
        JOB_LIST_HISTOGRAM_NO_JOBS_REASON,
        job_list_histograms_batch,
    )

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"metrics": "runtime,nhosts,queue_wait"},
    )
    request.session = {"username": "admin", "is_staff": True}

    with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 0, {}, {}),
    ):
        response = job_list_histograms_batch(request)

    assert response.status_code == 200
    data = response.json()
    assert data["nj"] == 0
    assert len(data["histograms"]) == 3
    metrics = {row["metric"] for row in data["histograms"]}
    assert metrics == {"runtime", "nhosts", "queue_wait"}
    for row in data["histograms"]:
        assert row["plot_item_thumb"] is None
        assert row["plot_item_full"] is None
        assert row["plot_unavailable_reason"] == JOB_LIST_HISTOGRAM_NO_JOBS_REASON


@pytest.mark.machine_unit_mock
def test_batch_never_omits_requested_metric_when_payload_builder_returns_none():
    from hpcperfstats.site.machine.api import job_list_histograms_batch

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"metrics": "runtime,unknown_metric"},
    )
    request.session = {"username": "admin", "is_staff": True}

    mock_df = MagicMock()
    with patch("hpcperfstats.site.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), 2, {}, {}),
    ), patch(
        "hpcperfstats.site.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours")], ["j1", "j2"]),
    ), patch(
        "hpcperfstats.site.machine.api._build_metric_histogram_payload",
        side_effect=[
            {"metric": "runtime", "nj": 2, "plot_item_thumb": {"root_id": "1"}},
            None,
        ],
    ):
        response = job_list_histograms_batch(request)

    assert response.status_code == 200
    data = response.json()
    assert len(data["histograms"]) == 2
    assert data["histograms"][0]["metric"] == "runtime"
    assert data["histograms"][1]["metric"] == "unknown_metric"
    assert "not available" in data["histograms"][1]["plot_unavailable_reason"]


@pytest.mark.machine_unit_mock
def test_invalidate_job_browse_path_also_targets_job_list_api_cache():
    """Staff purge of job browse SPA routes must drop /api/jobs/ histogram cache rows."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "/machine/date/2026-06"},
        content_type="application/json",
    )
    request.session = {"username": "alice", "is_staff": True}
    request.META["HTTP_HOST"] = "testserver"

    mock_client = MagicMock()
    mock_client.scan_iter.return_value = iter([])

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api._require_csrf_for_session_post",
        return_value=None,
    ), patch(
        "hpcperfstats.site.machine.api._get_redis_cache_client",
        return_value=mock_client,
    ), patch(
        "hpcperfstats.site.machine.api._delete_django_cache_page_entries_for_request",
    ) as mock_delete_django, patch(
        "hpcperfstats.site.machine.api._full_page_cache_url_digests_for_request_paths",
        return_value=set(),
    ), patch(
        "hpcperfstats.site.machine.api.invalidate_home_options_query_cache",
    ):
        response = api.invalidate_cache_for_page(request)

    assert response.status_code == 200
    deleted_paths = mock_delete_django.call_args[0][1]
    assert "/api/jobs/" in deleted_paths
    assert "/api/jobs/histograms/batch/" in deleted_paths


@pytest.mark.machine_unit_mock
def test_histogram_queryset_nj_matches_job_list_with_batch_metrics_param():
    """Batch histogram ``metrics=`` param must not force nj=0 when jobs match the filter."""
    from hpcperfstats.site.machine import api
    from hpcperfstats.site.machine.api import (
        JOB_LIST_HISTOGRAM_NO_JOBS_REASON,
        job_list,
        job_list_histograms_batch,
    )

    factory = RequestFactory()
    params = {
        "end_time__date": "2026-06",
        "username": "parity-user",
        "metrics": "runtime,nhosts,queue_wait",
        "_histogram_embed_v": "9",
    }
    list_request = factory.get("/api/jobs/", params)
    list_request.session = {"username": "admin", "is_staff": True}
    batch_request = factory.get("/api/jobs/histograms/batch/", params)
    batch_request.session = {"username": "admin", "is_staff": True}

    chain = MagicMock()
    chain.count.return_value = 2
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.aggregate.return_value = {"total_node_hours": 0.0}
    page = MagicMock()
    page.number = 1
    page.has_previous.return_value = False
    page.has_next.return_value = False
    page.object_list = []
    paginator = MagicMock()
    paginator.num_pages = 1
    paginator.page.return_value = page

    with patch.object(api.job_data.objects, "filter", return_value=chain), patch.object(
        api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
    ), patch.object(
        api, "normalize_job_list_query_params", side_effect=lambda f: f
    ), patch.object(
        api, "expand_month_date_to_range", side_effect=lambda f: f
    ), patch.object(
        api, "get_job_list_order_by", return_value="-end_time"
    ), patch.object(
        api, "partition_job_list_acct_filters", return_value=({"username": "parity-user"}, None)
    ), patch.object(
        api, "annotate_job_list_performance_fields", return_value=chain
    ), patch.object(
        api, "build_job_list_qname_and_filter_summary", return_value=("", [])
    ), patch.object(
        api, "aggregate_queue_wait_seconds_stats", return_value={"mean_wait_s": None}
    ), patch.object(api, "Paginator", return_value=paginator), patch.object(
        api, "JobListSerializer", return_value=MagicMock(data=[])
    ), patch(
        "hpcperfstats.site.machine.api.check_for_tokens", return_value=True
    ), patch.object(
        api,
        "_build_metric_histogram_payload",
        side_effect=lambda _df, _hm, metric, nj: {
            "metric": metric,
            "nj": nj,
            "plot_item_thumb": {"root_id": "1"},
            "plot_item_full": {"root_id": "2"},
        },
    ), patch.object(
        api,
        "_build_histogram_dataframe",
        return_value=(MagicMock(), [("runtime", "hours")], ["j1", "j2"]),
    ):
        list_response = job_list(list_request)
        batch_response = job_list_histograms_batch(batch_request)

    assert list_response.status_code == 200
    assert batch_response.status_code == 200
    list_data = list_response.data
    batch_data = batch_response.data
    assert list_data["nj"] == 2
    assert batch_data["nj"] == list_data["nj"]
    assert len(batch_data["histograms"]) == 3
    for row in batch_data["histograms"]:
        reason = row.get("plot_unavailable_reason")
        assert reason != JOB_LIST_HISTOGRAM_NO_JOBS_REASON
        assert row.get("plot_item_thumb") is not None or reason is None


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
