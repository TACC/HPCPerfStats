"""Live Redis cache integration tests (Docker compose). Skipped unless HPCPERFSTATS_PYTEST_LIVE_REDIS=1."""
import os
import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from hpcperfstats.site.machine import api as api_module

_LIVE = os.environ.get("HPCPERFSTATS_PYTEST_LIVE_REDIS", "").strip().lower() in (
    "1",
    "yes",
    "true",
)

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason=(
        "Set HPCPERFSTATS_PYTEST_LIVE_REDIS=1 "
        "(see tests/run_redis_cache_pytest_workflow.sh)"
    ),
)


@pytest.mark.live_redis
def test_redis_cache_roundtrip_dict():
  token = uuid.uuid4().hex
  key = f"pytest_live:{token}:payload"
  payload = {"a": 1, "b": [2, 3]}
  cache.set(key, payload, timeout=60)
  try:
    assert cache.get(key) == payload
  finally:
    cache.delete(key)


@pytest.mark.live_redis
def test_get_redis_cache_client_supports_scan():
  client = api_module._get_redis_cache_client()
  assert client is not None
  assert hasattr(client, "scan_iter")


@pytest.mark.live_redis
def test_invalidate_cache_for_page_deletes_matching_key():
  token = uuid.uuid4().hex
  path = "/machine/jobs"
  raw_key = f"pytest_live:{token}:custom:{path}:cache_marker".encode("utf-8")
  client = api_module._get_redis_cache_client()
  assert client is not None
  client.set(raw_key, b"1")
  try:
    factory = RequestFactory()
    request = factory.post(
        "/api/cache/invalidate-page/",
        {"page_path": path},
        content_type="application/json",
    )
    request.session = {
        "access_token": "t",
        "username": "live-redis-test",
        "is_staff": True,
    }
    with patch.object(api_module, "_require_auth", return_value=None):
      response = api_module.invalidate_cache_for_page(request)
    assert response.status_code == 200
    assert client.get(raw_key) is None
  finally:
    try:
      client.delete(raw_key)
    except Exception:
      pass
