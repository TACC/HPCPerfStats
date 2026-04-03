"""Additional API unit tests for small helpers in hpcperfstats.site.machine.api.

Covers:
- session_info: authenticated vs unauthenticated behavior and session fields.
- home_options: aggregation of dates/metrics/queues/states without hitting a real DB.
- search_dispatch: jid and host branches, including 404 when jid not found.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory
from hpcperfstats.site.machine.models import ApiKey


class TestAdminMonitorRefresh:
  def test_admin_monitor_refresh_clears_cache_for_selected_section(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"section": "cache", "refresh": "1"})
    request.session = {"is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api._get_cache_stats",
        return_value={"ok": True},
    ), patch("hpcperfstats.site.machine.api.cache") as mock_cache:
      response = api.admin_monitor(request)

    assert response.status_code == 200
    assert response.data == {"cache_stats": {"ok": True}}
    mock_cache.delete.assert_called_with(api.KEY_ADMIN_CACHE_STATS)

  def test_admin_monitor_refresh_clears_cache_for_xalt_section(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"section": "xalt", "refresh": "1"})
    request.session = {"is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.machine.api._get_xalt_jid_coverage",
      return_value={"ok": True},
    ), patch("hpcperfstats.site.machine.api.cache") as mock_cache:
      response = api.admin_monitor(request)

    assert response.status_code == 200
    assert response.data == {"xalt_stats": {"ok": True}}
    mock_cache.delete.assert_called_with(api.KEY_ADMIN_XALT_STATS)


@pytest.mark.django_db
class TestSessionInfo:
  """Tests for the session_info endpoint."""

  def test_session_info_requires_auth(self):
    """session_info returns auth error when _require_auth fails."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/session-info/")

    with patch("hpcperfstats.site.machine.api._require_auth") as mock_auth:
      mock_auth.return_value = api.Response(
          {"detail": "unauthorized"}, status=401
      )
      response = api.session_info(request)

    assert response.status_code == 401

  def test_session_info_returns_session_fields(self):
    """session_info returns logged_in=True and session username/is_staff flags."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/session-info/")
    request.session = {"username": "alice", "is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api.cfg.get_host_name_ext",
        return_value="test-machine",
    ):
      response = api.session_info(request)

    assert response.status_code == 200
    data = response.data
    assert data["logged_in"] is True
    assert data["username"] == "alice"
    assert data["is_staff"] is True
    assert data["machine_name"] == "test-machine"


@pytest.mark.django_db
class TestDropStaffForSession:
  """Tests for the drop_staff_for_session endpoint."""

  def test_drop_staff_for_session_requires_auth(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post("/api/session/drop-staff/")

    with patch("hpcperfstats.site.machine.api._require_auth") as mock_auth:
      mock_auth.return_value = api.Response({"detail": "unauthorized"}, status=401)
      response = api.drop_staff_for_session(request)

    assert response.status_code == 401

  def test_drop_staff_for_session_requires_current_staff(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post("/api/session/drop-staff/")
    request.session = {"username": "alice", "is_staff": False}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None):
      response = api.drop_staff_for_session(request)

    assert response.status_code == 403
    assert response.data["error"] == "Staff access required"

  def test_drop_staff_for_session_removes_staff_flag_and_returns_notice(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post("/api/session/drop-staff/")
    request.session = {"username": "alice", "is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None):
      response = api.drop_staff_for_session(request)

    assert response.status_code == 200
    assert request.session["is_staff"] is False
    assert response.data["is_staff"] is False
    assert "Log out and log back in" in response.data["message"]


@pytest.mark.django_db
class TestInvalidateCacheForPage:
  """Tests for the invalidate_cache_for_page endpoint."""

  def test_invalidate_cache_for_page_requires_staff(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "/machine/jobs"},
        content_type="application/json",
    )
    request.session = {"username": "alice", "is_staff": False}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None):
      response = api.invalidate_cache_for_page(request)

    assert response.status_code == 403
    assert response.data["error"] == "Staff access required"

  def test_invalidate_cache_for_page_deletes_matching_keys(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "/machine/jobs"},
        content_type="application/json",
    )
    request.session = {"username": "alice", "is_staff": True}

    mock_client = MagicMock()
    mock_client.scan_iter.return_value = iter(
        [
            b"views.decorators.cache.cache_page.prefix.get.deadbeef.hash",
            b"custom:/machine/jobs:cache_key",
            b"custom:/machine/admin_monitor:cache_key",
        ]
    )

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api._get_redis_cache_client",
        return_value=mock_client,
    ), patch("hpcperfstats.site.machine.api.hashlib.md5") as mock_md5:
      hash_obj = MagicMock()
      hash_obj.hexdigest.return_value = "deadbeef"
      mock_md5.return_value = hash_obj
      response = api.invalidate_cache_for_page(request)

    assert response.status_code == 200
    assert response.data["ok"] is True
    assert response.data["deleted_keys"] == 2
    assert response.data["page_path"] == "/machine/jobs"
    deleted_raw_keys = [call.args[0] for call in mock_client.delete.call_args_list]
    assert b"views.decorators.cache.cache_page.prefix.get.deadbeef.hash" in deleted_raw_keys
    assert b"custom:/machine/jobs:cache_key" in deleted_raw_keys
    assert b"custom:/machine/admin_monitor:cache_key" not in deleted_raw_keys


@pytest.mark.django_db
class TestHomeOptions:
  """Tests for the home_options endpoint."""

  def test_home_options_aggregates_date_lists_and_filters_empty_values(self):
    """home_options builds year_list, date_list, metrics, queues, and states from cached data."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/home-options/")

    sample_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2023, 12, 31)]
    sample_metrics = [{"metric": "runtime", "units": "hours"}]
    sample_queues = ["normal", "", None]
    sample_states = ["RUNNING", "", None]

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api._get_small_executor"
    ) as mock_exec, patch(
        "hpcperfstats.site.machine.api.cfg.get_host_name_ext",
        return_value="test-machine",
    ):
      # Fake executor that just runs the function synchronously
      class _FakeFuture:
        def __init__(self, value):
          self._value = value

        def result(self):
          return self._value

      def _submit(fn, *args, **kwargs):
        # fn is cached_orm; args: key, timeout, query_fn
        query_fn = args[-1]
        return _FakeFuture(query_fn())

      mock_exec.return_value = MagicMock(submit=_submit)

      with patch("hpcperfstats.site.machine.api.job_data") as mock_job_data, patch(
          "hpcperfstats.site.machine.api.metrics_data"
      ) as mock_metrics_data, patch(
          "hpcperfstats.site.machine.api.cached_orm"
      ) as mock_cached_orm:
        # cached_orm simply delegates to the query function
        mock_cached_orm.side_effect = (
            lambda *_args, **_kwargs: _args[-1]() if callable(_args[-1]) else None
        )

        # Configure job_data.querysets used in _dates_fn, _queues_fn, _states_fn
        mock_job_data.objects.dates.return_value = sample_dates
        mock_metrics_data.objects.distinct.return_value.values.return_value = sample_metrics
        mock_job_data.objects.distinct.return_value.values_list.return_value = sample_queues
        (
            mock_job_data.objects.exclude.return_value.distinct.return_value.values_list.return_value
        ) = sample_states

        response = api.home_options(request)

    assert response.status_code == 200
    data = response.data
    assert data["machine_name"] == "test-machine"
    assert data["year_list"] == [2024, 2023]
    # Ensure dates are grouped and sorted by month key
    assert data["date_list"]
    keys = [item[0] for item in data["date_list"]]
    assert "2024-01" in keys
    assert "2023-12" in keys
    assert data["metrics"] == sample_metrics
    assert data["queues"] == ["normal"]
    assert data["states"] == ["RUNNING"]


@pytest.mark.django_db
class TestSearchDispatch:
  """Tests for the search_dispatch helper endpoint."""

  def test_search_dispatch_redirects_to_job_when_jid_found(self):
    """search_dispatch returns redirect JSON when jid exists."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/search/", {"jid": "123"})

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api.cached_orm", return_value="123"
    ):
      response = api.search_dispatch(request)

    assert response.status_code == 200
    assert response.data["redirect"] == "/machine/job/123/"

  def test_search_dispatch_returns_404_when_jid_not_found(self):
    """search_dispatch returns 404 JSON error when jid does not exist."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/search/", {"jid": "999"})

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api.cached_orm", return_value=None
    ):
      response = api.search_dispatch(request)

    assert response.status_code == 404
    assert "error" in response.data

  def test_search_dispatch_redirects_to_host_plot_when_host_given(self):
    """search_dispatch returns redirect JSON to host plot URL with remaining query params."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get(
        "/api/search/",
        {"host": "node1", "start": "2024-01-01T00:00:00", "end": "2024-01-01T01:00:00"},
    )

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None):
      # job_list is not used when host is non-empty, but keep it patched to avoid DB.
      with patch("hpcperfstats.site.machine.api.job_list") as mock_job_list:
        response = api.search_dispatch(request)

    mock_job_list.assert_not_called()
    assert response.status_code == 200
    redirect_path = response.data["redirect"]
    assert redirect_path.startswith("/machine/host/node1/plot/")
    assert "start=2024-01-01T00%3A00%3A00" in redirect_path
    assert "end=2024-01-01T01%3A00%3A00" in redirect_path

  def test_search_dispatch_falls_back_to_job_list_when_no_jid_or_host(self):
    """search_dispatch delegates to job_list when no jid or host is provided."""
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/search/")

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api.job_list"
    ) as mock_job_list:
      mock_job_list.return_value = api.Response({"ok": True})
      response = api.search_dispatch(request)

    mock_job_list.assert_called_once()
    assert response.status_code == 200
    assert response.data["ok"] is True


class TestNonStaffJobVisibility:
  def test_apply_non_staff_visibility_includes_own_and_seen_accounts(self):
    from hpcperfstats.site.machine import api

    request = MagicMock()
    request.session = {"username": "alice", "is_staff": False}
    qs = MagicMock()
    filtered_qs = MagicMock()
    qs.filter.return_value = filtered_qs

    account_qs = MagicMock()
    account_qs.exclude.return_value = account_qs
    account_qs.values_list.return_value.distinct.return_value = ["proj-a", "proj-b"]

    with patch("hpcperfstats.site.machine.api.job_data.objects.filter", return_value=account_qs):
      out = api._apply_non_staff_job_visibility(qs, request)

    assert out == filtered_qs
    assert qs.filter.call_count == 1

  def test_apply_non_staff_visibility_without_username_returns_none_queryset(self):
    from hpcperfstats.site.machine import api

    request = MagicMock()
    request.session = {"is_staff": False}
    qs = MagicMock()
    none_qs = MagicMock()
    qs.none.return_value = none_qs

    out = api._apply_non_staff_job_visibility(qs, request)
    assert out == none_qs
    qs.none.assert_called_once()


class TestFormatLogTimestamp:
  def test_format_log_timestamp_naive_assumes_utc(self):
    from hpcperfstats.site.machine import api

    ts = datetime(2024, 1, 2, 3, 4, 5)  # naive
    out = api._format_log_timestamp(ts)
    assert out.startswith("2024-01-02T03:04:05")
    # Should normalise to UTC offset with colon, e.g. +00:00
    assert out.endswith("+00:00")

  def test_format_log_timestamp_preserves_timezone(self):
    from hpcperfstats.site.machine import api

    ts = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    out = api._format_log_timestamp(ts)
    assert out == "2024-01-02T03:04:05+00:00"

  def test_format_log_timestamp_non_datetime_falls_back_to_str(self):
    from hpcperfstats.site.machine import api

    out = api._format_log_timestamp("not-a-datetime")
    assert out == "not-a-datetime"


class TestSiteContentCacheTimeout:
  """Site TTL is driven by cache_utils.get_site_content_cache_timeout (not per-job age)."""

  def test_empty_db_uses_one_hour_ttl(self):
    from hpcperfstats.site.machine import cache_utils

    with patch.object(cache_utils, "get_site_newest_job_end_time", return_value=None):
      assert cache_utils.get_site_content_cache_timeout() == cache_utils.SITE_CACHE_TTL_FRESH_SECONDS

  def test_recent_newest_end_uses_one_hour_ttl(self):
    from hpcperfstats.site.machine import cache_utils

    now = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
    newest = datetime(2026, 3, 20, 0, 0, 0, tzinfo=timezone.utc)
    with patch.object(cache_utils, "get_site_newest_job_end_time", return_value=newest), patch.object(
        cache_utils.timezone, "now", return_value=now
    ):
      assert cache_utils.get_site_content_cache_timeout() == cache_utils.SITE_CACHE_TTL_FRESH_SECONDS

  def test_stale_newest_end_uses_unlimited_cache_ttl(self):
    from hpcperfstats.site.machine import cache_utils

    now = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
    newest = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    with patch.object(cache_utils, "get_site_newest_job_end_time", return_value=newest), patch.object(
        cache_utils.timezone, "now", return_value=now
    ):
      assert cache_utils.get_site_content_cache_timeout() is None


class TestGetApiKeyFromRequest:
  def test_get_api_key_from_authorization_header(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/")
    request.META["HTTP_AUTHORIZATION"] = "Api-Key secret123"

    key = api._get_api_key_from_request(request)
    assert key == "secret123"

  def test_get_api_key_from_x_api_key_header(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/")
    request.META["HTTP_X_API_KEY"] = "header-key"

    key = api._get_api_key_from_request(request)
    assert key == "header-key"

  def test_get_api_key_ignores_query_param(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/?api_key=query-key")

    key = api._get_api_key_from_request(request)
    assert key is None

  def test_get_api_key_returns_none_when_missing(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/")

    key = api._get_api_key_from_request(request)
    assert key is None


class TestCacheStats:
  def test_get_cache_stats_includes_total_cache_usable(self):
    from hpcperfstats.site.machine import api

    class _FakeRedisClient:
      def info(self):
        return {
            "used_memory": 1024,
            "used_memory_human": "1K",
            "maxmemory": 4096,
            "maxmemory_human": "4K",
        }

      def scan_iter(self, count=None):
        return []

    class _FakeCacheBackend:
      def get_client(self):
        return _FakeRedisClient()

    class _FakeCache:
      def __init__(self):
        self._cache = _FakeCacheBackend()

      def get(self, key):
        return None

      def set(self, key, value, timeout=None):
        return None

    with patch("hpcperfstats.site.machine.api.cache", _FakeCache()):
      stats = api._get_cache_stats()

    assert stats["total_cache_usable_bytes"] == 4096
    assert stats["total_cache_usable_human"] == "4K"


class TestAdminMonitor:
  def test_admin_monitor_rabbitmq_hosts_section(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"section": "rabbitmq_hosts"})
    request.session = {"is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
        "hpcperfstats.site.machine.api._get_recent_rabbitmq_host_stats",
        return_value=[{
            "host": "node1.example.com",
            "last_time": "2026-03-23T10:00:00+00:00",
            "age_bucket": "ok",
        }],
    ):
      response = api.admin_monitor(request)

    assert response.status_code == 200
    assert response.data == {
        "rabbitmq_host_stats": [{
            "host": "node1.example.com",
            "last_time": "2026-03-23T10:00:00+00:00",
            "age_bucket": "ok",
        }]
    }

  def test_admin_monitor_xalt_section(self):
    from hpcperfstats.site.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"section": "xalt"})
    request.session = {"is_staff": True}

    with patch("hpcperfstats.site.machine.api._require_auth", return_value=None), patch(
      "hpcperfstats.site.machine.api._get_xalt_jid_coverage",
      return_value={
        "total_jids": 2,
        "jids_with_xalt_data": 1,
        "jids_missing_xalt_data": 1,
        "found_jids": ["jid-1"],
        "found_jids_limit": 200,
        "found_jids_truncated": False,
        "missing_jids": ["jid-2"],
        "missing_jids_limit": 200,
        "missing_jids_truncated": False,
      },
    ):
      response = api.admin_monitor(request)

    assert response.status_code == 200
    assert response.data == {
      "xalt_stats": {
        "total_jids": 2,
        "jids_with_xalt_data": 1,
        "jids_missing_xalt_data": 1,
        "found_jids": ["jid-1"],
        "found_jids_limit": 200,
        "found_jids_truncated": False,
        "missing_jids": ["jid-2"],
        "missing_jids_limit": 200,
        "missing_jids_truncated": False,
      }
    }

  def test_get_recent_rabbitmq_host_stats_reads_redis_keys(self):
    from hpcperfstats.site.machine import api

    class _FakeRedisClient:
      def scan_iter(self, match=None, count=None):
        return [b"recent_host:node1.example.com", b"recent_host:node2.example.com"]

      def get(self, key):
        if key == b"recent_host:node1.example.com":
          return b"1710000000"
        if key == b"recent_host:node2.example.com":
          return b"1710000300"
        return None

    class _FakeCacheBackend:
      def get_client(self):
        return _FakeRedisClient()

    class _FakeCache:
      def __init__(self):
        self._cache = _FakeCacheBackend()

      def get(self, key):
        return None

      def set(self, key, value, timeout=None):
        return None

    with patch("hpcperfstats.site.machine.api.cache", _FakeCache()):
      host_stats = api._get_recent_rabbitmq_host_stats()

    hosts = sorted([row["host"] for row in host_stats])
    assert hosts == ["node1.example.com", "node2.example.com"]


class TestAgeBucket:
  """_age_bucket matches Redis and DB-backed admin host stats labeling."""

  @pytest.mark.parametrize(
      "age,expected",
      [
          (timedelta(weeks=1, microseconds=1), "gt_week"),
          (timedelta(days=1, seconds=1), "gt_day"),
          (timedelta(hours=1, seconds=1), "gt_hour"),
          (timedelta(minutes=10, seconds=1), "gt_10min"),
          (timedelta(minutes=5), "ok"),
          (timedelta(0), "ok"),
      ],
  )
  def test_age_bucket_thresholds(self, age, expected):
    from hpcperfstats.site.machine import api

    assert api._age_bucket(age) == expected

  def test_age_bucket_exact_week_is_not_gt_week(self):
    from hpcperfstats.site.machine import api

    assert api._age_bucket(timedelta(weeks=1)) == "gt_day"


class TestAdminMonitorHostStatDict:
  """_admin_monitor_host_stat_dict centralizes FQDN + age_bucket rows."""

  def test_returns_none_without_fqdn_or_last_time(self):
    from hpcperfstats.site.machine import api

    now = datetime(2024, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    t0 = datetime(2024, 1, 10, 11, 0, 0, tzinfo=timezone.utc)
    assert api._admin_monitor_host_stat_dict("", t0, now) is None
    assert api._admin_monitor_host_stat_dict("host", None, now) is None
    assert api._admin_monitor_host_stat_dict("short", t0, now) is None

  def test_returns_row_with_isoformat_and_bucket(self):
    from hpcperfstats.site.machine import api

    now = datetime(2024, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    t0 = datetime(2024, 1, 10, 11, 55, 0, tzinfo=timezone.utc)
    row = api._admin_monitor_host_stat_dict("n.example.com", t0, now)
    assert row["host"] == "n.example.com"
    assert row["last_time"] == t0.isoformat()
    assert row["age_bucket"] == "ok"


class TestApiKeyValid:
  def test_api_key_valid_returns_none_for_unknown_key(self):
    from hpcperfstats.site.machine import api

    with patch("hpcperfstats.site.machine.api.ApiKey.objects.get", side_effect=ApiKey.DoesNotExist):
      assert api._api_key_valid("does-not-exist") is None

  def test_api_key_valid_returns_active_key_and_updates_last_used(self):
    from hpcperfstats.site.machine import api

    raw_key = "k1"
    key_obj = MagicMock()
    key_obj.key = ApiKey.hash_raw_key(raw_key)
    with patch("hpcperfstats.site.machine.api.ApiKey.objects.get", return_value=key_obj):
      result = api._api_key_valid(raw_key)

    assert result is not None
    assert result == key_obj
    key_obj.save.assert_called_once()

