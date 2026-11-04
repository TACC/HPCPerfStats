"""Live Valkey cache integration tests (compose). Skipped unless HPCPERFSTATS_PYTEST_LIVE_VALKEY=1."""

import contextlib
import os
import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory

from hpcperfstats.site.lib.machine import api as api_module
from hpcperfstats.site.lib.machine.cache_utils import get_valkey_client

from .csrf_test_utils import csrf_headers

_COMPOSE = os.environ.get(
  "HPCPERFSTATS_COMPOSE_NETWORK", ""
).strip().lower() in (
  "1",
  "yes",
  "true",
)
_LIVE = os.environ.get(
  "HPCPERFSTATS_PYTEST_LIVE_VALKEY", ""
).strip().lower() in (
  "1",
  "yes",
  "true",
)

pytestmark = pytest.mark.skipif(
  not (_COMPOSE and _LIVE),
  reason=(
    "Requires the compose network and live Valkey cache "
    "(HPCPERFSTATS_COMPOSE_NETWORK=1 and HPCPERFSTATS_PYTEST_LIVE_VALKEY=1). "
    "Run: tests/run_valkey_cache_pytest_workflow.sh"
  ),
)


@pytest.mark.live_valkey
def test_cache_backend_is_django_valkey():
  assert (
    settings.CACHES["default"]["BACKEND"] == "django_valkey.cache.ValkeyCache"
  )


@pytest.mark.live_valkey
def test_valkey_cache_roundtrip_dict_and_counters():
  token = uuid.uuid4().hex
  key = f"pytest_live:{token}:payload"
  payload = {"a": 1, "b": [2, 3]}
  cache.set(key, payload, timeout=60)
  try:
    assert cache.get(key) == payload
    assert cache.add(f"{key}:n", 5, timeout=60)
    assert not cache.add(f"{key}:n", 6, timeout=60)
    assert cache.incr(f"{key}:n") == 6
    assert cache.touch(key, 120)
    cache.set_many({f"{key}:x": 1, f"{key}:y": 2}, timeout=60)
    assert cache.get_many([f"{key}:x", f"{key}:y"]) == {
      f"{key}:x": 1,
      f"{key}:y": 2,
    }
    cache.delete_many([f"{key}:x", f"{key}:y"])
    assert cache.get(f"{key}:x") is None
  finally:
    cache.delete_many([key, f"{key}:n"])


@pytest.mark.live_valkey
def test_valkey_cache_roundtrips_http_response():
  """dynamic_cache_page stores HttpResponse objects (pickle serializer)."""
  key = f"pytest_live:{uuid.uuid4().hex}:response"
  response = HttpResponse(b"page", content_type="text/plain")
  response["X-Live"] = "1"
  cache.set(key, response, timeout=60)
  try:
    got = cache.get(key)
    assert got.content == b"page"
    assert got["X-Live"] == "1"
  finally:
    cache.delete(key)


@pytest.mark.live_valkey
def test_valkey_server_is_valkey_9():
  client = get_valkey_client()
  assert client is not None
  info = client.info(section="server")
  assert info.get("server_name") == "valkey"
  version = info.get("valkey_version") or ""
  major = int(version.split(".", maxsplit=1)[0])
  assert major >= 9, f"expected Valkey 9.x, got valkey_version={version!r}"


@pytest.mark.live_valkey
def test_valkey_compose_tuning_io_threads_and_unix_socket():
  """Compose Valkey uses 4 I/O threads and the shared Unix socket."""
  client = get_valkey_client()
  assert client is not None
  io_threads = client.config_get("io-threads")
  assert io_threads.get("io-threads") == "4"
  unixsocket = client.config_get("unixsocket")
  assert unixsocket.get("unixsocket") == "/run/valkey/valkey.sock"
  kwargs = client.connection_pool.connection_kwargs
  assert kwargs.get("path") == "/run/valkey/valkey.sock"
  assert kwargs.get("db") == 1


@pytest.mark.live_valkey
def test_get_valkey_client_raw_commands():
  """SADD/SMEMBERS, SCAN and MEMORY USAGE work through get_valkey_client()."""
  client = get_valkey_client()
  assert client is not None
  key = f"pytest_live:{uuid.uuid4().hex}:set"
  try:
    client.sadd(key, "k1", "k2")
    assert {m.decode() for m in client.smembers(key)} == {"k1", "k2"}
    assert key.encode() in set(client.scan_iter(match=f"{key}*", count=500))
    assert client.memory_usage(key) > 0
  finally:
    client.delete(key)


@pytest.mark.live_valkey
def test_admin_cache_stats_report_valkey_version_and_keys():
  cache.delete(api_module.KEY_ADMIN_CACHE_STATS)
  marker = f"pytest_live:{uuid.uuid4().hex}:keyspace"
  cache.set(marker, 1, timeout=60)
  try:
    stats = api_module._get_cache_stats()
  finally:
    cache.delete(marker)
  assert stats["valkey_version"].split(".")[0].isdigit()
  assert "redis_version" not in stats
  # Counted in the configured DB 1 (regression: old code read DB 0 -> null).
  assert stats["keys"] >= 1


@pytest.mark.live_valkey
def test_invalidate_cache_for_page_deletes_matching_key():
  token = uuid.uuid4().hex
  path = "/machine/jobs"
  raw_key = f"pytest_live:{token}:custom:{path}:cache_marker".encode()
  client = get_valkey_client()
  assert client is not None
  client.set(raw_key, b"1")
  try:
    factory = RequestFactory()
    request = factory.post(
      "/api/cache/invalidate-page/",
      {"page_path": path},
      content_type="application/json",
      **csrf_headers(),
    )
    request.session = {
      "access_token": "t",
      "username": "live-valkey-test",
      "is_staff": True,
    }
    with patch.object(api_module, "_require_auth", return_value=None):
      response = api_module.invalidate_cache_for_page(request)
    assert response.status_code == 200
    assert client.get(raw_key) is None
  finally:
    with contextlib.suppress(Exception):
      client.delete(raw_key)
