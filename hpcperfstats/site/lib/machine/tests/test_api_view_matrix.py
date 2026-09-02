"""One test class per ``@api_view`` in api.py not fully covered elsewhere.

Complements ``test_api_misc.py`` and ``test_api_coverage_gaps.py`` with focused
view-matrix branches (auth gates, validation, mocked success paths).
"""

from concurrent.futures import Future
from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings
from rest_framework.test import APIRequestFactory

from .csrf_test_utils import csrf_headers

pytestmark = pytest.mark.django_db(databases=[])

_API_COVERAGE_GAP_SETTINGS = {
    "ALLOWED_HOSTS": ["testserver", "example.com", "localhost", "127.0.0.1"],
    "CACHES": {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "api-view-matrix-tests",
        }
    },
}


@pytest.fixture(autouse=True)
def _api_view_matrix_settings():
    with override_settings(**_API_COVERAGE_GAP_SETTINGS):
        yield


class TestSessionInfoView:
    def test_requires_auth(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/session-info/")
        denied = api.Response({"detail": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.session_info(request)
        assert response.status_code == 401

    def test_returns_session_payload(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/session-info/")
        request.session = {"username": "bob", "is_staff": False}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api.cfg, "get_host_name_ext", return_value="cluster"
        ):
            response = api.session_info(request)
        assert response.status_code == 200
        assert response.data["logged_in"] is True
        assert response.data["username"] == "bob"


class TestHomeOptionsView:
    def test_requires_auth(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/home/")
        denied = api.Response({"detail": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.home_options(request)
        assert response.status_code == 401

    def test_returns_options_shape(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/home/")

        class _Future:
            def __init__(self, val):
                self._val = val

            def result(self):
                return self._val

        def _submit(_fn, *_args, **_kwargs):
            return _Future(_args[-1]())

        mock_exec = MagicMock()
        mock_exec.submit.side_effect = _submit

        mock_jd = MagicMock()
        mock_jd.objects.dates.return_value = []
        mock_jd.objects.distinct.return_value.values_list.return_value = []
        (
            mock_jd.objects.exclude.return_value.distinct.return_value.values_list.return_value
        ) = []
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_small_executor", return_value=mock_exec
        ), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ), patch.object(api, "job_data", mock_jd), patch.object(
            api, "job_metrics_catalog_entries", return_value=[]
        ), patch.object(api.cfg, "get_host_name_ext", return_value="hpc"):
            response = api.home_options(request)
        assert response.status_code == 200
        assert "metrics" in response.data
        assert response.data["machine_name"] == "hpc"


class TestTestLoginUserView:
    def test_404_when_flag_off(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/test-login/user/")
        with patch(
            "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
            return_value=False,
        ):
            response = api.test_login_user(request)
        assert response.status_code == 404

    def test_get_unconfigured(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/test-login/user/")
        request.session = {"is_staff": True}
        with patch(
            "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
            return_value=True,
        ), patch.object(
            api, "_require_staff", return_value=None
        ), patch.object(api.TestLoginUser, "get_singleton", return_value=None):
            response = api.test_login_user(request)
        assert response.status_code == 200
        assert response.data["configured"] is False
        assert response.data["username"] is None

    def test_post_validation_error(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().post(
            "/api/test-login/user/",
            data={},
            content_type="application/json",
        )
        request.session = {"is_staff": True, "username": "staffer"}
        request.data = {"username": "", "password": ""}
        with patch(
            "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
            return_value=True,
        ), patch.object(
            api, "_require_staff", return_value=None
        ), patch.object(api, "_require_csrf_for_session_post", return_value=None):
            response = api.test_login_user(request)
        assert response.status_code == 400


class TestUserApiKeyStatusView:
    def test_requires_oauth_session(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/user-api-key/")
        with patch.object(api, "check_for_tokens", return_value=False):
            response = api.user_api_key_status(request)
        assert response.status_code == 401

    def test_returns_existing_key_without_raw(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/user-api-key/")
        request.session = {"username": "alice", "is_staff": False}
        key_obj = MagicMock(key_prefix="abcd")
        qs = MagicMock()
        qs.order_by.return_value.first.return_value = key_obj
        with patch.object(api, "check_for_tokens", return_value=True), patch.object(
            api.ApiKey.objects, "filter", return_value=qs
        ):
            response = api.user_api_key_status(request)
        assert response.status_code == 200
        assert response.data["raw_key"] is None
        assert response.data["key_prefix"] == "abcd"

    def test_creates_key_when_none_exists(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/user-api-key/")
        request.session = {"username": "alice", "is_staff": True}
        qs = MagicMock()
        qs.order_by.return_value.first.return_value = None
        new_key = MagicMock(key_prefix="wxyz")
        with patch.object(api, "check_for_tokens", return_value=True), patch.object(
            api.ApiKey.objects, "filter", return_value=qs
        ), patch.object(
            api.ApiKey, "create_from_raw_key", return_value=(new_key, "raw-secret")
        ):
            response = api.user_api_key_status(request)
        assert response.status_code == 200
        assert response.data["raw_key"] == "raw-secret"


class TestUserApiKeyRotateView:
    def test_requires_csrf_for_post(self):
        from hpcperfstats.site.lib.machine import api

        factory = APIRequestFactory()
        request = factory.post("/api/user-api-key/rotate/")
        request.session = {"username": "alice", "is_staff": False}
        with patch.object(api, "check_for_tokens", return_value=True):
            response = api.user_api_key_rotate(request)
        assert response.status_code == 403
        assert "CSRF" in response.data["detail"]

    def test_rotates_active_keys(self):
        from hpcperfstats.site.lib.machine import api

        factory = APIRequestFactory()
        request = factory.post(
            "/api/user-api-key/rotate/",
            HTTP_X_CSRFTOKEN="token",
        )
        request.session = {"username": "alice", "is_staff": False}
        new_key = MagicMock(key_prefix="new1")
        filter_qs = MagicMock()
        with patch.object(api, "check_for_tokens", return_value=True), patch.object(
            api.ApiKey.objects, "filter", return_value=filter_qs
        ), patch.object(
            api.ApiKey, "create_from_raw_key", return_value=(new_key, "fresh-key")
        ):
            response = api.user_api_key_rotate(request)
        assert response.status_code == 200
        filter_qs.update.assert_called_once_with(is_active=False)
        assert response.data["raw_key"] == "fresh-key"


class TestDropStaffForSessionView:
    def test_requires_csrf_for_post(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().post("/api/session/drop-staff/")
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None):
            response = api.drop_staff_for_session(request)
        assert response.status_code == 403
        assert "CSRF" in response.data["detail"]

    def test_requires_staff(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().post("/api/session/drop-staff/", **csrf_headers())
        request.session = {"is_staff": False}
        with patch.object(api, "_require_staff") as mock_staff:
            mock_staff.return_value = api.Response({"error": "no"}, status=403)
            response = api.drop_staff_for_session(request)
        assert response.status_code == 403

    def test_clears_staff_flag(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().post("/api/session/drop-staff/", **csrf_headers())
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None):
            response = api.drop_staff_for_session(request)
        assert response.status_code == 200
        assert request.session["is_staff"] is False


class TestJobListView:
    def test_requires_auth(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/")
        denied = api.Response({"detail": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.job_list(request)
        assert response.status_code == 401

    def test_empty_query_returns_zero_jobs(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/")
        request.session = {"username": "u", "is_staff": False}
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api,
            "_build_job_list_queryset_from_request",
            return_value=(mock_qs, {}, None, "-end_time"),
        ), patch.object(
            api, "build_job_list_qname_and_filter_summary", return_value=(None, [])
        ):
            response = api.job_list(request)
        assert response.status_code == 200
        assert response.data["nj"] == 0
        assert response.data["job_list"] == []

    def test_paginated_response_with_serializer(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/?page=1")
        request.session = {"username": "u", "is_staff": True}

        page = MagicMock()
        page.object_list = [MagicMock()]
        page.number = 1
        page.has_previous.return_value = False
        page.has_next.return_value = False

        paginator = MagicMock()
        paginator.num_pages = 1
        paginator.page.return_value = page

        mock_qs = MagicMock()
        mock_qs.count.return_value = 1
        mock_qs.aggregate.return_value = {"total_node_hours": 8.0}

        ser = MagicMock()
        ser.data = [{"jid": "j1", "sample_count": 5}]

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api,
            "_build_job_list_queryset_from_request",
            return_value=(mock_qs, {}, None, "-end_time"),
        ), patch.object(api, "build_job_list_qname_and_filter_summary", return_value=(None, [])), patch.object(
            api, "Paginator", return_value=paginator
        ), patch.object(
            api, "aggregate_queue_wait_seconds_stats", return_value={}
        ), patch.object(api, "JobListSerializer", return_value=ser):
            response = api.job_list(request)
        assert response.status_code == 200
        assert response.data["job_list"] == [{"jid": "j1", "sample_count": 5}]
        assert response.data["pagination"]["page"] == 1


class TestJobListHistogramsView:
    def test_missing_group_returns_400(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/")
        request.session = {"username": "u"}
        with patch.object(api, "_require_auth", return_value=None):
            response = api.job_list_histograms(request)
        assert response.status_code == 400
        assert "group" in response.data["error"].lower()

    def test_empty_nj_metric_group_returns_null_plots(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/", {"group": "metric", "metric": "runtime"}
        )
        request.session = {"username": "u"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 0, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 200
        assert response.data["nj"] == 0
        assert response.data["plot_item_thumb"] is None

    def test_unknown_group_returns_400(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/", {"group": "unknown"}
        )
        request.session = {"username": "u"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 1, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400

    def test_metric_group_missing_metric_param(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/", {"group": "metric"})
        request.session = {"username": "u"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400

    def test_metric_not_available_for_query(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/",
            {"group": "metric", "metric": "missing_metric"},
        )
        request.session = {"username": "u"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
        ), patch.object(
            api, "_build_histogram_dataframe", return_value=(MagicMock(), [], [])
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400
        assert "not available" in response.data["error"].lower()

    def test_metric_histogram_success_with_sanitized_plots(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/",
            {"group": "metric", "metric": "runtime"},
        )
        request.session = {"username": "u"}
        thumb = MagicMock()
        full = MagicMock()
        valid_item = {
            "doc": {"roots": {"root_ids": ["h1"]}},
            "root_id": "h1",
        }
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
        ), patch.object(
            api,
            "_build_histogram_dataframe",
            return_value=(MagicMock(), [("runtime", "hours")], ["j1"]),
        ), patch.object(
            api, "_job_list_metric_hist_pair", return_value=(thumb, full)
        ), patch.object(api, "_sanitize_hist_plot_item", return_value=valid_item):
            response = api.job_list_histograms(request)
        assert response.status_code == 200
        assert response.data["plot_item_thumb"] == valid_item
        assert response.data["plot_unavailable_reason"] is None


class TestJobDetailView:
    def _mock_job(self):
        job = MagicMock()
        job.jid = "j1"
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)
        job.metrics_distinct_time_count = 10
        return job

    def test_404_when_job_not_found(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": True}
        not_found = api.Response({"error": "Job not found"}, status=404)
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(None, not_found)
        ):
            response = api.job_detail(request, "j1")
        assert response.status_code == 404

    def test_403_when_forbidden(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": False}
        forbidden = api.Response({"error": "Not allowed"}, status=403)
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(None, forbidden)
        ):
            response = api.job_detail(request, "j1")
        assert response.status_code == 403

    def test_non_light_mode_fetches_proc_list_via_executor(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": False}
        job = self._mock_job()
        jt = MagicMock()
        jt.acct_host_list = ["n1.example.com"]
        jt.start_time = job.start_time
        jt.end_time = job.end_time

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={"fsio": {}}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value=""), patch.object(
            api, "_collect_future_results_with_deadline",
            return_value=({"proc_list": [{"host": "n1", "proc": "proc-a", "threads": 1}]}, set()),
        ):
            mock_ser.return_value.data = {"jid": "j1"}
            response = api.job_detail(request, "j1")
        assert response.status_code == 200
        assert response.data["proc_list"] == [{"host": "n1", "proc": "proc-a", "threads": 1}]

    def test_light_mode_skips_heavy_fetches(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/", {"light": "1"})
        request.session = {"username": "u", "is_staff": False}
        job = self._mock_job()
        jt = MagicMock()
        jt.acct_host_list = ["n1.example.com"]
        jt.start_time = job.start_time
        jt.end_time = job.end_time
        ser_data = {"jid": "j1"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "load_job_detail_artifact", return_value={}
        ), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(
            api, "build_job_metrics_display_list", return_value=[]
        ), patch.object(api, "JobListSerializer") as mock_ser:
            mock_ser.return_value.data = ser_data
            response = api.job_detail(request, "j1")
        assert response.status_code == 200
        assert response.data["proc_list"] == []
        assert response.data["xalt_data"]["exec_path"] == []
        assert response.data["derived_data_status"] == "loading"


class TestJobPlotsView:
    def test_invalid_plot_param_returns_400(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/", {"plot": "bad"})
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ):
            response = api.job_plots(request, "j1")
        assert response.status_code == 400

    def test_404_when_job_missing(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/")
        request.session = {"username": "u", "is_staff": True}
        not_found = api.Response({"error": "Job not found"}, status=404)
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(None, not_found)
        ):
            response = api.job_plots(request, "j1")
        assert response.status_code == 404

    def test_cache_hit_returns_ready_single_plot(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        cached = {"plot_item": {"type": "object"}, "unavailable_reason": None}

        def _cache_get(key, default=None):
            if "JOB_PLOTS_JSON" in str(key):
                return cached
            return default

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api, "load_cached_job_plot_entry", return_value=None
        ):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert response.data["plot_item"] == cached["plot_item"]

    def test_progressive_partial_when_some_plots_loading(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/",
            {"progressive": "1"},
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        cached = {"plot_item": {"id": "p"}, "unavailable_reason": None}

        def _cache_get(key, default=None):
            key_s = str(key)
            if "JOB_PLOTS_JSON" in key_s and "summary_plot" in key_s:
                return cached
            return default

        inflight_future = MagicMock()
        inflight_future.done.return_value = False

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api, "load_cached_job_plot_entry", return_value=None
        ), patch.object(api, "_get_small_executor") as mock_exec, patch.object(
            api, "_job_plot_inflight",
            {
                ("j1", "roofline", "normal"): {
                    "future": inflight_future,
                    "created_at": 0.0,
                },
                ("j1", "gpu_roofline", "normal"): {
                    "future": inflight_future,
                    "created_at": 0.0,
                },
            },
        ):
            mock_exec.return_value.submit.return_value = inflight_future
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["status"] == "partial"
        assert "summary_plot" in str(response.data.get("mplot_item", "")) or response.data.get(
            "mplot_item"
        ) == cached["plot_item"]
        assert len(response.data["loading_plots"]) >= 1


class TestTypeDetailView:
    def test_returns_loading_when_artifact_missing(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/types/host_cpu/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(
            api, "load_job_detail_artifact", return_value=None
        ), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ):
            response = api.type_detail(request, "j1", "host_cpu")
        assert response.status_code == 200
        assert response.data["status"] == "loading"
        assert response.data["type_name"] == "host_cpu"

    def test_returns_ready_payload_from_artifact(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/types/host_cpu/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        artifact = {
            "type_name": "host_cpu",
            "jobid": "j1",
            "tplot_item": {"doc": {}},
            "stats_data": [1],
            "schema": [],
        }
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(
            api, "load_job_detail_artifact", return_value=artifact
        ), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ):
            response = api.type_detail(request, "j1", "host_cpu")
        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert response.data["stats_data"] == [1]


class TestJobPlotsReadyAndLoading:
    def test_all_plots_ready_returns_full_payload(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        cached = {"plot_item": {"x": 1}, "unavailable_reason": None}

        def _cache_get(key, default=None):
            if "JOB_PLOTS_JSON" in str(key):
                return cached
            return default

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=_cache_get
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["mplot_item"] == cached["plot_item"]
        assert response.data["rplot_item"] == cached["plot_item"]

    def test_non_progressive_loading_returns_202(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        pending = MagicMock()
        pending.done.return_value = False

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=lambda key, default=None: default
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api, "_get_small_executor"
        ) as mock_exec:
            mock_exec.return_value.submit.return_value = pending
            response = api.job_plots(request, "j1")
        assert response.status_code == 202
        assert response.data["status"] == "loading"


class TestJobListCountFailure:
    def test_count_exception_returns_404(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/")
        request.session = {"username": "u", "is_staff": False}
        mock_qs = MagicMock()
        mock_qs.count.side_effect = RuntimeError("db")
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api,
            "_build_job_list_queryset_from_request",
            return_value=(mock_qs, {}, None, "-end_time"),
        ):
            response = api.job_list(request)
        assert response.status_code == 404


class TestAdminMonitorView:
    def test_requires_staff(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/admin_monitor/")
        denied = api.Response({"error": "no"}, status=403)
        with patch.object(api, "_require_staff", return_value=denied):
            response = api.admin_monitor(request)
        assert response.status_code == 403

    def test_section_cache_returns_stats_only(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "cache"}
        )
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "_get_cache_stats", return_value={"hits": 1}
        ):
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert response.data == {"cache_stats": {"hits": 1}}

    def test_section_hosts_uses_cached_orm(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "hosts"}
        )
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "cached_orm", return_value=[{"host": "n1.example.com"}]
        ):
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert response.data["host_stats"][0]["host"] == "n1.example.com"

    def test_section_timescaledb_and_rabbitmq(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "timescaledb"}
        )
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "_get_timescaledb_stats", return_value={"database_name": "db"}
        ):
            response = api.admin_monitor(request)
        assert response.data["timescaledb_stats"]["database_name"] == "db"

        request2 = RequestFactory().get(
            "/api/admin_monitor/", {"section": "rabbitmq"}
        )
        request2.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "_get_rabbitmq_stats", return_value={"ok": True}
        ):
            response2 = api.admin_monitor(request2)
        assert response2.data["rabbitmq_stats"]["ok"] is True

    def test_section_telemetry_health(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "telemetry_health"}
        )
        request.session = {"is_staff": True}
        payload = {
            "window_hours": 12,
            "timed_out": False,
            "all_zero_events": [],
            "missing_core_types": ["host_cpu"],
        }
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "compute_telemetry_health", return_value=payload
        ):
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert response.data == {"telemetry_health": payload}

    def test_full_payload_without_section(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/admin_monitor/")
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "cached_orm", return_value=[]
        ), patch.object(
            api, "_get_recent_rabbitmq_host_stats", return_value=[]
        ), patch.object(api, "_get_cache_stats", return_value={}), patch.object(
            api, "_get_rabbitmq_stats", return_value={}
        ), patch.object(api, "_get_timescaledb_stats", return_value={}), patch.object(
            api, "_get_xalt_jid_coverage", return_value={}
        ), patch.object(api, "compute_telemetry_health", return_value={}):
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert "host_stats" in response.data
        assert "xalt_stats" in response.data
        assert "telemetry_health" in response.data

    def test_hosts_section_survives_host_data_query_failure(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/admin_monitor/", {"section": "hosts"}
        )
        request.session = {"is_staff": True}

        def _cached_orm(_key, _ttl, fn):
            return fn()

        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "cached_orm", side_effect=_cached_orm
        ), patch.object(
            api, "_list_recent_host_fqdns_from_redis", return_value=["n1.example.com"]
        ), patch.object(
            api,
            "latest_sample_time_by_host",
            side_effect=RuntimeError("db timeout"),
        ):
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert response.data["host_stats"] == []


def _submit_immediate_future(fn):
    from concurrent.futures import Future

    fut = Future()
    try:
        fut.set_result(fn())
    except Exception as exc:
        fut.set_exception(exc)
    return fut


def _cache_set_fail_job_plots_only(key, val, timeout=None):
    if "JOB_PLOTS" in str(key):
        raise RuntimeError("cache write fail")
    return None


def _plots_cache_get_factory(hit_keys=(), _l2_keys=(), data_keys=()):
    """Build cache.get side_effect that preserves DRF throttle defaults."""

    def _cache_get(key, default=None):
        key_s = str(key)
        if "throttle" in key_s.lower():
            return default
        for token in hit_keys:
            if token in key_s and "JOB_PLOTS_JSON" in key_s:
                return {"plot_item": {"cached": token}, "unavailable_reason": None}
        for token in data_keys:
            if token in key_s and "JOB_PLOTS_DATA" in key_s:
                return {"zoom_base": token, "doc": {"roots": {"root_ids": ["z"]}}}
        return default

    return _cache_get


class TestJobPlotsL2FinalizeAndZoom:
    def test_l2_hydrate_promotes_to_l1_cache(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        l2_item = {"doc": {"roots": {"root_ids": ["l2"]}}, "root_id": "l2"}
        l2_entry = {"plot_item": l2_item, "unavailable_reason": None}

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=l2_entry), patch.object(
            api.cache, "set"
        ) as mock_set, patch.object(api, "register_job_plot_cache_key"):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["plot_item"] == l2_item
        assert mock_set.call_count >= 1

    def test_l2_null_plot_item_is_terminal_ready(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "roofline"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        reason = "Missing roofline counters in host_data"
        l2_entry = {"plot_item": None, "unavailable_reason": reason}

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=l2_entry), patch.object(
            api, "_get_small_executor"
        ) as mock_exec:
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert response.data["plot_item"] is None
        assert response.data["unavailable_reason"] == reason
        mock_exec.assert_not_called()

    def test_generic_l1_unavailable_reason_is_terminal(self):
        from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        stale = {"plot_item": None, "unavailable_reason": MSG_NO_METRIC_DATA}

        def _cache_get(key, default=None):
            key_s = str(key)
            if "summary_plot" in key_s and "JOB_PLOTS_JSON" in key_s:
                return stale
            return default

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api, "load_cached_job_plot_entry", return_value=None
        ), patch.object(api, "_get_small_executor") as mock_exec:
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert response.data["unavailable_reason"] == MSG_NO_METRIC_DATA
        mock_exec.assert_not_called()

    def test_zoom_mode_reuses_cached_plot_data(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "roofline", "zoom": "1"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        zoomed = {"zoomed": True}

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get",
            side_effect=_plots_cache_get_factory(data_keys=("roofline",)),
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api, "_apply_zoom_layout_to_json_item", return_value=zoomed
        ):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["plot_item"] == zoomed


class TestJobDetailXaltFetch:
    def _job(self):
        job = MagicMock()
        job.jid = "jid-xalt"
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)
        job.metrics_distinct_time_count = 4
        return job

    def _xalt_model_mocks(self):
        run_row = MagicMock(run_id=1, exec_path="/opt/app/bin/foo", cwd="/work/dir")
        join_row = MagicMock(run_id=1, obj_id=99)
        lib_row = MagicMock(obj_id=99, object_path="/lib/libfoo.so", module_name="foo")

        def _slice_qs(rows):
            only_qs = MagicMock()
            only_qs.__getitem__.return_value = rows
            ordered = MagicMock()
            ordered.only.return_value = only_qs
            filtered = MagicMock()
            filtered.order_by.return_value = ordered
            mgr = MagicMock()
            mgr.filter.return_value = filtered
            return mgr

        run_mgr = _slice_qs([run_row])
        join_mgr = _slice_qs([join_row])
        lib_mgr = MagicMock()
        lib_mgr.filter.return_value.only.return_value = [lib_row]
        return run_mgr, join_mgr, lib_mgr

    def test_fetch_xalt_assembles_exec_paths_and_libset(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/jid-xalt/")
        request.session = {"username": "u", "is_staff": True}
        job = self._job()
        jt = MagicMock()
        jt.acct_host_list = ["n1.example.com"]
        jt.start_time = job.start_time
        jt.end_time = job.end_time
        run_mgr, join_mgr, lib_mgr = self._xalt_model_mocks()

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        def _cached_orm(key, _ttl, fn):
            if "XALT" in str(key):
                return fn()
            if "PROC_LIST" in str(key):
                return []
            return fn()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(api, "cached_orm", side_effect=_cached_orm), patch.object(
            api.run.objects, "using", return_value=run_mgr
        ), patch.object(
            api.join_run_object.objects, "using", return_value=join_mgr
        ), patch.object(api.lib.objects, "using", return_value=lib_mgr):
            mock_ser.return_value.data = {"jid": "jid-xalt"}
            response = api.job_detail(request, "jid-xalt")

        assert response.status_code == 200
        assert "/opt/app/bin/foo" in response.data["xalt_data"]["exec_path"]
        assert response.data["xalt_data"]["libset"] == [("/lib/libfoo.so", "foo")]

    def test_fetch_xalt_skips_usr_exec_paths(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/jid-xalt/")
        request.session = {"username": "u", "is_staff": True}
        job = self._job()
        jt = MagicMock()
        jt.acct_host_list = []
        jt.start_time = job.start_time
        jt.end_time = job.end_time
        run_row = MagicMock(run_id=2, exec_path="/home/usr/local/bin", cwd="/tmp")
        only_qs = MagicMock()
        only_qs.__getitem__.return_value = [run_row]
        ordered = MagicMock()
        ordered.only.return_value = only_qs
        filtered = MagicMock()
        filtered.order_by.return_value = ordered
        run_mgr = MagicMock()
        run_mgr.filter.return_value = filtered

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ), patch.object(api.run.objects, "using", return_value=run_mgr), patch.object(
            api.join_run_object.objects, "using", return_value=MagicMock()
        ), patch.object(api.lib.objects, "using", return_value=MagicMock()):
            mock_ser.return_value.data = {"jid": "jid-xalt"}
            response = api.job_detail(request, "jid-xalt")
        assert response.data["xalt_data"]["exec_path"] == []


class TestJobMonitorAggregates:
    def _stats_chain(self, rows):
        ordered = MagicMock()
        ordered.__iter__ = lambda self: iter(rows)
        annotated2 = MagicMock()
        annotated2.order_by.return_value = ordered
        filtered = MagicMock()
        filtered.annotate.return_value = annotated2
        annotated1 = MagicMock()
        annotated1.filter.return_value = filtered
        values_qs = MagicMock()
        values_qs.annotate.return_value = annotated1
        base_qs = MagicMock()
        base_qs.values.return_value = values_qs
        return base_qs

    def test_invalid_days_defaults_and_returns_rows(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/job_monitor/", {"days": "bad"})
        request.session = {"is_staff": True}
        row = {
            "username": "alice",
            "total_jobs": 20,
            "failed_jobs": 4,
            "timedout_jobs": 2,
            "failed_rate": 20.0,
            "timedout_rate": 10.0,
        }
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api.job_data.objects, "filter", return_value=self._stats_chain([row])
        ):
            response = api.job_monitor(request)
        assert response.status_code == 200
        assert response.data["window_days"] == 30
        assert response.data["results"][0]["failed_rate"] == 20.0
        assert response.data["results"][0]["timedout_jobs"] == 2

    def test_clamps_days_to_365(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/job_monitor/", {"days": "9999"})
        request.session = {"is_staff": True}
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api.job_data.objects, "filter", return_value=self._stats_chain([])
        ):
            response = api.job_monitor(request)
        assert response.data["window_days"] == 365


class TestJobMonitorGpuFallbackBranches:
    def test_empty_metrics_does_not_call_host_data_gpu_helpers(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/job_monitor/gpu/", {"username": "bob", "days": "7"}
        )
        request.session = {"is_staff": True}

        class _MdChain:
            def filter(self, **_kwargs):
                return self

            def exists(self):
                return False

            def aggregate(self, **_kwargs):
                return {"s": None}

        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ), patch.object(api.metrics_data.objects, "filter", return_value=_MdChain()), patch.object(
            api, "_compute_job_gpu_stats"
        ) as mock_compute:
            response = api.job_monitor_gpu_for_user(request)
        assert response.status_code == 200
        assert response.data["has_data"] is False
        assert response.data["gpu_active_total"] is None
        mock_compute.assert_not_called()

    def test_empty_metrics_reports_no_data(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/job_monitor/gpu/", {"username": "bob"}
        )
        request.session = {"is_staff": True}

        class _MdChain:
            def filter(self, **_kwargs):
                return self

            def exists(self):
                return False

            def aggregate(self, **_kwargs):
                return {"s": None}

        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(
            api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
        ), patch.object(api.metrics_data.objects, "filter", return_value=_MdChain()):
            response = api.job_monitor_gpu_for_user(request)
        assert response.status_code == 200
        assert response.data["has_data"] is False


class TestJobPlotsFetchErrors:
    def test_artifact_miss_returns_loading_without_executor(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api, "_get_small_executor"
        ) as mock_exec:
            response = api.job_plots(request, "j1")
        assert response.status_code == 202
        assert response.data["status"] == "loading"
        mock_exec.assert_not_called()

    def test_l2_cache_set_failure_still_returns_ready(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "gpu_roofline"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        plot_payload = {"doc": {"roots": {"root_ids": ["g1"]}}, "root_id": "g1"}
        l2_entry = {"plot_item": plot_payload, "unavailable_reason": None}

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=l2_entry), patch.object(
            api.cache, "set", side_effect=_cache_set_fail_job_plots_only
        ), patch.object(api, "register_job_plot_cache_key"):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["plot_item"] == plot_payload


class TestHostPlotBuildCallback:
    def test_plot_builder_exception_yields_none_item(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/host_plot/",
            {
                "host": "n1.example.com",
                "end_time__gte": "2026-08-01T12:00:00Z",
                "end_time__lte": "2026-08-01T13:00:00Z",
            },
        )
        request.session = {"is_staff": True}

        def _cached_orm(_key, _ttl, fn):
            return fn()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", side_effect=_cached_orm), patch.object(
            api, "HostDataProvider", side_effect=RuntimeError("no data")
        ):
            response = api.host_plot(request)
        assert response.status_code == 200
        assert response.data["plot_item"] is None
        assert "no host plot data" in response.data["plot_unavailable_reason"].lower()

    def test_invalid_end_time_falls_back_to_now(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/host_plot/",
            {
                "host": "n1.example.com",
                "end_time__gte": "2026-08-04T12:00:00Z",
                "end_time__lte": "not-a-date",
            },
        )
        request.session = {"is_staff": True}
        fake_item = {"doc": {"roots": {"root_ids": ["p"]}}, "root_id": "p"}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", return_value=fake_item):
            response = api.host_plot(request)
        assert response.status_code == 200
        assert response.data["plot_item"] == fake_item


class TestJobPlotsCoverageClosure:
    def test_artifact_miss_does_not_use_live_compute(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/", {"plot": "roofline"})
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api, "_get_small_executor"
        ) as mock_exec:
            response = api.job_plots(request, "j1")
        assert response.status_code == 202
        assert response.data["status"] == "loading"
        mock_exec.assert_not_called()

    def test_l2_hydrate_json_and_data_cache_set_failures(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "summary_plot"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        l2_item = {"doc": {"roots": {"root_ids": ["l2"]}}, "root_id": "l2"}
        l2_entry = {"plot_item": l2_item, "unavailable_reason": None}

        def _set_fail(key, val, timeout=None):
            key_s = str(key)
            if "JOB_PLOTS_JSON" in key_s or "JOB_PLOTS_DATA" in key_s:
                raise RuntimeError("cache write fail")
            return None

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache, "get", side_effect=_plots_cache_get_factory()
        ), patch.object(api, "load_cached_job_plot_entry", return_value=l2_entry), patch.object(
            api.cache, "set", side_effect=_set_fail
        ), patch.object(api, "register_job_plot_cache_key"):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["plot_item"] == l2_item

    def test_zoom_reuses_plot_data_when_missing_l1(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/j1/plots/", {"plot": "roofline", "zoom": "1"}
        )
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        zoomed = {"zoomed": True}
        api._job_plot_inflight.clear()

        class _Exec:
            def submit(self, fn):
                fut = Future()
                fut.set_result((None, "loading"))
                return fut

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "compute_plot_input_fingerprint", return_value="fp"), patch.object(
            api.cache,
            "get",
            side_effect=_plots_cache_get_factory(data_keys=("roofline",)),
        ), patch.object(api, "load_cached_job_plot_entry", return_value=None), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(api, "_apply_zoom_layout_to_json_item", return_value=zoomed):
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data["plot_item"] == zoomed

    def test_progressive_all_plots_ready_payload(self):
        from types import SimpleNamespace

        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/", {"progressive": "1"})
        request.session = {"username": "u", "is_staff": True}
        fake_job = SimpleNamespace(jid="j1")

        def _cache_get(key, default=None):
            if "JOB_PLOTS_JSON" in str(key):
                return {"plot_item": {"ok": True}, "unavailable_reason": None}
            return default

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(fake_job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "compute_plot_input_fingerprint", return_value="fp"
        ), patch.object(api.cache, "get", side_effect=_cache_get), patch.object(
            api, "load_cached_job_plot_entry", return_value=None
        ), patch.object(api, "_get_small_executor") as mock_exec:
            response = api.job_plots(request, "j1")
        assert response.status_code == 200
        assert response.data.get("status") == "ready"
        assert response.data.get("progressive") is True
        assert response.data.get("loading_plots") == []
        mock_exec.assert_not_called()

    def test_auth_error_returns_early(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/plots/")
        denied = api.Response({"error": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.job_plots(request, "j1")
        assert response.status_code == 401


class TestJobDetailCoverageClosure:
    def test_light_mode_skips_parallel_tasks(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/", {"light": "1"})
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)
        jt = MagicMock()
        jt.acct_host_list = ["n1.example.com", "n2.example.com"]
        jt.start_time = job.start_time
        jt.end_time = job.end_time

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api.cfg, "get_host_name_ext", return_value=".cluster"
        ):
            mock_ser.return_value.data = {"jid": "j1"}
            response = api.job_detail(request, "j1")
        assert response.status_code == 200
        assert response.data["xalt_data"]["exec_path"] == []
        assert "OR" in response.data["client_url"]

    def test_job_detail_client_url_inserts_dot_before_host_name_ext(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j-stampede/")
        request.session = {"username": "u", "is_staff": False}
        job = MagicMock(jid="j-stampede")
        job.host_list = ["c551-002"]
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(
            api, "load_job_detail_artifact", return_value={}
        ), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(
            api, "_job_for_detail_list_serializer", return_value=job
        ), patch.object(api.cfg, "get_xalt_user", return_value=""), patch.object(
            api.cfg, "get_host_name_ext", return_value="stampede3.tacc.utexas.edu"
        ):
            mock_ser.return_value.data = {"jid": "j-stampede"}
            response = api.job_detail(request, "j-stampede")

        assert response.status_code == 200
        client_url = response.data["client_url"]
        assert "host%3Dc551-002.stampede3.tacc.utexas.edu" in client_url
        assert "c551-002stampede3" not in client_url

    def test_xalt_missing_lib_and_duplicate_module_deduped(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/jid-xalt/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock()
        job.jid = "jid-xalt"
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)
        job.metrics_distinct_time_count = 1
        run_row = MagicMock(run_id=1, exec_path="/opt/bin/app", cwd="/work")
        join_missing = MagicMock(run_id=1, obj_id=100)
        join_dup = MagicMock(run_id=1, obj_id=101)
        join_dup2 = MagicMock(run_id=1, obj_id=101)
        lib_a = MagicMock(obj_id=101, object_path="/lib/a.so", module_name="foo")

        def _slice_qs(rows):
            only_qs = MagicMock()
            only_qs.__getitem__.return_value = rows
            ordered = MagicMock()
            ordered.only.return_value = only_qs
            filtered = MagicMock()
            filtered.order_by.return_value = ordered
            mgr = MagicMock()
            mgr.filter.return_value = filtered
            return mgr

        run_mgr = _slice_qs([run_row])
        join_mgr = _slice_qs([join_missing, join_dup, join_dup2])
        lib_mgr = MagicMock()
        lib_mgr.filter.return_value.only.return_value = [lib_a]

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        def _cached_orm(key, _ttl, fn):
            if "XALT" in str(key):
                return fn()
            if "PROC_LIST" in str(key):
                return []
            return fn()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(api, "cached_orm", side_effect=_cached_orm), patch.object(
            api.run.objects, "using", return_value=run_mgr
        ), patch.object(
            api.join_run_object.objects, "using", return_value=join_mgr
        ), patch.object(api.lib.objects, "using", return_value=lib_mgr), patch.object(
            api.cfg, "get_host_name_ext", return_value=""
        ):
            mock_ser.return_value.data = {"jid": "jid-xalt"}
            response = api.job_detail(request, "jid-xalt")
        assert response.status_code == 200
        assert len(response.data["xalt_data"]["libset"]) == 1

    def test_xalt_module_name_none_becomes_none_label(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/jid-xalt-none/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock()
        job.jid = "jid-xalt-none"
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)
        job.metrics_distinct_time_count = 1
        run_row = MagicMock(run_id=1, exec_path="/opt/bin/app", cwd="/work")
        join_row = MagicMock(run_id=1, obj_id=101)
        lib_row = MagicMock(obj_id=101, object_path="/lib/n.so", module_name=None)

        def _slice_qs(rows):
            only_qs = MagicMock()
            only_qs.__getitem__.return_value = rows
            ordered = MagicMock()
            ordered.only.return_value = only_qs
            filtered = MagicMock()
            filtered.order_by.return_value = ordered
            mgr = MagicMock()
            mgr.filter.return_value = filtered
            return mgr

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        def _cached_orm(key, _ttl, fn):
            if "XALT" in str(key):
                return fn()
            if "PROC_LIST" in str(key):
                return []
            return fn()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(api, "cached_orm", side_effect=_cached_orm), patch.object(
            api.run.objects, "using", return_value=_slice_qs([run_row])
        ), patch.object(
            api.join_run_object.objects, "using", return_value=_slice_qs([join_row])
        ), patch.object(
            api.lib.objects, "using", return_value=MagicMock(
                filter=MagicMock(return_value=MagicMock(only=MagicMock(return_value=[lib_row])))
            )
        ), patch.object(api.cfg, "get_host_name_ext", return_value=""):
            mock_ser.return_value.data = {"jid": "jid-xalt-none"}
            response = api.job_detail(request, "jid-xalt-none")
        assert response.status_code == 200
        assert response.data["xalt_data"]["libset"] == [("/lib/n.so", "none")]

    def test_proc_list_result_bool_raises_swallowed(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value=""), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(
            api, "_collect_future_results_with_deadline",
            return_value=({"proc_list": _BadBool()}, set()),
        ), patch.object(api.cfg, "get_host_name_ext", return_value=""):
            mock_ser.return_value.data = {"jid": "j1"}
            response = api.job_detail(request, "j1")
        assert response.status_code == 200
        assert response.data["proc_list"] == []


class _BadBool:
    def __bool__(self):
        raise RuntimeError("bad bool")


class TestJobListCoverageClosure:
    def test_auth_error(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/")
        denied = api.Response({"error": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.job_list(request)
        assert response.status_code == 401

    def test_staff_queue_wait_aggregate_failure_and_pagination(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/", {"page": "bad"})
        request.session = {"username": "u", "is_staff": True}
        chain = MagicMock()
        chain.count.return_value = 5
        chain.aggregate.return_value = {"total_node_hours": 10.0}
        page = MagicMock()
        page.object_list = [MagicMock()]
        page.number = 1
        page.has_previous.return_value = False
        page.has_next.return_value = False

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_job_list_queryset_from_request",
            return_value=(chain, {}, {}, "-end_time"),
        ), patch.object(
            api, "build_job_list_qname_and_filter_summary", return_value=("q", "f")
        ), patch.object(
            api, "aggregate_queue_wait_seconds_stats", side_effect=RuntimeError("wait")
        ), patch.object(api, "Paginator") as mock_pag, patch.object(
            api, "JobListSerializer"
        ) as mock_ser:
            pag = MagicMock()
            last_page = MagicMock()
            last_page.object_list = []
            last_page.number = 1
            last_page.has_previous.return_value = False
            last_page.has_next.return_value = False
            pag.page.side_effect = [api.PageNotAnInteger(), page]
            pag.num_pages = 1
            mock_pag.return_value = pag
            mock_ser.return_value.data = []
            response = api.job_list(request)
        assert response.status_code == 200
        assert "queue_wait_mean_hours" not in response.data["aggregates"]

    def test_empty_page_uses_last_page(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/", {"page": "999"})
        request.session = {"username": "u", "is_staff": False}
        chain = MagicMock()
        chain.count.return_value = 3
        chain.aggregate.return_value = {"total_node_hours": 1.0}
        last_page = MagicMock()
        last_page.object_list = []
        last_page.number = 1
        last_page.has_previous.return_value = False
        last_page.has_next.return_value = False

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_job_list_queryset_from_request",
            return_value=(chain, {}, {}, "-end_time"),
        ), patch.object(
            api, "build_job_list_qname_and_filter_summary", return_value=("q", "f")
        ), patch.object(api, "Paginator") as mock_pag, patch.object(
            api, "JobListSerializer"
        ) as mock_ser:
            pag = MagicMock()
            pag.page.side_effect = [api.EmptyPage(), last_page]
            pag.num_pages = 1
            mock_pag.return_value = pag
            mock_ser.return_value.data = []
            response = api.job_list(request)
        assert response.status_code == 200
        assert response.data["pagination"]["page"] == 1


class TestJobListHistogramsCoverageClosure:
    def test_auth_error(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/")
        denied = api.Response({"error": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.job_list_histograms(request)
        assert response.status_code == 401

    def test_unknown_group_with_jobs_returns_400(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/", {"group": "unknown"}
        )
        request.session = {"username": "u", "is_staff": True}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400


class TestTypeDetailAndHostPlotClosure:
    def test_type_detail_auth_and_not_found(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/types/cpu/")
        denied = api.Response({"error": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.type_detail(request, "j1", "cpu")
        assert response.status_code == 401

        request.session = {"username": "u", "is_staff": True}
        err = api.Response({"error": "missing"}, status=404)
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(None, err)
        ):
            response = api.type_detail(request, "j1", "cpu")
        assert response.status_code == 404

    def test_host_plot_allows_non_staff_and_bad_start_time(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/host_plot/",
            {"host": "n1.example.com", "end_time__gte": "not-a-date"},
        )
        request.session = {"username": "u", "is_staff": False}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", return_value=None) as mock_cached:
            response = api.host_plot(request)
        assert response.status_code == 200
        assert response.data["plot_item"] is None
        mock_cached.assert_called_once()

    def test_host_plot_naive_datetimes_aware(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/host_plot/",
            {
                "host": "n1.example.com",
                "end_time__gte": "2026-08-01T12:00:00",
                "end_time__lte": "2026-08-02T12:00:00",
            },
        )
        request.session = {"is_staff": True}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", return_value={"ok": True}):
            response = api.host_plot(request)
        assert response.status_code == 200


class TestJobDetailRemainingKeysClosure:
    def test_job_detail_auth_error(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        denied = api.Response({"error": "no"}, status=401)
        with patch.object(api, "_require_auth", return_value=denied):
            response = api.job_detail(request, "j1")
        assert response.status_code == 401

    def test_job_detail_logs_pending_keys_on_timeout(self, caplog):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/j1/")
        request.session = {"username": "u", "is_staff": True}
        job = MagicMock(jid="j1")
        job.start_time = datetime(2024, 1, 1, tzinfo=dt_timezone.utc)
        job.end_time = datetime(2024, 1, 2, tzinfo=dt_timezone.utc)

        class _Exec:
            def submit(self, fn):
                return _submit_immediate_future(fn)

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_get_visible_job_or_error_response", return_value=(job, None)
        ), patch.object(api, "get_site_content_cache_timeout", return_value=60), patch.object(api, "load_job_detail_artifact", return_value={}), patch.object(
            api, "compute_detail_input_fingerprint", return_value="fp"
        ), patch.object(api, "build_job_metrics_display_list", return_value=[]), patch.object(
            api, "JobListSerializer"
        ) as mock_ser, patch.object(api.cfg, "get_xalt_user", return_value="xuser"), patch.object(
            api, "_get_small_executor", return_value=_Exec()
        ), patch.object(
            api, "_collect_future_results_with_deadline",
            return_value=({}, {"xalt"}),
        ), patch.object(api.cfg, "get_host_name_ext", return_value=""):
            mock_ser.return_value.data = {"jid": "j1"}
            response = api.job_detail(request, "j1")
        assert response.status_code == 200
        assert any("max wait exceeded" in r.message for r in caplog.records)


class TestHostPlotJsonItemClosure:
    def test_host_plot_builder_returns_none_on_exception(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/host_plot/",
            {
                "host": "n1.example.com",
                "end_time__gte": "2026-08-01T12:00:00Z",
                "end_time__lte": "2026-08-02T12:00:00Z",
            },
        )
        request.session = {"is_staff": True}

        def _cached_orm(_key, _ttl, fn):
            return fn()

        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "get_site_content_cache_timeout", return_value=60
        ), patch.object(api, "cached_orm", side_effect=_cached_orm), patch.object(
            api, "HostDataProvider", return_value=MagicMock()
        ), patch.object(api.plots, "SummaryPlot", return_value=MagicMock(plot=MagicMock(return_value=MagicMock()))), patch.object(
            api, "json_item", side_effect=RuntimeError("serialize fail")
        ):
            response = api.host_plot(request)
        assert response.status_code == 200
        assert response.data["plot_item"] is None


class TestAdminMonitorRefreshAllSectionsClosure:
    def test_refresh_without_section_clears_all(self):
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
            api, "compute_telemetry_health", return_value={}
        ), patch.object(
            api.cache, "delete"
        ) as mock_delete:
            response = api.admin_monitor(request)
        assert response.status_code == 200
        assert mock_delete.call_count >= 6


class TestJobListHistogramMetricMissingClosure:
    def test_metric_histogram_missing_metric_param(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get(
            "/api/jobs/histograms/", {"group": "metric"}
        )
        request.session = {"username": "u", "is_staff": True}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 2, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400


class TestJobListHistogramUnknownGroupClosure:
    def test_unknown_group_with_no_jobs_returns_400(self):
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().get("/api/jobs/histograms/", {"group": "bogus"})
        request.session = {"username": "u", "is_staff": True}
        with patch.object(api, "_require_auth", return_value=None), patch.object(
            api, "_build_histogram_queryset", return_value=(MagicMock(), 0, {}, {})
        ):
            response = api.job_list_histograms(request)
        assert response.status_code == 400
        assert "Unknown group" in response.data["error"]


class TestApiKeyRotateClosure:
    def test_csrf_missing_returns_403(self):
        from hpcperfstats.site.lib.machine import api
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.post("/api/user/api-key/rotate/")
        request.session = {"username": "u"}
        with patch.object(api, "check_for_tokens", return_value=True):
            response = api.user_api_key_rotate(request)
        assert response.status_code == 403

    def test_auth_required_returns_401(self):
        from hpcperfstats.site.lib.machine import api
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.post(
            "/api/user/api-key/rotate/",
            HTTP_X_CSRFTOKEN="tok",
        )
        request.session = {"username": "u"}
        with patch.object(api, "check_for_tokens", return_value=False):
            response = api.user_api_key_rotate(request)
        assert response.status_code == 401


class TestDropStaffSessionModifiedClosure:
    def test_sets_session_modified_when_present(self):
        from django.contrib.sessions.backends.base import SessionBase
        from hpcperfstats.site.lib.machine import api

        request = RequestFactory().post("/api/drop-staff/", **csrf_headers())
        session = SessionBase()
        session["is_staff"] = True
        request.session = session
        with patch.object(api, "_require_staff", return_value=None):
            response = api.drop_staff_for_session(request)
        assert response.status_code == 200
        assert request.session.modified is True


class TestInvalidateCachePageClosure:
    def test_legacy_scan_delete_exception_continues(self):
        from hpcperfstats.site.lib.machine import api
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.post(
            "/api/cache/invalidate-page/",
            {"page_path": "/machine/jobs/"},
            format="json",
            HTTP_X_CSRFTOKEN="test-csrf-token",
        )
        request.session = {"is_staff": True}

        class _Client:
            def scan_iter(self, count=500):
                for i in range(5002):
                    yield f"/machine/jobs/key{i}"

            def delete(self, raw_key):
                raise RuntimeError("del fail")

        backend = MagicMock()
        backend.get_client.return_value = _Client()
        fake_cache = MagicMock()
        fake_cache._cache = backend
        with patch.object(api, "_require_staff", return_value=None), patch.object(
            api, "cache", fake_cache
        ), patch.object(api, "_get_redis_cache_client", return_value=_Client()), patch.object(
            api, "_delete_django_cache_page_entries_for_request", return_value=0
        ), patch.object(
            api, "_redis_delete_cache_page_keys_matching_digests", return_value=0
        ), patch.object(api, "_full_page_cache_url_digests_for_request_paths", return_value=set()):
            response = api.invalidate_cache_for_page(request)
        assert response.status_code == 200
