"""Additional host-side tests to reach 100% line coverage on api.py.

Uses the same LocMem / django_db(databases=[]) contract as test_api_helpers.py.
"""

from __future__ import annotations

import sys
from concurrent.futures import Future
from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.django_db(databases=[])

_API_SETTINGS = {
    "ALLOWED_HOSTS": ["testserver", "example.com", "localhost", "127.0.0.1"],
    "CACHES": {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "api-coverage-closure",
        }
    },
    "CACHE_MIDDLEWARE_KEY_PREFIX": "test-prefix",
}


@pytest.fixture(autouse=True)
def _api_closure_settings():
    with override_settings(**_API_SETTINGS):
        yield


class _BadBool:
    def __bool__(self):
        raise RuntimeError("bad bool")


def _submit_immediate_future(fn):
    fut = Future()
    try:
        fut.set_result(fn())
    except Exception as exc:
        fut.set_exception(exc)
    return fut


class TestCollectFutureDeadlineClosure:
    def test_timeout_leaves_remaining_and_cancels_pending(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError
        from hpcperfstats.site.lib.machine import api

        pending = Future()
        mapping = {pending: "slow"}
        with patch.object(
            api, "as_completed", side_effect=FuturesTimeoutError()
        ):
            results, remaining = api._collect_future_results_with_deadline(
                mapping, 0.05
            )
        assert results == {}
        assert remaining == {"slow"}
        pending.cancel()


class TestGpuAndVisibilityClosure:
    def test_gpu_agg_rows_delegates(self):
        from hpcperfstats.site.lib.machine import api

        j = MagicMock()
        with patch(
            "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.gpu_agg_rows_for_job_window",
            return_value=["row"],
        ) as mock_fn:
            assert api._gpu_agg_rows_for_job(j) == ["row"]
            mock_fn.assert_called_once_with(j)

    def test_compute_job_gpu_stats_dict_agg_and_count_exception(self):
        from hpcperfstats.site.lib.machine import api

        job = MagicMock(jid="j1")
        j = MagicMock()
        with patch.object(api, "cached_orm", side_effect=[{"x": 1}, RuntimeError("no count")]), patch(
            "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.reduce_gpu_agg_to_util_stats",
            return_value=(1, 2.0, 3.0),
        ):
            out = api._compute_job_gpu_stats(job, j, 60, include_gpu_count=True)
        assert out[0] == 1
        assert out[3] is None

    def test_apply_non_staff_via_wrapped_request_session(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        inner = MagicMock()
        inner.session = {"is_staff": False, "username": "alice"}
        request = MagicMock(session=None, _request=inner)
        with patch.object(
            api, "cached_non_staff_visible_accounts", return_value=["acct1"]
        ):
            out = api._apply_non_staff_job_visibility(qs, request)
        assert out is filtered

    def test_apply_non_staff_no_session_returns_queryset_unchanged(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        request = MagicMock(session=None, _request=None)
        assert api._apply_non_staff_job_visibility(qs, request) is qs


class TestJobListQuerysetClosure:
    @pytest.mark.parametrize(
        "params",
        [
            {"metrics_nodelimiter": "1"},
            {"metrics__gte": "1"},
            {"metrics_runtime__badop": "1"},
        ],
    )
    def test_malformed_metric_filters_log_warnings(self, params, caplog):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/", params)
        request.session = {"username": "u", "is_staff": True}
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        with patch.object(api.job_data.objects, "filter", return_value=chain), patch.object(
            api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
        ), patch.object(api, "normalize_job_list_query_params", side_effect=lambda f: f), patch.object(
            api, "expand_month_date_to_range", side_effect=lambda f: f
        ), patch.object(api, "get_job_list_order_by", return_value="-end_time"), patch.object(
            api, "partition_job_list_acct_filters", return_value=({}, None)
        ), patch.object(api, "annotate_job_list_performance_fields", return_value=chain):
            api._build_job_list_queryset_from_request(request)
        assert any("Ignoring" in r.message for r in caplog.records)

    def test_host_filter_and_sample_count_sort(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/",
            {"host_list__contains": "n1", "order_by": "-metrics_distinct_time_count"},
        )
        request.session = {"username": "u", "is_staff": True}
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        with patch.object(api.job_data.objects, "filter", return_value=chain), patch.object(
            api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
        ), patch.object(api, "normalize_job_list_query_params", side_effect=lambda f: f), patch.object(
            api, "expand_month_date_to_range", side_effect=lambda f: f
        ), patch.object(
            api, "get_job_list_order_by", return_value="-metrics_distinct_time_count"
        ), patch.object(
            api, "partition_job_list_acct_filters", return_value=({}, "n1.example.com")
        ), patch.object(api, "annotate_job_list_performance_fields", return_value=chain):
            api._build_job_list_queryset_from_request(request, annotate_all=True)
        chain.filter.assert_any_call(host_list__contains=["n1.example.com"])
        chain.order_by.assert_called()


class TestRedisCacheClientClosure:
    def test_get_client_exceptions_fall_back(self):
        from hpcperfstats.site.lib.machine import api

        backend = MagicMock()
        backend.get_client.side_effect = RuntimeError("fail")
        client_holder = MagicMock()
        client_holder.get_client.side_effect = RuntimeError("fail2")
        fake_cache = MagicMock()
        fake_cache._cache = backend
        fake_cache.client = client_holder
        with patch.object(api, "cache", fake_cache):
            assert api._get_redis_cache_client() is None


class TestCacheInvalidationClosure:
    def test_delete_django_cache_page_all_branches(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/machine/jobs/")
        with patch.object(api.cache, "delete", return_value=True), patch.object(
            api, "get_cache_key", return_value=None
        ), patch.object(api, "_generate_cache_key", return_value="page-key"):
            deleted = api._delete_django_cache_page_entries_for_request(
                request, ["/machine/jobs/"]
            )
        assert deleted >= 2

    def test_redis_delete_scan_typeerror_and_delete_errors(self):
        from hpcperfstats.site.lib.machine import api

        class _Client:
            def scan_iter(self, match=None, count=500):
                if match is not None:
                    raise TypeError("no match")
                yield b"views.decorators.cache.cache_page.MD5digest"

            def delete(self, raw_key):
                raise RuntimeError("del fail")

        assert (
            api._redis_delete_cache_page_keys_matching_digests(_Client(), {"digest"})
            == 0
        )

    def test_redis_delete_success_path(self):
        from hpcperfstats.site.lib.machine import api

        class _Client:
            def scan_iter(self, match=None, count=500):
                yield "views.decorators.cache.cache_page.abc123digest"
                yield "other.abc123digest.suffix"

            def delete(self, raw_key):
                return 1

        assert (
            api._redis_delete_cache_page_keys_matching_digests(_Client(), {"digest"})
            == 1
        )


class TestCacheStatsClosure:
    def test_cache_get_exception_and_outer_scan_abort(self):
        from hpcperfstats.site.lib.machine import api

        class _BadRedis:
            def info(self):
                return {"used_memory": 1}

            def scan_iter(self, count=500):
                yield b"k"
                raise RuntimeError("scan aborted")

            def memory_usage(self, key):
                return 10

        backend = MagicMock()
        backend.get_client.return_value = _BadRedis()
        fake_cache = MagicMock()
        fake_cache.get.side_effect = RuntimeError("cache get fail")
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache), patch.object(
            api.settings, "CACHES", {"default": {"LOCATION": "redis://x", "TIMEOUT": 1}}
        ):
            stats = api._get_cache_stats()
        assert isinstance(stats, dict)

    def test_cache_set_exception_still_returns_stats(self):
        from hpcperfstats.site.lib.machine import api

        class _Redis:
            def info(self):
                return {"used_memory": 5}

        backend = MagicMock()
        backend.get_client.return_value = _Redis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache.set.side_effect = RuntimeError("set fail")
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache):
            stats = api._get_cache_stats()
        assert stats.get("total_data_cached_bytes") == 5


class TestTimescaledbFetchoneClosure:
    def test_fetchone_none_and_partial_rows(self):
        from hpcperfstats.site.lib.machine import api

        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            None,
            None,
            None,
            (None, None),
            (5, None, None, None),
            None,
            (None, None, None),
        ]
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = None
        with patch.object(api.cache, "get", return_value=None), patch.object(
            api.connection, "cursor", return_value=cursor_cm
        ), patch.object(api.cache, "set"):
            stats = api._get_timescaledb_stats()
        assert isinstance(stats, dict)
        assert "database_name" not in stats


class TestRabbitmqClosure:
    def test_import_requests_failure_returns_empty(self):
        from hpcperfstats.site.lib.machine import api

        real_import = __import__

        def _import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("no requests")
            return real_import(name, *args, **kwargs)

        with patch.object(api.cache, "get", return_value=None), patch(
            "builtins.__import__", side_effect=_import
        ):
            assert api._get_rabbitmq_stats() == {}

    def test_snapshot_get_exception_and_naive_timestamp(self):
        from hpcperfstats.site.lib.machine import api

        resp = MagicMock(status_code=200)
        resp.json.return_value = {"message_stats": {"publish": 50}}
        mock_requests = MagicMock()
        mock_requests.get.return_value = resp

        def _cache_get(key, default=None):
            if key == api.KEY_ADMIN_RMQ_SNAPSHOT:
                raise RuntimeError("snap fail")
            return default

        with patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api.cfg, "get_rmq_server", return_value="r"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ), patch.object(api.cache, "set"):
            stats = api._get_rabbitmq_stats()
        assert stats["messages_published_total"] == 50

    def test_snapshot_math_with_naive_iso_timestamp(self):
        from hpcperfstats.site.lib.machine import api

        resp = MagicMock(status_code=200)
        resp.json.return_value = {"message_stats": {"publish": 100}}
        mock_requests = MagicMock()
        mock_requests.get.return_value = resp
        snapshot = {"timestamp": "2020-01-01T00:00:00", "publish": 80}

        def _cache_get(key, default=None):
            if key == api.KEY_ADMIN_RMQ_SNAPSHOT:
                return snapshot
            return default

        with patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api.cfg, "get_rmq_server", return_value="r"
        ), patch.object(api.cfg, "get_rmq_queue", return_value="q"), patch.dict(
            sys.modules, {"requests": mock_requests}
        ), patch.object(api.cache, "set"):
            stats = api._get_rabbitmq_stats()
        assert "messages_published_last_24h_estimate" in stats


class TestRecentRabbitmqHostsClosure:
    def test_scan_skips_invalid_hosts_and_bad_values(self):
        from hpcperfstats.site.lib.machine import api

        class _Client:
            def scan_iter(self, match=None, count=1000):
                yield b"recent_host:short"
                yield b"recent_host:n1.example.com"
                yield "recent_host:n2.example.com"

            def get(self, key):
                if b"n1" in key if isinstance(key, bytes) else "n1" in key:
                    return b"not-a-number"
                return str(int(datetime(2024, 1, 1, tzinfo=dt_timezone.utc).timestamp()))

        backend = MagicMock()
        backend.get_client.return_value = _Client()
        fake_cache = MagicMock()
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache):
            rows = api._get_recent_rabbitmq_host_stats()
        assert isinstance(rows, list)

    def test_outer_exception_returns_empty_list(self):
        from hpcperfstats.site.lib.machine import api

        fake_cache = MagicMock()
        fake_cache._cache = MagicMock()
        fake_cache._cache.get_client.side_effect = RuntimeError("boom")
        with patch.object(api, "cache", fake_cache):
            assert api._get_recent_rabbitmq_host_stats() == []


class TestSanitizeAndBokehClosure:
    def test_sanitize_json_item_exception_returns_none(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api, "json_item", side_effect=RuntimeError("boom")):
            assert api._sanitize_hist_plot_item(MagicMock()) is None

    def test_valid_payload_root_ids_int_list(self):
        from hpcperfstats.site.lib.machine import api

        payload = {"doc": {"roots": {"root_ids": ["1"]}}, "root_ids": [1]}
        assert api._is_valid_bokeh_json_item_payload(payload) is True


class TestHistogramClosure:
    def test_build_histogram_queryset_count_failure(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/")
        chain = MagicMock()
        chain.count.side_effect = RuntimeError("db")
        with patch.object(
            api,
            "_build_job_list_queryset_from_request",
            return_value=(chain, {}, {}, "-end_time"),
        ):
            qs, nj, fields, cur = api._build_histogram_queryset(request)
        assert nj == 0

    def test_histogram_dataframe_metric_name_split(self):
        from hpcperfstats.site.lib.machine import api

        job_list_qs = MagicMock()
        job_list_qs.values.return_value = [
            {
                "jid": "j1",
                "start_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "submit_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "runtime": 3600,
                "nhosts": 1,
            }
        ]
        metrics_qs = MagicMock()
        metrics_qs.filter.return_value.values.return_value = [
            {"jid_id": "j1", "metric": "cpu_hours", "units": "h", "value": 1.0}
        ]
        with patch.object(api.metrics_data.objects, "filter", return_value=metrics_qs):
            _df, _hist_metrics, jids = api._build_histogram_dataframe(
                job_list_qs, {"cpu_hours__gte": "0"}
            )
        assert jids == ["j1"]


class TestSacctIngestClosure:
    def test_body_decode_failure(self):
        from hpcperfstats.site.lib.machine import api
        from hpcperfstats.site.lib.machine.tests.test_api_coverage_gaps import _plain_post

        request = _plain_post("/api/sacct/ingest/?date=2024-01-01", b"x")
        bad_body = MagicMock()
        bad_body.__len__ = MagicMock(return_value=1)
        bad_body.decode = MagicMock(side_effect=RuntimeError("decode fail"))
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            type(request), "body", new=property(lambda self: bad_body)
        ):
            response = api.sacct_ingest(request)
        assert response.status_code == 400

    @override_settings(DEBUG=True)
    def test_sync_acct_debug_reraises(self):
        from hpcperfstats.site.lib.machine import api
        from hpcperfstats.site.lib.machine.tests.test_api_coverage_gaps import _plain_post

        body = "JobID|State\n1|COMPLETED\n"
        request = _plain_post("/api/sacct/ingest/?date=2024-06-15", body.encode())
        vs = MagicMock()
        vs.iterator.return_value = iter([])
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "persist_accounting_daily_file"
        ), patch.object(
            api, "sync_acct_from_content", side_effect=RuntimeError("ingest")
        ), patch.object(api.job_data.objects, "filter") as mock_filter:
            mock_filter.return_value.values_list.return_value = vs
            with pytest.raises(RuntimeError, match="ingest"):
                api.sacct_ingest(request)

    @override_settings(DEBUG=True)
    def test_persist_file_debug_reraises(self):
        from hpcperfstats.site.lib.machine import api
        from hpcperfstats.site.lib.machine.tests.test_api_coverage_gaps import _plain_post

        request = _plain_post("/api/sacct/ingest/?date=2024-06-15", b"body")
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "persist_accounting_daily_file", side_effect=RuntimeError("write")
        ):
            with pytest.raises(RuntimeError, match="write"):
                api.sacct_ingest(request)


class TestAdminMonitorRefreshClosure:
    def test_refresh_cache_delete_exception_swallowed(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "cache", "refresh": "1"}
        )
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "_get_cache_stats", return_value={"ok": True}
        ), patch.object(api.cache, "delete", side_effect=RuntimeError("del")):
            response = api.admin_monitor(request)
        assert response.status_code == 200


class TestXaltCoverageClosure:
    def test_xalt_coverage_empty_jids_and_query_failure(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api.job_data.objects, "filter"
        ) as mock_filter:
            mock_filter.return_value.values_list.return_value.distinct.return_value = []
            out = api._get_xalt_jid_coverage(days=3)
        assert out["total_jids"] == 0

        with patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "cached_orm", side_effect=RuntimeError("xalt down")
        ):
            err = api._get_xalt_jid_coverage(days=3)
        assert "error" in err

    def test_xalt_coverage_with_present_and_missing_jids(self):
        from hpcperfstats.site.lib.machine import api

        class _Vals:
            def distinct(self):
                return ["j1", "j2", "j3"]

        class _XaltQs:
            def filter(self, **_k):
                return self

            def values(self, *_a):
                return self

            def annotate(self, **_k):
                return self

            def __iter__(self):
                yield {
                    "job_id": "j1",
                    "runs_total": 2,
                    "runs_recent": 1,
                }

        job_qs = MagicMock()
        job_qs.values_list.return_value.distinct.return_value = ["j1", "j2", "j3"]
        with patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api.job_data.objects, "filter", return_value=job_qs
        ), patch.object(api.run.objects, "using", return_value=_XaltQs()), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ):
            out = api._get_xalt_jid_coverage(days=3, missing_limit=1)
        assert out["total_jids"] == 3
        assert out["jids_with_xalt_data"] >= 1


class TestJobMonitorGpuDaysClosure:
    def test_invalid_days_parsed_as_30(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/job_monitor/gpu/", {"username": "bob", "days": "bad"}
        )
        request.session = {"is_staff": True}

        class _MdChain:
            def filter(self, **_k):
                return self

            def exists(self):
                return False

        class _JobQs:
            def only(self, *_f):
                return []

        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", side_effect=lambda _k, _t, fn: fn()), patch.object(
            api.metrics_data.objects, "filter", return_value=_MdChain()
        ), patch.object(api.job_data.objects, "filter", return_value=_JobQs()):
            response = api.job_monitor_gpu_for_user(request)
        assert response.status_code == 200
        assert response.data["has_data"] is False


class TestRemainingHelperLinesClosure:
    def test_compute_job_gpu_stats_coerces_dict_agg_to_list(self):
        from hpcperfstats.site.lib.machine import api

        job = MagicMock(jid="j1")
        j = MagicMock()
        with patch.object(api, "cached_orm", return_value={"gpu": 1}), patch(
            "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.reduce_gpu_agg_to_util_stats",
            return_value=(1, 2.0, 3.0),
        ) as mock_reduce:
            out = api._compute_job_gpu_stats(job, j, 60, include_gpu_count=False)
        mock_reduce.assert_called_once_with([{"gpu": 1}])
        assert out[0] == 1

    def test_get_api_key_authorization_and_x_api_key(self):
        from hpcperfstats.site.lib.machine import api

        req1 = RequestFactory().get("/")
        req1.META["HTTP_AUTHORIZATION"] = "Api-Key   "
        assert api._get_api_key_from_request(req1) is None

        req2 = RequestFactory().get("/")
        req2.META["HTTP_X_API_KEY"] = "secret-key"
        assert api._get_api_key_from_request(req2) == "secret-key"

    def test_non_staff_visible_accounts_filter(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        request = RequestFactory().get("/")
        request.session = {"is_staff": False, "username": "alice"}
        with patch.object(api, "cached_non_staff_visible_accounts", return_value=["a1"]):
            assert api._apply_non_staff_job_visibility(qs, request) is filtered

    def test_metric_filter_empty_name_or_op(self, caplog):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/", {"metrics___gte": "1", "metrics_runtime__": "1"}
        )
        request.session = {"username": "u", "is_staff": True}
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        with patch.object(api.job_data.objects, "filter", return_value=chain), patch.object(
            api, "_apply_non_staff_job_visibility", side_effect=lambda qs, _r: qs
        ), patch.object(api, "normalize_job_list_query_params", side_effect=lambda f: f), patch.object(
            api, "expand_month_date_to_range", side_effect=lambda f: f
        ), patch.object(api, "get_job_list_order_by", return_value="-end_time"), patch.object(
            api, "partition_job_list_acct_filters", return_value=({}, None)
        ):
            api._build_job_list_queryset_from_request(request)
        assert any("Ignoring malformed" in r.message for r in caplog.records)

    def test_delete_cache_page_get_cache_key_branch(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/machine/")
        with patch.object(api.cache, "delete", return_value=True), patch.object(
            api, "get_cache_key", return_value="ck"
        ):
            deleted = api._delete_django_cache_page_entries_for_request(request, ["/machine/"])
        assert deleted >= 2

    def test_cache_stats_db0_string_parse_failure(self):
        from hpcperfstats.site.lib.machine import api

        class _Redis:
            def info(self):
                return {"used_memory": 1, "db0": "keys=bad,expires=0"}

            def scan_iter(self, count=500):
                return iter([])

        backend = MagicMock()
        backend.get_client.return_value = _Redis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache):
            stats = api._get_cache_stats()
        assert stats["total_data_cached_bytes"] == 1
        assert "db0_keys" not in stats

    def test_redis_delete_typeerror_scan_and_skip_non_cache_keys(self):
        from hpcperfstats.site.lib.machine import api

        class _Client:
            call = 0

            def scan_iter(self, match=None, count=500):
                self.call += 1
                if match is not None:
                    raise TypeError("no match")
                yield "unrelated:key"

            def delete(self, raw_key):
                return 1

        client = _Client()
        assert api._redis_delete_cache_page_keys_matching_digests(client, {"d"}) == 0

    def test_cache_stats_info_branches_and_no_client(self):
        from hpcperfstats.site.lib.machine import api

        class _Redis:
            def info(self):
                return {
                    "used_memory": 100,
                    "used_memory_human": "100B",
                    "maxmemory": 200,
                    "maxmemory_human": "200B",
                    "db0": "keys=1,expires=0",
                }

            def scan_iter(self, count=500):
                return iter([])

        backend = MagicMock()
        backend.get_client.return_value = _Redis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache), patch.object(
            api.settings, "CACHES", {"default": {"LOCATION": "redis://x", "TIMEOUT": 1}}
        ):
            stats = api._get_cache_stats()
        assert stats["total_cache_usable_human"] == "200B"
        assert stats["total_data_cached_bytes"] == 100
        assert stats["db0_keys"] == 1

        with patch.object(api, "_get_redis_cache_client", return_value=None):
            stats2 = api._get_cache_stats()
        assert "location" in stats2

    def test_gpu_stats_inner_exception_swallowed(self):
        from hpcperfstats.site.lib.machine import api

        job = MagicMock(jid="j1")
        with patch.object(api, "cached_orm", side_effect=RuntimeError("gpu fail")):
            out = api._compute_job_gpu_stats(job, MagicMock(), 60, include_gpu_count=False)
        assert out == (None, None, None, None)

    def test_api_key_valid_empty_and_save_failure(self):
        from hpcperfstats.site.lib.machine import api

        assert api._api_key_valid("") is None
        assert api._api_key_valid(None) is None

        key_obj = MagicMock()
        with patch.object(api.ApiKey.objects, "get", return_value=key_obj), patch.object(
            key_obj, "save", side_effect=RuntimeError("save fail")
        ):
            assert api._api_key_valid("raw-key") is key_obj

    def test_non_staff_no_visible_accounts_username_only(self):
        from hpcperfstats.site.lib.machine import api

        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        request = RequestFactory().get("/")
        request.session = {"is_staff": False, "username": "alice"}
        with patch.object(api, "cached_non_staff_visible_accounts", return_value=[]):
            assert api._apply_non_staff_job_visibility(qs, request) is filtered
        qs.filter.assert_called_once()

    def test_delete_cache_page_ck_and_nk_branches(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/machine/")
        with patch.object(api.cache, "delete", return_value=True), patch.object(
            api, "get_cache_key", side_effect=[None, "ck"]
        ), patch.object(api, "_generate_cache_key", return_value="nk"):
            deleted = api._delete_django_cache_page_entries_for_request(request, ["/machine/"])
        assert deleted >= 2

    def test_redis_delete_inner_delete_exception(self):
        from hpcperfstats.site.lib.machine import api

        digest = "abc123"

        class _Client:
            def scan_iter(self, match=None, count=500):
                yield f"views.decorators.cache.cache_page.{digest}.GET"

            def delete(self, raw_key):
                raise RuntimeError("del fail")

        assert api._redis_delete_cache_page_keys_matching_digests(_Client(), {digest}) == 0

    def test_redis_delete_typeerror_fallback_then_delete(self):
        from hpcperfstats.site.lib.machine import api

        client = MagicMock()
        client.scan_iter.side_effect = [
            TypeError("match unsupported"),
            iter(["views.decorators.cache.cache_page.abc123digest"]),
        ]
        client.delete.return_value = 1
        assert api._redis_delete_cache_page_keys_matching_digests(client, {"digest"}) == 1

    def test_cache_stats_cached_snapshot_and_outer_failure(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cache, "get", return_value={"cached": True}):
            assert api._get_cache_stats() == {"cached": True}

        class _BadClient:
            def info(self):
                raise RuntimeError("redis down")

        with patch.object(api.cache, "get", return_value=None), patch.object(
            api, "_get_redis_cache_client", return_value=_BadClient()
        ), patch.object(api.settings, "CACHES", {"default": {"LOCATION": "redis://x"}}):
            stats = api._get_cache_stats()
        assert stats.get("location") == "redis://x"

    def test_cache_stats_scan_break_and_memory_usage_failure(self):
        from hpcperfstats.site.lib.machine import api

        class _Redis:
            def info(self):
                return {"used_memory": 1, "db0": "not=valid"}

            def scan_iter(self, count=500):
                for i in range(600):
                    yield f"k{i}"

            def memory_usage(self, key):
                raise RuntimeError("mem fail")

        backend = MagicMock()
        backend.get_client.return_value = _Redis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache):
            stats = api._get_cache_stats()
        assert stats["total_data_cached_bytes"] == 1
        assert "most_used_cached_keys" in stats

    def test_cache_stats_top_keys_scan_exception(self):
        from hpcperfstats.site.lib.machine import api

        class _Redis:
            def info(self):
                return {"used_memory": 1}

            def scan_iter(self, count=500):
                raise RuntimeError("scan boom")

        backend = MagicMock()
        backend.get_client.return_value = _Redis()
        fake_cache = MagicMock()
        fake_cache.get.return_value = None
        fake_cache._cache = backend
        with patch.object(api, "cache", fake_cache):
            stats = api._get_cache_stats()
        assert stats["total_data_cached_bytes"] == 1

    def test_rabbitmq_stats_cache_get_exception(self):
        from hpcperfstats.site.lib.machine import api

        with patch.object(api.cache, "get", side_effect=RuntimeError("cache fail")), patch(
            "builtins.__import__", side_effect=lambda name, *a, **k: (_ for _ in ()).throw(ImportError("no requests")) if name == "requests" else __import__(name, *a, **k)
        ):
            assert api._get_rabbitmq_stats() == {}

    def test_recent_rabbitmq_get_client_exception_and_bad_values(self):
        from hpcperfstats.site.lib.machine import api

        class _Backend:
            def get_client(self):
                raise RuntimeError("no client")

        class _Client:
            def scan_iter(self, match=None, count=1000):
                yield b"recent_host:badhost"
                yield "recent_host:n1.example.com"

            def get(self, key):
                if b"badhost" in key if isinstance(key, bytes) else "badhost" in key:
                    raise RuntimeError("get fail")
                return "not-a-ts"

        fake_cache = MagicMock()
        fake_cache._cache = _Backend()
        fake_cache.client = _Client()
        with patch.object(api, "cache", fake_cache):
            rows = api._get_recent_rabbitmq_host_stats()
        assert rows == []

    def test_recent_rabbitmq_bytes_key_and_valid_row(self):
        from hpcperfstats.site.lib.machine import api

        ts = int(datetime(2024, 6, 1, tzinfo=dt_timezone.utc).timestamp())

        class _Client:
            def scan_iter(self, match=None, count=1000):
                yield b"recent_host:n2.example.com"

            def get(self, key):
                return str(ts).encode("utf-8")

        backend = MagicMock()
        backend.get_client.side_effect = RuntimeError("unwrap fail")
        fake_cache = MagicMock()
        fake_cache._cache = backend
        fake_cache.client = _Client()
        with patch.object(api, "cache", fake_cache):
            rows = api._get_recent_rabbitmq_host_stats()
        assert len(rows) == 1

    def test_recent_rabbitmq_scan_paths(self):
        from hpcperfstats.site.lib.machine import api

        ts = int(datetime(2024, 6, 1, tzinfo=dt_timezone.utc).timestamp())

        class _Client:
            def scan_iter(self, match=None, count=1000):
                yield "recent_host:badhost"
                yield b"recent_host:n3.example.com"

            def get(self, key):
                key_s = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                if "badhost" in key_s:
                    raise RuntimeError("get fail")
                if "n3.example.com" in key_s:
                    return str(ts).encode("utf-8")
                return None

        backend = MagicMock()
        backend.get_client.side_effect = RuntimeError("unwrap fail")
        fake_cache = MagicMock()
        fake_cache._cache = backend
        fake_cache.client = _Client()
        with patch.object(api, "cache", fake_cache):
            rows = api._get_recent_rabbitmq_host_stats()
        assert len(rows) == 1
        assert rows[0]["host"] == "n3.example.com"

    def test_histogram_queryset_success_returns_count(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/")
        chain = MagicMock()
        chain.count.return_value = 7
        with patch.object(
            api,
            "_build_job_list_queryset_from_request",
            return_value=(chain, {"k": "v"}, {}, {}),
        ):
            _qs, nj, fields, _cur = api._build_histogram_queryset(request)
        assert nj == 7
        assert fields == {"k": "v"}

    def test_build_histogram_dataframe_metric_rows(self):
        from hpcperfstats.site.lib.machine import api

        job_list_qs = MagicMock()
        job_list_qs.values.return_value = [
            {
                "jid": "j1",
                "start_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "submit_time": datetime(2024, 1, 1, tzinfo=dt_timezone.utc),
                "runtime": 3600,
                "nhosts": 1,
            }
        ]
        md_qs = MagicMock()
        md_qs.values.return_value = [
            {"jid_id": "j1", "metric": "gflops", "units": "GFLOP/s", "value": 1.0}
        ]
        with patch.object(api.metrics_data.objects, "filter", return_value=md_qs):
            df, hist_metrics, jids = api._build_histogram_dataframe(
                job_list_qs, {"gflops__gte": "0"}
            )
        assert jids == ["j1"]
        assert ("gflops", "GFLOP/s") in hist_metrics

    def test_bokeh_payload_empty_root_ids_list(self):
        from hpcperfstats.site.lib.machine import api

        payload = {"doc": {"roots": {"root_ids": ["a"]}}, "root_ids": []}
        assert api._is_valid_bokeh_json_item_payload(payload) is False

    def test_recent_rabbitmq_get_client_failures_return_empty(self):
        from hpcperfstats.site.lib.machine import api

        class _Cache:
            _cache = type(
                "_Backend",
                (),
                {"get_client": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("unwrap")))},
            )()
            client = type(
                "_Wrapper",
                (),
                {"get_client": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("wrap")))},
            )()

        with patch.object(api, "cache", _Cache()):
            assert api._get_recent_rabbitmq_host_stats() == []

    def test_recent_rabbitmq_all_skip_and_success_paths(self):
        from hpcperfstats.site.lib.machine import api

        ts = int(datetime(2024, 6, 1, tzinfo=dt_timezone.utc).timestamp())

        class _Client:
            def scan_iter(self, match=None, count=1000):
                yield "other:prefix"
                yield "recent_host:nodot"
                yield "recent_host:n4.example.com"
                yield "recent_host:n5.example.com"
                yield "recent_host:n6.example.com"
                yield "recent_host:n7.example.com"

            def get(self, key):
                key_s = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                if "n4.example.com" in key_s:
                    raise RuntimeError("get fail")
                if "n5.example.com" in key_s:
                    return None
                if "n6.example.com" in key_s:
                    return "not-a-ts"
                return str(ts)

        class _Cache:
            _cache = type(
                "_Backend",
                (),
                {"get_client": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("unwrap")))},
            )()

        cache_obj = _Cache()
        cache_obj.client = _Client()
        with patch.object(api, "cache", cache_obj):
            rows = api._get_recent_rabbitmq_host_stats()
        assert len(rows) == 1
        assert rows[0]["host"] == "n7.example.com"

    def test_recent_rabbitmq_outer_scan_exception_clears_rows(self):
        from hpcperfstats.site.lib.machine import api

        class _Client:
            def scan_iter(self, match=None, count=1000):
                raise RuntimeError("scan boom")

        class _Cache:
            _cache = None
            client = _Client()

        with patch.object(api, "cache", _Cache()):
            assert api._get_recent_rabbitmq_host_stats() == []

    def test_job_plots_harvest_timeout(self):
        from types import SimpleNamespace

        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from hpcperfstats.site.lib.machine import api

        api._job_plot_inflight.clear()
        request = RequestFactory().get("/api/jobs/j1/plots/", {"progressive": "1"})
        request.session = {"username": "u", "is_staff": True}
        fake_job = SimpleNamespace(jid="j1")
        pending = Future()

        class _FakeExecutor:
            def submit(self, fn):
                return pending

        def _cache_get(key, default=None):
            if "throttle" in str(key).lower():
                return default
            return None

        def _fake_as_completed(futures, timeout=None):
            pending_list = list(futures)
            if pending_list:
                yield pending_list[0]
            raise FuturesTimeoutError()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(fake_job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "get_live_distinct_time_count_for_jid", return_value=1
        ), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=_cache_get
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api.jid_table, "jid_table", return_value=SimpleNamespace()
        ), patch.object(api, "_get_small_executor", return_value=_FakeExecutor()), patch(
            "hpcperfstats.site.lib.machine.api.as_completed",
            side_effect=_fake_as_completed,
        ):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["status"] == "partial"

    def test_job_plots_zoom_missing_plot_data_still_fetches(self):
        from types import SimpleNamespace

        from hpcperfstats.site.lib.machine import api

        api._job_plot_inflight.clear()
        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot", "zoom": "1"}
        )
        request.session = {"username": "u", "is_staff": True}
        fake_job = SimpleNamespace(jid="j1")
        done = Future()
        done.set_result(({"zoomed": True}, None))

        class _FakeExecutor:
            def submit(self, fn):
                return done

        def _cache_get(key, default=None):
            key_s = str(key)
            if "throttle" in key_s.lower():
                return default
            if "JOB_PLOTS_DATA" in key_s:
                return None
            return default

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(fake_job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "get_live_distinct_time_count_for_jid", return_value=1
        ), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=_cache_get
        ), patch.object(
            api, "load_cached_job_plot_entry", return_value=None
        ), patch.object(
            api.jid_table, "jid_table", return_value=SimpleNamespace()
        ), patch.object(api, "_get_small_executor", return_value=_FakeExecutor()):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200

    def test_xalt_blank_job_id_skipped_in_coverage(self):
        from hpcperfstats.site.lib.machine import api

        class _XaltQs:
            def filter(self, **_k):
                return self

            def values(self, *_a):
                return self

            def annotate(self, **_k):
                return self

            def __iter__(self):
                yield {"job_id": "", "runs_total": 1, "runs_recent": 0}
                yield {"job_id": "j1", "runs_total": 2, "runs_recent": 1}

        job_qs = MagicMock()
        job_qs.values_list.return_value.distinct.return_value = ["j1", "j2"]
        with patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api.job_data.objects, "filter", return_value=job_qs
        ), patch.object(api.run.objects, "using", return_value=_XaltQs()), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ):
            out = api._get_xalt_jid_coverage(days=3, missing_limit=5)
        assert out["jids_with_xalt_data"] == 1

    def test_admin_monitor_refresh_delete_exception(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/admin_monitor/", {"refresh": "1"})
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "cached_orm", return_value=[]
        ), patch.object(api, "_get_recent_rabbitmq_host_stats", return_value=[]), patch.object(
            api, "_get_cache_stats", return_value={}
        ), patch.object(api, "_get_rabbitmq_stats", return_value={}), patch.object(
            api, "_get_timescaledb_stats", return_value={}
        ), patch.object(api, "_get_xalt_jid_coverage", return_value={}), patch.object(
            api.cache, "delete", side_effect=RuntimeError("del fail")
        ):
            response = api.admin_monitor(request)
        assert response.status_code == 200
