"""Regression tests for the archive-members Redis client singleton (T1).

A wedged socket on the shared runtime client used to block a day_close thread
on ``recv`` forever, holding its lease and slot. These tests lock the
connection kwargs, the creation lock, and the drop-on-error behavior.
"""
from __future__ import annotations

import threading

import pytest

from hpcperfstats.dbload.lib import sync_timedb_archive_members_redis as amr


class _StubClient:
  """Minimal client stub recording pool disconnects."""

  def __init__(self, url, **kwargs):
    self.url = url
    self.kwargs = dict(kwargs)
    self.pings = 0
    self.connection_pool = self

  def ping(self):
    self.pings += 1
    return True

  def disconnect(self):
    _StubClient.disconnected.append(self)


_StubClient.disconnected = []


class _StubRedisModule:
  def __init__(self, created):
    self._created = created

  def from_url(self, url, **kwargs):
    client = _StubClient(url, **kwargs)
    self._created.append(client)
    return client


@pytest.fixture(autouse=True)
def _clean_singleton(monkeypatch):
  amr.reset_archive_members_redis_client_for_tests()
  _StubClient.disconnected = []
  monkeypatch.setattr(amr.cfg, "get_redis_location", lambda: "redis://stub:6379/0")
  monkeypatch.setattr(amr, "archive_members_redis_enabled", lambda: True)
  yield
  amr.reset_archive_members_redis_client_for_tests()


def _install_stub_redis(monkeypatch, created):
  import sys

  module = _StubRedisModule(created)
  monkeypatch.setitem(sys.modules, "redis", module)


def test_connection_kwargs_bound_every_call_in_time():
  kwargs = amr.redis_client_connection_kwargs()
  assert kwargs["decode_responses"] is True
  assert kwargs["socket_connect_timeout"] > 0
  assert kwargs["socket_timeout"] > 0
  assert kwargs["retry_on_timeout"] is True
  assert kwargs["health_check_interval"] > 0
  assert kwargs["max_connections"] >= 1


def test_runtime_client_is_created_with_timeout_kwargs(monkeypatch):
  created = []
  _install_stub_redis(monkeypatch, created)
  client = amr.get_archive_members_redis_client(required=True)
  assert client is created[0]
  # The defect was a client built with decode_responses as its only kwarg.
  assert set(amr.redis_client_connection_kwargs()).issubset(client.kwargs)
  assert client.kwargs["socket_timeout"] == amr._REDIS_SOCKET_TIMEOUT_S


def test_concurrent_first_touch_creates_exactly_one_client(monkeypatch):
  created = []
  _install_stub_redis(monkeypatch, created)
  start = threading.Barrier(8)
  seen = []

  def _worker():
    start.wait()
    seen.append(amr.get_archive_members_redis_client(required=True))

  threads = [threading.Thread(target=_worker) for _ in range(8)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join()

  assert len(created) == 1
  assert all(client is created[0] for client in seen)


def test_drop_client_disconnects_pool_and_forces_reconnect(monkeypatch):
  created = []
  _install_stub_redis(monkeypatch, created)
  first = amr.get_archive_members_redis_client(required=True)
  amr.drop_archive_members_redis_client()
  assert _StubClient.disconnected == [first]
  second = amr.get_archive_members_redis_client(required=True)
  assert second is not first
  assert len(created) == 2


def test_ping_failure_drops_cached_client(monkeypatch):
  created = []
  _install_stub_redis(monkeypatch, created)
  client = amr.get_archive_members_redis_client(required=True)

  def _boom():
    raise OSError("connection reset by peer")

  monkeypatch.setattr(client, "ping", _boom)
  with pytest.raises(amr.ArchiveMembersRedisConnectionError):
    amr._verify_redis_ping_or_raise(client)
  # Caching a wedged client would fail every later command on the dead socket.
  assert amr._REDIS_CLIENT is None
  assert _StubClient.disconnected == [client]


def test_url_change_replaces_cached_client(monkeypatch):
  created = []
  _install_stub_redis(monkeypatch, created)
  first = amr.get_archive_members_redis_client(required=True)
  monkeypatch.setattr(amr.cfg, "get_redis_location", lambda: "redis://other:6379/1")
  second = amr.get_archive_members_redis_client(required=True)
  assert second is not first
  assert second.url == "redis://other:6379/1"
