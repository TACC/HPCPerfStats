"""Direct unit tests for helpers in hpcperfstats.site.lib.machine.api.

Uses ``django_db(databases=[])`` and LocMem cache (same contract as
``test_api_coverage_gaps.py``) so tests run on the host without compose ``db``.
"""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings
from rest_framework import status

pytestmark = pytest.mark.django_db(databases=[])

_API_COVERAGE_GAP_SETTINGS = {
    "ALLOWED_HOSTS": ["testserver", "example.com", "localhost", "127.0.0.1"],
    "CACHES": {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "api-helpers-tests",
        }
    },
    "CACHE_MIDDLEWARE_KEY_PREFIX": "test-prefix",
}


@pytest.fixture(autouse=True)
def _api_helpers_settings():
    with override_settings(**_API_COVERAGE_GAP_SETTINGS):
        yield


def _make_metrics_job(metric_values):
    """Build a mock job whose metrics_data_set.all() returns metric rows."""
    rows = []
    for metric, value in metric_values.items():
        row = MagicMock()
        row.metric = metric
        row.value = value
        rows.append(row)
    job = MagicMock()
    job.metrics_data_set.all.return_value = rows
    return job


class TestExtractBokehDocRootIds:
    def test_returns_empty_for_non_dict(self):
        from hpcperfstats.site.lib.machine import api

        assert api._extract_bokeh_doc_root_ids(None) == set()
        assert api._extract_bokeh_doc_root_ids([]) == set()

    def test_list_roots_string_and_dict_ids(self):
        from hpcperfstats.site.lib.machine import api

        doc = {
            "roots": [
                "root-a",
                {"id": "root-b"},
                {"id": 42},
                "",
                99,
            ]
        }
        assert api._extract_bokeh_doc_root_ids(doc) == {"root-a", "root-b", "42"}

    def test_dict_roots_root_ids_and_references(self):
        from hpcperfstats.site.lib.machine import api

        doc = {
            "roots": {
                "root_ids": ["r1", 7],
                "references": [{"id": "r2"}, {"id": 8}, {"other": "x"}],
            }
        }
        assert api._extract_bokeh_doc_root_ids(doc) == {"r1", "7", "r2", "8"}


class TestIsValidBokehJsonItemPayload:
    def _valid_payload(self):
        return {
            "doc": {"roots": {"root_ids": ["plot-1"]}},
            "root_id": "plot-1",
        }

    def test_rejects_non_dict_and_missing_doc(self):
        from hpcperfstats.site.lib.machine import api

        assert api._is_valid_bokeh_json_item_payload(None) is False
        assert api._is_valid_bokeh_json_item_payload({"doc": "bad"}) is False
        assert api._is_valid_bokeh_json_item_payload({"doc": {}}) is False

    def test_accepts_root_id_string_or_int(self):
        from hpcperfstats.site.lib.machine import api

        payload = self._valid_payload()
        assert api._is_valid_bokeh_json_item_payload(payload) is True
        payload_int = {
            "doc": {"roots": {"root_ids": ["99"]}},
            "root_id": 99,
        }
        assert api._is_valid_bokeh_json_item_payload(payload_int) is True

    def test_accepts_root_ids_list_and_rejects_mismatch(self):
        from hpcperfstats.site.lib.machine import api

        ok = {
            "doc": {"roots": {"root_ids": ["a", "b"]}},
            "root_ids": ["a", "b"],
        }
        assert api._is_valid_bokeh_json_item_payload(ok) is True
        bad = {
            "doc": {"roots": {"root_ids": ["a"]}},
            "root_ids": ["a", "missing"],
        }
        assert api._is_valid_bokeh_json_item_payload(bad) is False


class TestSanitizeHistPlotItem:
    def test_returns_none_for_none_plot(self):
        from hpcperfstats.site.lib.machine import api

        assert api._sanitize_hist_plot_item(None) is None

    def test_returns_none_when_json_item_invalid(self):
        from hpcperfstats.site.lib.machine import api

        plot = MagicMock()
        with patch.object(api, "json_item", return_value={"doc": {}}):
            assert api._sanitize_hist_plot_item(plot) is None

    def test_returns_payload_when_valid(self):
        from hpcperfstats.site.lib.machine import api

        plot = MagicMock()
        payload = {
            "doc": {"roots": {"root_ids": ["p1"]}},
            "root_id": "p1",
        }
        with patch.object(api, "json_item", return_value=payload):
            assert api._sanitize_hist_plot_item(plot) == payload


class TestQueueHistogramDisplayLabel:
    def test_strips_and_defaults_empty_to_no_queue(self):
        from hpcperfstats.site.lib.machine import api

        assert api._queue_histogram_display_label("  batch  ") == "batch"
        assert api._queue_histogram_display_label(None) == "(no queue)"
        assert api._queue_histogram_display_label("   ") == "(no queue)"


class TestMergeQueueBarRows:
    def test_jobs_metric_sums_duplicate_labels(self):
        from hpcperfstats.site.lib.machine import api

        rows = [("", 2), (None, 3), ("normal", 5)]
        merged = api._merge_queue_bar_rows(rows, metric="jobs")
        assert merged == [("(no queue)", 5), ("normal", 5)]

    def test_node_hours_metric_float_accumulation(self):
        from hpcperfstats.site.lib.machine import api

        rows = [("q1", 1.5), ("q1", 2.5), ("", 0.0)]
        merged = api._merge_queue_bar_rows(rows, metric="node_hours")
        assert merged[0] == ("q1", 4.0)

    def test_unknown_metric_raises(self):
        from hpcperfstats.site.lib.machine import api

        with pytest.raises(ValueError, match="unknown queue bar metric"):
            api._merge_queue_bar_rows([], metric="bad")


class TestJobTimesAsLocal:
    def test_naive_datetimes_assumed_utc_then_localized(self):
        from hpcperfstats.site.lib.machine import api

        start = datetime(2024, 6, 1, 12, 0, 0)
        end = datetime(2024, 6, 1, 13, 0, 0)
        loc_start, loc_end = api._job_times_as_local(start, end)
        assert loc_start.tzinfo is not None
        assert loc_end.tzinfo is not None

    def test_aware_datetimes_converted(self):
        from hpcperfstats.site.lib.machine import api

        start = datetime(2024, 6, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        end = datetime(2024, 6, 1, 13, 0, 0, tzinfo=dt_timezone.utc)
        loc_start, loc_end = api._job_times_as_local(start, end)
        assert loc_start.hour == loc_end.hour - 1 or loc_start != loc_end


class TestRequireAuthAndStaff:
    def test_require_auth_session_ok(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/")
        request.session = {}
        with patch.object(api, "check_for_tokens", return_value=True):
            assert api._require_auth(request) is None

    def test_require_auth_api_key_sets_session(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/")
        request.session = {}
        request.META["HTTP_AUTHORIZATION"] = "Api-Key secret"
        key_obj = MagicMock(username="alice", is_staff=True)
        with patch.object(api, "check_for_tokens", return_value=False), patch.object(
            api, "_api_key_valid", return_value=key_obj
        ):
            assert api._require_auth(request) is None
        assert request.session["username"] == "alice"
        assert request.session["is_staff"] is True

    def test_require_auth_returns_401_when_unauthenticated(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/")
        request.session = {}
        with patch.object(api, "check_for_tokens", return_value=False), patch.object(
            api, "_api_key_valid", return_value=None
        ):
            resp = api._require_auth(request)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_require_staff_delegates_auth_then_checks_flag(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/")
        request.session = {"username": "u", "is_staff": False}
        denied = api.Response({"detail": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            assert api._require_staff(request) is denied

        with patch.object(api, "_require_auth", return_value=None):
            resp = api._require_staff(request)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

        request.session["is_staff"] = True
        with patch.object(api, "_require_auth", return_value=None):
            assert api._require_staff(request) is None


class TestApplyNonStaffJobVisibility:
    def test_staff_returns_queryset_unchanged(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        request = MagicMock()
        request.session = {"is_staff": True, "username": "alice"}
        assert api._apply_non_staff_job_visibility(qs, request) is qs

    def test_no_username_returns_none_queryset(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        none_qs = MagicMock()
        qs.none.return_value = none_qs
        request = MagicMock()
        request.session = {"is_staff": False}
        assert api._apply_non_staff_job_visibility(qs, request) is none_qs

    def test_with_accounts_filters_username_or_account(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        request = MagicMock()
        request.session = {"is_staff": False, "username": "alice"}
        with patch.object(
            api, "cached_non_staff_visible_accounts", return_value=["proj"]
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60):
            out = api._apply_non_staff_job_visibility(qs, request)
        assert out is filtered
        qs.filter.assert_called_once()


class TestGetVisibleJobOrErrorResponse:
    def test_404_when_job_missing(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": True}
        with patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "cached_orm", return_value=None
        ):
            job, err = api._get_visible_job_or_error_response(
                request, "j1", lambda: None
            )
        assert job is None
        assert err.status_code == status.HTTP_404_NOT_FOUND

    def test_403_when_not_visible(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": False}
        job_obj = MagicMock(jid="j1")
        vis_qs = MagicMock()
        vis_qs.exists.return_value = False
        with patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "cached_orm", return_value=job_obj
        ), patch.object(
            api, "ensure_job_metrics_data_prefetched"
        ), patch.object(
            api, "_apply_non_staff_job_visibility", return_value=vis_qs
        ), patch.object(api.job_data.objects, "filter", return_value=vis_qs):
            job, err = api._get_visible_job_or_error_response(
                request, "j1", lambda: job_obj
            )
        assert job is None
        assert err.status_code == status.HTTP_403_FORBIDDEN

    def test_success_returns_job(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": True}
        job_obj = MagicMock(jid="j1")
        vis_qs = MagicMock()
        vis_qs.exists.return_value = True
        with patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "cached_orm", return_value=job_obj
        ), patch.object(
            api, "ensure_job_metrics_data_prefetched"
        ), patch.object(
            api, "_apply_non_staff_job_visibility", return_value=vis_qs
        ), patch.object(api.job_data.objects, "filter", return_value=vis_qs):
            job, err = api._get_visible_job_or_error_response(
                request, "j1", lambda: job_obj
            )
        assert err is None
        assert job is job_obj


class TestFsioAndGpuDetailFromMetrics:
    def test_fsio_llite_dict_when_read_write_present(self):
        from hpcperfstats.site.lib.machine import api

        job = _make_metrics_job({
            "detail_fsio_llite_read_mb": 10.0,
            "detail_fsio_llite_write_mb": 20.0,
            "detail_fsio_llite_peak_mb_s": 1.5,
            "detail_fsio_llite_peak_iops": 100.0,
        })
        out = api._fsio_dict_from_metrics(job)
        assert out == {"llite": [10.0, 20.0, 1.5, 100.0]}

    def test_fsio_nfs_fallback_when_llite_incomplete(self):
        from hpcperfstats.site.lib.machine import api

        job = _make_metrics_job({
            "detail_fsio_nfs_read_mb": 5.0,
            "detail_fsio_nfs_write_mb": 6.0,
        })
        out = api._fsio_dict_from_metrics(job)
        assert "nfs" in out
        assert out["nfs"][0] == 5.0

    def test_fsio_returns_none_when_incomplete(self):
        from hpcperfstats.site.lib.machine import api

        assert api._fsio_dict_from_metrics(_make_metrics_job({})) is None

    def test_gpu_detail_tuple_all_four_metrics(self):
        from hpcperfstats.site.lib.machine import api

        job = _make_metrics_job({
            "detail_gpu_active": 2.4,
            "detail_gpu_util_max": 95.5,
            "detail_gpu_util_mean": 50.1,
            "detail_gpu_count": 4.0,
        })
        assert api._gpu_detail_tuple_from_metrics(job) == (2, 95.5, 50.1, 4)

    def test_gpu_detail_tuple_none_when_missing_row(self):
        from hpcperfstats.site.lib.machine import api

        job = _make_metrics_job({"detail_gpu_active": 1.0})
        assert api._gpu_detail_tuple_from_metrics(job) is None


class TestPageCacheHelpers:
    def test_full_page_cache_url_digests_for_both_secure_variants(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/")
        request.META["HTTP_HOST"] = "example.com"
        digests = api._full_page_cache_url_digests_for_request_paths(
            request, ["/machine", "api/home"]
        )
        assert len(digests) == 4
        assert all(len(d) == 32 for d in digests)

    def test_delete_django_cache_page_entries_returns_count(self):
        from hpcperfstats.site.lib.machine import api
        from django.core.cache import cache

        cache.clear()
        request = RequestFactory().get("/")
        request.META["HTTP_HOST"] = "testserver"
        deleted = api._delete_django_cache_page_entries_for_request(
            request, ["/machine/"]
        )
        assert deleted >= 0

    def test_redis_delete_cache_page_keys_matching_digests(self):
        from hpcperfstats.site.lib.machine import api

        class _FakeRedis:
            def __init__(self):
                self.deleted = []

            def scan_iter(self, match=None, count=500):
                yield b"views.decorators.cache.cache_page.GET.deadbeef.abc"
                yield b"other:key"

            def delete(self, raw_key):
                self.deleted.append(raw_key)
                return 1

        client = _FakeRedis()
        n = api._redis_delete_cache_page_keys_matching_digests(
            client, {"deadbeef"}
        )
        assert n == 1
        assert len(client.deleted) == 1

    def test_redis_delete_returns_zero_without_client_or_digests(self):
        from hpcperfstats.site.lib.machine import api

        assert api._redis_delete_cache_page_keys_matching_digests(None, {"x"}) == 0
        assert api._redis_delete_cache_page_keys_matching_digests(MagicMock(), set()) == 0

    def test_get_redis_cache_client_unwraps_backend(self):
        from hpcperfstats.site.lib.machine import api

        inner = MagicMock()
        backend = MagicMock()
        backend.get_client.return_value = inner
        with patch.object(api, "cache") as mock_cache:
            mock_cache._cache = backend
            assert api._get_redis_cache_client() is inner


class TestJSONResponseAndSiteCacheTimeout:
    def test_json_response_json_method(self):
        from hpcperfstats.site.lib.machine.api import _JSONResponse

        resp = _JSONResponse({"ok": True})
        assert resp.json() == {"ok": True}

    def test_site_response_cache_timeout_delegates(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/")
        with patch.object(api, "get_site_content_cache_timeout", return_value=123):
            assert api.site_response_cache_timeout(request) == 123


class TestCollectFutureResultsWithDeadline:
    def test_collects_completed_futures_within_deadline(self):
        from concurrent.futures import Future
        from hpcperfstats.site.lib.machine import api

        fut = Future()
        fut.set_result("ok")
        mapping = {fut: "key1"}
        results, remaining = api._collect_future_results_with_deadline(mapping, 1.0)
        assert results == {"key1": "ok"}
        assert remaining == set()

    def test_ignores_failed_future_result(self):
        from concurrent.futures import Future
        from hpcperfstats.site.lib.machine import api

        fut = Future()
        fut.set_exception(RuntimeError("boom"))
        mapping = {fut: "key1"}
        results, remaining = api._collect_future_results_with_deadline(mapping, 1.0)
        assert results == {}
        assert remaining == set()


class TestAdminHostStatsHelpers:
    def test_get_admin_host_stats_statement_timeout_ms_minimum(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(
            api.cfg, "get_db_statement_timeout_ms", return_value=1000
        ):
            assert api._get_admin_host_stats_statement_timeout_ms() >= 600000

    def test_get_admin_host_stats_statement_timeout_ms_handles_config_error(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(
            api.cfg, "get_db_statement_timeout_ms", side_effect=RuntimeError("bad")
        ):
            assert api._get_admin_host_stats_statement_timeout_ms() == 600000

    def test_pg_session_statement_timeout_skips_non_postgresql(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.connection, "vendor", "sqlite"):
            with api._pg_session_statement_timeout_for_admin_host_stats_query():
                pass

    def test_pg_session_statement_timeout_sets_local_on_postgresql(self):
        from hpcperfstats.site.lib.machine import api
        import contextlib

        cursor = MagicMock()
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = None
        with patch.object(api.connection, "vendor", "postgresql"), patch.object(
            api.transaction, "atomic", return_value=contextlib.nullcontext()
        ), patch.object(api.connection, "cursor", return_value=cursor_cm), patch.object(
            api, "_get_admin_host_stats_statement_timeout_ms", return_value=999
        ):
            with api._pg_session_statement_timeout_for_admin_host_stats_query():
                pass
        cursor.execute.assert_called_once_with(
            "SET LOCAL statement_timeout = %s", [999]
        )


class TestBuildJobListQuerysetFromRequest:
    def test_builds_queryset_with_metric_filters(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/",
            {
                "username": "alice",
                "metrics_runtime__gte": "1",
                "page": "2",
                "order_by": "-end_time",
            },
        )
        request.session = {"username": "alice", "is_staff": True}
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
            api, "partition_job_list_acct_filters", return_value=({"username": "alice"}, None)
        ), patch.object(
            api, "annotate_job_list_performance_fields", return_value=chain
        ):
            qs, fields, cur_metrics, order_by = api._build_job_list_queryset_from_request(
                request, annotate_all=True
            )
        assert order_by == "-end_time"
        assert "runtime__gte" in cur_metrics
        assert qs is chain


class TestHistogramBuilders:
    def test_build_histogram_queryset_on_db_error_returns_empty(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/")
        with patch.object(
            api,
            "_build_job_list_queryset_from_request",
            side_effect=RuntimeError("db down"),
        ):
            qs, nj, fields, cur_metrics = api._build_histogram_queryset(request)
        assert nj == 0
        assert fields == {}

    def test_build_histogram_dataframe_joins_metrics(self):
        from hpcperfstats.site.lib.machine import api

        job_list_qs = MagicMock()
        job_list_qs.values.return_value = [
            {
                "jid": "j1",
                "start_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "submit_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "runtime": 3600,
                "nhosts": 2,
            }
        ]
        metrics_qs = MagicMock()
        metrics_qs.filter.return_value.values.return_value = [
            {"jid_id": "j1", "metric": "cpu_hours", "units": "h", "value": 1.0}
        ]
        with patch.object(api.metrics_data.objects, "filter", return_value=metrics_qs):
            df, hist_metrics, jids = api._build_histogram_dataframe(
                job_list_qs, {"cpu_hours__gte": "0"}
            )
        assert jids == ["j1"]
        assert "cpu_hours" in df.columns
        assert "runtime" in df.columns
        assert len(hist_metrics) >= 3


class TestJobListQueueBarChart:
    def test_returns_none_for_empty_rows(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        qs.values.return_value.annotate.return_value.order_by.return_value.values_list.return_value = []
        assert api._job_list_queue_bar_chart(qs) is None

    def test_jobs_metric_builds_figure(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        qs.values.return_value.annotate.return_value.order_by.return_value.values_list.return_value = [
            ("normal", 3),
            ("", 1),
        ]
        fig = api._job_list_queue_bar_chart(qs, metric="jobs")
        assert fig is not None
        assert fig.title.text == "Jobs by queue"


class TestComputeJobGpuStats:
    def test_returns_stats_from_cached_orm(self):
        from hpcperfstats.site.lib.machine import api

        job = MagicMock(jid="j1")
        j = MagicMock()
        with patch.object(api, "cached_orm", side_effect=[["row"], 4]), patch.object(
            api,
            "reduce_gpu_agg_to_util_stats",
            return_value=(1, 90.0, 50.0),
            create=True,
        ), patch(
            "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.reduce_gpu_agg_to_util_stats",
            return_value=(1, 90.0, 50.0),
        ), patch(
            "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.gpu_count_total_for_job_window",
            return_value=2,
        ):
            out = api._compute_job_gpu_stats(job, j, 60, include_gpu_count=True)
        assert out == (1, 90.0, 50.0, 4)


class TestInflightPlotTasks:
    def test_evict_stale_inflight_plot_tasks_removes_old_entries(self):
        from hpcperfstats.site.lib.machine import api

        future = MagicMock()
        future.done.return_value = False
        api._job_plot_inflight.clear()
        api._job_plot_inflight["k1"] = {"future": future, "created_at": 0.0}
        with patch.object(api.time, "monotonic", return_value=999999.0):
            api._evict_stale_inflight_plot_tasks()
        assert "k1" not in api._job_plot_inflight

    def test_get_small_executor_is_singleton(self):
        from hpcperfstats.site.lib.machine import api

        api._small_executor = None
        with patch.object(api.cfg, "get_api_small_executor_max_workers", return_value=2):
            ex1 = api._get_small_executor()
            ex2 = api._get_small_executor()
        assert ex1 is ex2
        api._small_executor = None


class TestTimescaledbStats:
    def test_returns_cached_stats_when_present(self):
        from hpcperfstats.site.lib.machine import api

        cached = {"database_name": "portal"}
        with patch.object(api.cache, "get", return_value=cached):
            assert api._get_timescaledb_stats() == cached

    def test_queries_cursor_and_caches_result(self):
        from hpcperfstats.site.lib.machine import api

        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            ("portal", "pg16"),
            ("2.14",),
            (3,),
            (10, 4),
            (100, 200, "100 bytes", "200 bytes"),
            (5000, 9000, "9 kB"),
        ]
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = None
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.connection, "cursor", return_value=cursor_cm
        ), patch.object(api.cache, "set"):
            stats = api._get_timescaledb_stats()
        assert stats["database_name"] == "portal"
        assert stats["hypertable_count"] == 3
        assert stats["host_data_row_estimate"] == 5000


class TestRabbitmqStats:
    def test_returns_cached_stats(self):
        from hpcperfstats.site.lib.machine import api

        cached = {"queue_depth": 1}
        with patch.object(api.cache, "get", return_value=cached):
            assert api._get_rabbitmq_stats() == cached

    def test_fetches_management_api_and_estimates_24h(self):
        from hpcperfstats.site.lib.machine import api

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "messages": 5,
            "message_stats": {"publish": 1000, "deliver_get": 900},
        }
        snapshot = {
            "timestamp": "2020-01-01T00:00:00+00:00",
            "publish": 800,
        }

        def _cache_get(key, default=None):
            if key == api.KEY_ADMIN_RMQ_STATS:
                return None
            if key == api.KEY_ADMIN_RMQ_SNAPSHOT:
                return snapshot
            return default

        import sys

        mock_requests = MagicMock()
        mock_requests.get.return_value = resp
        with patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api.cfg, "get_rmq_server", return_value="rabbit"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q1"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ):
            stats = api._get_rabbitmq_stats()
        assert stats["queue"] == "q1"
        assert stats["messages_published_total"] == 1000
        assert "messages_published_last_24h_estimate" in stats


class TestXaltJidCoverage:
    def test_not_configured_returns_error_shape(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cfg, "get_xalt_user", return_value=""):
            out = api._get_xalt_jid_coverage()
        assert "error" in out
        assert out["window_days"] == 3

    def test_coverage_with_jids_and_xalt_rows(self):
        from hpcperfstats.site.lib.machine import api

        class _JidQs:
            def values_list(self, *_a, **_k):
                return self

            def distinct(self):
                return ["j1", "j2"]

        class _RunQs:
            def filter(self, **_k):
                return self

            def values(self, *_a):
                return self

            def annotate(self, **_k):
                return [{"job_id": "j1", "runs_total": 2, "runs_recent": 1}]

        with patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api.job_data.objects, "filter", return_value=_JidQs()
        ), patch.object(api.run.objects, "using", return_value=_RunQs()), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ):
            out = api._get_xalt_jid_coverage(days=3)
        assert out["total_jids"] == 2
        assert out["jids_with_xalt_data"] == 1
        assert out["missing_jids"] == ["j2"]


class TestGpuDetailInvalidValues:
    def test_gpu_detail_tuple_returns_none_on_bad_values(self):
        from hpcperfstats.site.lib.machine import api

        job = _make_metrics_job({
            "detail_gpu_active": "not-a-number",
            "detail_gpu_util_max": 1.0,
            "detail_gpu_util_mean": 1.0,
            "detail_gpu_count": 1.0,
        })
        assert api._gpu_detail_tuple_from_metrics(job) is None


class TestTimescaledbStatsErrorBranches:
    def test_connection_cursor_failure_returns_empty_stats(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.connection, "cursor", side_effect=RuntimeError("no db")
        ), patch.object(api.cache, "set"):
            stats = api._get_timescaledb_stats()
        assert stats == {}

    def test_per_query_execute_failures_still_return_partial_stats(self):
        from hpcperfstats.site.lib.machine import api

        cursor = MagicMock()

        def _execute(sql, params=None):
            if "current_database" in sql:
                raise RuntimeError("version query failed")
            if "timescaledb" in sql and "extversion" in sql:
                raise RuntimeError("ext query failed")
            if "hypertables" in sql:
                raise RuntimeError("hypertable query failed")
            if "chunks" in sql and "compressed_chunks" in sql:
                raise RuntimeError("chunk count failed")
            if "chunk_sizes" in sql:
                raise RuntimeError("chunk sizes failed")
            if "host_data" in sql:
                raise RuntimeError("host_data size failed")

        cursor.execute.side_effect = _execute
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = None
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.connection, "cursor", return_value=cursor_cm
        ), patch.object(api.cache, "set"):
            stats = api._get_timescaledb_stats()
        assert isinstance(stats, dict)

    def test_cache_get_and_set_exceptions_are_swallowed(self):
        from hpcperfstats.site.lib.machine import api

        cursor = MagicMock()
        cursor.fetchone.return_value = ("db", "ver")
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = None
        with patch.object(api.cache, "get", side_effect=RuntimeError("cache down")), patch.object(
            api.connection, "cursor", return_value=cursor_cm
        ), patch.object(api.cache, "set", side_effect=RuntimeError("cache set fail")):
            stats = api._get_timescaledb_stats()
        assert stats.get("database_name") == "db"


class TestRabbitmqStatsErrorBranches:
    def test_config_lookup_failure_returns_empty(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.cfg, "get_rmq_server", side_effect=RuntimeError("no cfg")
        ):
            assert api._get_rabbitmq_stats() == {}

    def test_connection_error_sets_error_field(self):
        from hpcperfstats.site.lib.machine import api
        import sys

        mock_requests = MagicMock()
        mock_requests.get.side_effect = ConnectionError("refused")
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.cfg, "get_rmq_server", return_value="rabbit"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q1"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ):
            stats = api._get_rabbitmq_stats()
        assert "error" in stats

    def test_non_200_response_sets_http_error(self):
        from hpcperfstats.site.lib.machine import api
        import sys

        resp = MagicMock(status_code=503)
        mock_requests = MagicMock()
        mock_requests.get.return_value = resp
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.cfg, "get_rmq_server", return_value="rabbit"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q1"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ):
            stats = api._get_rabbitmq_stats()
        assert "HTTP 503" in stats["error"]

    def test_json_decode_failure_sets_error(self):
        from hpcperfstats.site.lib.machine import api
        import sys

        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("bad json")
        mock_requests = MagicMock()
        mock_requests.get.return_value = resp
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.cfg, "get_rmq_server", return_value="rabbit"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q1"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ):
            stats = api._get_rabbitmq_stats()
        assert "decode" in stats["error"].lower()

    def test_snapshot_math_failure_still_caches_stats(self):
        from hpcperfstats.site.lib.machine import api
        import sys

        resp = MagicMock(status_code=200)
        resp.json.return_value = {"message_stats": {"publish": 10}}
        mock_requests = MagicMock()
        mock_requests.get.return_value = resp

        def _cache_get(key, default=None):
            if key == api.KEY_ADMIN_RMQ_SNAPSHOT:
                return {"timestamp": "not-a-timestamp", "publish": 1}
            return default

        with patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api.cfg, "get_rmq_server", return_value="rabbit"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q1"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ), patch.object(api.cache, "set", side_effect=RuntimeError("no cache")):
            stats = api._get_rabbitmq_stats()
        assert stats["messages_published_total"] == 10


class TestCacheStatsScanPaths:
    def test_scan_iter_builds_top_keys_and_parses_db0_string(self):
        from hpcperfstats.site.lib.machine import api
        from django.conf import settings

        class _FakeRedis:
            def info(self):
                return {
                    "redis_version": "7.0",
                    "used_memory": 2048,
                    "used_memory_human": "2K",
                    "maxmemory": 4096,
                    "maxmemory_human": "4K",
                    "keyspace_hits": 100,
                    "keyspace_misses": 5,
                    "db0": "keys=42,expires=1",
                }

            def scan_iter(self, count=500):
                yield b"big:key"
                yield "small:key"

            def memory_usage(self, key):
                if key == b"big:key":
                    return 900
                return 10

        backend = MagicMock()
        backend.get_client.return_value = _FakeRedis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend

        with patch.object(api, "cache", fake_cache), patch.object(
            settings, "CACHES", {"default": {"LOCATION": "redis://x", "TIMEOUT": 300}}
        ):
            stats = api._get_cache_stats()

        assert stats["db0_keys"] == 42
        assert stats["cache_hits"] == 100
        assert stats["most_used_cached_keys"][0]["key"] == "big:key"
        assert stats["total_data_cached_bytes_sampled"] == 910

    def test_memory_usage_failure_still_samples_keys(self):
        from hpcperfstats.site.lib.machine import api

        class _FakeRedis:
            def info(self):
                return {"used_memory": 1}

            def scan_iter(self, count=500):
                yield b"k1"

            def memory_usage(self, key):
                raise RuntimeError("no memory_usage")

        backend = MagicMock()
        backend.get_client.return_value = _FakeRedis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend

        with patch.object(api, "cache", fake_cache):
            stats = api._get_cache_stats()
        assert stats["most_used_cached_keys"][0]["approx_size_bytes"] == 0


class TestJobListQueueBarChartNodeHours:
    def test_node_hours_metric_builds_figure(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        qs.values.return_value.annotate.return_value.order_by.return_value.values_list.return_value = [
            ("gpu", 12.5),
        ]
        fig = api._job_list_queue_bar_chart(qs, metric="node_hours")
        assert fig is not None
        assert fig.title.text == "Node hours by queue"


class TestJobListMetricHistPair:
    def test_delegates_to_job_hist_twice(self):
        from hpcperfstats.site.lib.machine import api

        thumb = MagicMock()
        full = MagicMock()
        df = MagicMock()
        with patch.object(api, "job_hist", side_effect=[thumb, full]) as mock_hist:
            out = api._job_list_metric_hist_pair(
                df, "runtime", "hours", "Runtime", (100, 80), (600, 400)
            )
        assert out == (thumb, full)
        assert mock_hist.call_count == 2


class TestSacctIngestErrorBranches:
    def test_sync_acct_failure_returns_500(self):
        from hpcperfstats.site.lib.machine import api
        from hpcperfstats.site.lib.machine.tests.test_api_coverage_gaps import _plain_post

        body = "JobID|State\n123|COMPLETED\n"
        request = _plain_post(
            "/api/sacct/ingest/?date=2024-06-15",
            body.encode("utf-8"),
        )
        vs = MagicMock()
        vs.iterator.return_value = iter([])
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "persist_accounting_daily_file"
        ), patch.object(
            api, "sync_acct_from_content", side_effect=RuntimeError("ingest failed")
        ), patch.object(api.job_data.objects, "filter") as mock_filter:
            mock_filter.return_value.values_list.return_value = vs
            response = api.sacct_ingest(request)
        assert response.status_code == 500
        assert response.data["error"] == "Ingest failed"

    def test_persist_failure_returns_500(self):
        from hpcperfstats.site.lib.machine import api
        from hpcperfstats.site.lib.machine.tests.test_api_coverage_gaps import _plain_post

        body = "JobID|State\n123|COMPLETED\n"
        request = _plain_post(
            "/api/sacct/ingest/?date=2024-06-15",
            body.encode("utf-8"),
        )
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "persist_accounting_daily_file",
            side_effect=OSError("permission denied"),
        ), patch.object(api, "sync_acct_from_content") as mock_sync:
            response = api.sacct_ingest(request)
        assert response.status_code == 500
        assert response.data["error"] == "Failed to write accounting file"
        mock_sync.assert_not_called()
