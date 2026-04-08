"""Targeted API view tests for branches under-covered by integration tests.

Use ``django_db(databases=[])`` so they run on the host without compose ``db``.
ORM is mocked where views would otherwise query. LocMem cache avoids Redis
during ``@dynamic_cache_page`` wrapping.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory as DjangoRequestFactory, override_settings
from rest_framework.test import APIRequestFactory

pytestmark = pytest.mark.django_db(databases=[])

_API_COVERAGE_GAP_SETTINGS = {
    "ALLOWED_HOSTS": ["testserver", "example.com", "localhost", "127.0.0.1"],
    "CACHES": {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "api-coverage-gap-tests",
        }
    },
}


@pytest.fixture(autouse=True)
def _api_coverage_gap_settings():
  with override_settings(**_API_COVERAGE_GAP_SETTINGS):
    yield


def _plain_post(path, body: bytes):
  """POST with raw body (sacct-style); pass bare HttpRequest into ``@api_view``."""
  return DjangoRequestFactory().post(
      path, data=body, content_type="text/plain"
  )


class _EmptyQueryChain:
  """Minimal queryset chain for ``job_monitor`` aggregation (empty results)."""

  def values(self, *args, **kwargs):
    return self

  def annotate(self, *args, **kwargs):
    return self

  def filter(self, *args, **kwargs):
    return self

  def order_by(self, *args, **kwargs):
    return self

  def __iter__(self):
    return iter(())


class TestInvalidateCacheForPage:
  """invalidate_cache_for_page staff gate, validation, and Redis scan path."""

  def test_requires_staff(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "/machine/"},
        format="json",
    )
    denied = api.Response({"detail": "no"}, status=403)
    with patch.object(api, "_require_staff", return_value=denied):
      response = api.invalidate_cache_for_page(request)
    assert response.status_code == 403

  def test_missing_page_path_returns_400(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.post("/api/cache/invalidate-page/", {}, format="json")
    with patch.object(api, "_require_staff", return_value=None):
      response = api.invalidate_cache_for_page(request)
    assert response.status_code == 400
    assert "page_path" in str(response.data.get("error", "")).lower()

  def test_non_scannable_cache_returns_503(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "jobs"},
        format="json",
    )
    request.META["HTTP_HOST"] = "example.com"
    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "_get_redis_cache_client", return_value=object()
    ):
      response = api.invalidate_cache_for_page(request)
    assert response.status_code == 503

  def test_success_deletes_matching_keys(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": "/machine"},
        format="json",
    )
    request.META["HTTP_HOST"] = "example.com"

    class _FakeRedis:
      def __init__(self):
        self.deleted = []

      def scan_iter(self, count=500):
        yield b"unrelated"
        yield b"cache:machine:https://example.com/machine"

      def delete(self, raw_key):
        self.deleted.append(raw_key)

    fake = _FakeRedis()
    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "_get_redis_cache_client", return_value=fake
    ):
      response = api.invalidate_cache_for_page(request)
    assert response.status_code == 200
    assert response.data["ok"] is True
    assert response.data["page_path"] == "/machine"
    assert response.data["deleted_keys"] >= 1


class TestHostPlotApi:
  """host_plot auth, validation, and cached payload."""

  def test_requires_auth(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get(
        "/api/host_plot/",
        {"host": "n1", "end_time__gte": "2024-01-01T00:00:00+00:00"},
    )
    denied = api.Response({"detail": "no"}, status=401)
    with patch.object(api, "_require_auth", return_value=denied):
      response = api.host_plot(request)
    assert response.status_code == 401

  def test_missing_host_or_start_returns_400(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get("/api/host_plot/", {"end_time__gte": "2024-01-01"})
    with patch.object(api, "_require_auth", return_value=None):
      response = api.host_plot(request)
    assert response.status_code == 400

  def test_returns_plot_item_when_cache_hit(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get(
        "/api/host_plot/",
        {
            "host": "n1.example.com",
            "end_time__gte": "2024-06-01T12:00:00Z",
            "end_time__lte": "now()",
        },
    )
    fake_item = {"type": "object", "name": "test_plot"}
    with patch.object(api, "_require_auth", return_value=None), patch.object(
        api, "cached_orm", return_value=fake_item
    ), patch.object(api, "get_site_content_cache_timeout", return_value=60):
      response = api.host_plot(request)
    assert response.status_code == 200
    assert response.data["plot_item"] == fake_item
    assert response.data["host"] == "n1.example.com"


class TestJobMonitorApi:
  """job_monitor staff gate and days clamping."""

  def test_requires_staff(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get("/api/job_monitor/")
    denied = api.Response({"detail": "no"}, status=403)
    with patch.object(api, "_require_staff", return_value=denied):
      response = api.job_monitor(request)
    assert response.status_code == 403

  def test_clamps_days_and_returns_shape(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get("/api/job_monitor/", {"days": "9999"})
    jd = MagicMock()
    jd.objects.filter.return_value = _EmptyQueryChain()
    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "job_data", jd
    ):
      response = api.job_monitor(request)
    assert response.status_code == 200
    assert response.data["window_days"] == 365
    assert "results" in response.data
    assert response.data["results"] == []


class TestJobMonitorGpuForUserApi:
  """job_monitor_gpu_for_user validation."""

  def test_requires_staff(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get("/api/job_monitor/gpu/", {"username": "u1"})
    denied = api.Response({"detail": "no"}, status=403)
    with patch.object(api, "_require_staff", return_value=denied):
      response = api.job_monitor_gpu_for_user(request)
    assert response.status_code == 403

  def test_missing_username_returns_400(self):
    from hpcperfstats.site.machine import api

    factory = APIRequestFactory()
    request = factory.get("/api/job_monitor/gpu/", {})
    with patch.object(api, "_require_staff", return_value=None):
      response = api.job_monitor_gpu_for_user(request)
    assert response.status_code == 400

  def test_sums_detail_gpu_metrics_when_rows_exist(self):
    """Rollup uses metrics_data sums when any detail_gpu_count row exists in window."""
    from hpcperfstats.site.machine import api

    class R0:
      def filter(self, **kw):
        if kw.get("metric") == "detail_gpu_count" and "value__isnull" not in kw:

          class R1:
            def exists(self):
              return True

          return R1()
        if (
            kw.get("metric") == "detail_gpu_active"
            and kw.get("value__isnull") is False
        ):

          class Ra:
            def aggregate(self, **_):
              return {"s": 3.0}

          return Ra()
        if (
            kw.get("metric") == "detail_gpu_count"
            and kw.get("value__isnull") is False
        ):

          class Rc:
            def aggregate(self, **_):
              return {"s": 12.0}

          return Rc()
        return R0()

    class _MDObjects:
      def filter(self, **_kwargs):
        return R0()

    class MD:
      objects = _MDObjects()

    factory = APIRequestFactory()
    request = factory.get("/api/job_monitor/gpu/", {"username": "alice"})
    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "get_site_content_cache_timeout", return_value=60
    ), patch.object(
        api, "cached_orm", side_effect=lambda _k, _t, fn: fn()
    ), patch.object(api, "metrics_data", MD):
      response = api.job_monitor_gpu_for_user(request)
    assert response.status_code == 200
    assert response.data["gpu_active_total"] == 3
    assert response.data["gpu_count_total"] == 12
    assert response.data["gpu_active_percentage"] == 25.0
    assert response.data["has_data"] is True


class TestSacctIngestApi:
  """sacct_ingest staff gate and body/date validation."""

  def test_requires_staff(self):
    from hpcperfstats.site.machine import api

    request = _plain_post("/api/sacct/ingest/?date=2024-01-01", b"x")
    denied = api.Response({"detail": "no"}, status=403)
    with patch.object(api, "_require_staff", return_value=denied):
      response = api.sacct_ingest(request)
    assert response.status_code == 403

  def test_empty_body_returns_zero_inserted(self):
    from hpcperfstats.site.machine import api

    request = _plain_post("/api/sacct/ingest/?date=2024-01-02", b"  \n")
    with patch.object(api, "_require_staff", return_value=None):
      response = api.sacct_ingest(request)
    assert response.status_code == 200
    assert response.data["inserted"] == 0

  def test_missing_date_returns_400(self):
    from hpcperfstats.site.machine import api

    request = _plain_post("/api/sacct/ingest/", b"a|b")
    with patch.object(api, "_require_staff", return_value=None):
      response = api.sacct_ingest(request)
    assert response.status_code == 400

  def test_invalid_date_returns_400(self):
    from hpcperfstats.site.machine import api

    request = _plain_post("/api/sacct/ingest/?date=not-a-date", b"x")
    with patch.object(api, "_require_staff", return_value=None):
      response = api.sacct_ingest(request)
    assert response.status_code == 400

  def test_ingest_calls_sync_acct(self):
    from hpcperfstats.site.machine import api

    body = "JobID|State\n123|COMPLETED\n"
    request = _plain_post(
        "/api/sacct/ingest/?date=2024-06-15",
        body.encode("utf-8"),
    )
    jd = MagicMock()
    vs = MagicMock()
    vs.iterator.return_value = iter([])
    jd.objects.filter.return_value.values_list.return_value = vs
    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "sync_acct_from_content", return_value=3
    ) as mock_sync, patch.object(api, "job_data", jd):
      response = api.sacct_ingest(request)
    assert response.status_code == 200
    assert response.data["inserted"] == 3
    mock_sync.assert_called_once()
