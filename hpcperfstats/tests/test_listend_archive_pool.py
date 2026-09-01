"""Tests for host-affine listend archive thread pool dispatch and FIFO."""
from __future__ import annotations

import threading
import time

import pytest


class _FakeMethodFrame:
  def __init__(self, delivery_tag=1):
    self.delivery_tag = delivery_tag


class _FakeChannel:
  def __init__(self):
    self.acked = []
    self.nacked = []
    self.lock = threading.Lock()

  def basic_ack(self, delivery_tag=None):
    with self.lock:
      self.acked.append(delivery_tag)

  def basic_nack(self, delivery_tag=None, requeue=False):
    with self.lock:
      self.nacked.append((delivery_tag, requeue))


class _FakeConnection:
  """Runs add_callback_threadsafe callbacks immediately (unit-test stand-in)."""

  def add_callback_threadsafe(self, callback):
    callback()


@pytest.fixture
def archive_pool_env(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  listend.stop_listend_archive_pool()
  channel = _FakeChannel()
  conn = _FakeConnection()
  listend.set_amqp_connection(conn, channel)
  listend.start_listend_archive_pool(n_threads=4)
  yield listend, channel, tmp_path
  listend.stop_listend_archive_pool()
  listend.clear_amqp_connection()


def test_host_affine_archive_index_stable():
  from hpcperfstats.dbload.lib.listend_db_ingest import host_affine_worker_index

  a = host_affine_worker_index("c001.example.edu", 8)
  b = host_affine_worker_index("c001.example.edu", 8)
  assert a == b
  assert 0 <= a < 8


def test_same_host_fifo_on_archive_worker(archive_pool_env, monkeypatch):
  listend, channel, tmp_path = archive_pool_env
  order = []
  order_lock = threading.Lock()
  real_append = listend.append_monitor_payload_to_archive

  def slow_append(message):
    host = message.split()[2]
    with order_lock:
      order.append(("start", host, message.split()[0]))
    time.sleep(0.05)
    result = real_append(message)
    with order_lock:
      order.append(("end", host, message.split()[0]))
    return result

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", slow_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )

  body1 = b"1710000001.0 1 samehost.example.com x\n"
  body2 = b"1710000002.0 1 samehost.example.com y\n"
  listend.on_message(channel, _FakeMethodFrame(1), None, body1)
  listend.on_message(channel, _FakeMethodFrame(2), None, body2)

  deadline = time.time() + 5.0
  while time.time() < deadline and len(channel.acked) < 2:
    time.sleep(0.01)
  assert channel.acked == [1, 2]
  starts = [t for t in order if t[0] == "start" and t[1] == "samehost.example.com"]
  assert [s[2] for s in starts] == ["1710000001.0", "1710000002.0"]


def test_different_hosts_can_archive_in_parallel(archive_pool_env, monkeypatch):
  listend, channel, _tmp = archive_pool_env
  barrier = threading.Barrier(2, timeout=5.0)
  real_append = listend.append_monitor_payload_to_archive

  def barrier_append(message):
    barrier.wait()
    return real_append(message)

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", barrier_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )

  listend.on_message(
      channel, _FakeMethodFrame(10), None,
      b"1710000001.0 1 host-a.example.com x\n",
  )
  listend.on_message(
      channel, _FakeMethodFrame(11), None,
      b"1710000001.0 1 host-b.example.com x\n",
  )

  deadline = time.time() + 5.0
  while time.time() < deadline and len(channel.acked) < 2:
    time.sleep(0.01)
  assert sorted(channel.acked) == [10, 11]


def test_ack_only_after_archive_success(archive_pool_env, monkeypatch):
  listend, channel, _tmp = archive_pool_env
  archived = []

  def tracking_append(message):
    archived.append(message)
    return listend.ArchiveAppendResult(
        host="h", path="/tmp/x", offset=0, length=1,
    )

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", tracking_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )
  listend.on_message(
      channel, _FakeMethodFrame(99), None,
      b"1710000001.0 1 myhost.example.com x\n",
  )
  deadline = time.time() + 5.0
  while time.time() < deadline and not channel.acked:
    time.sleep(0.01)
  assert archived
  assert channel.acked == [99]
  assert channel.nacked == []


def test_archive_io_error_nacks(archive_pool_env, monkeypatch):
  listend, channel, _tmp = archive_pool_env

  def boom(_message):
    raise OSError("disk full")

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", boom)
  listend.on_message(
      channel, _FakeMethodFrame(7), None,
      b"1710000001.0 1 myhost.example.com x\n",
  )
  deadline = time.time() + 5.0
  while time.time() < deadline and not channel.nacked:
    time.sleep(0.01)
  assert channel.acked == []
  assert channel.nacked == [(7, True)]


def test_drop_mode_submit_not_on_consume_thread(archive_pool_env, monkeypatch):
  listend, channel, _tmp = archive_pool_env
  consume_tid = threading.get_ident()
  submit_tids = []

  def capture_submit(*_a, **_k):
    submit_tids.append(threading.get_ident())
    return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      capture_submit,
  )
  listend.on_message(
      channel, _FakeMethodFrame(5), None,
      b"1710000001.0 1 myhost.example.com x\n",
  )
  deadline = time.time() + 5.0
  while time.time() < deadline and not channel.acked:
    time.sleep(0.01)
  assert channel.acked == [5]
  assert submit_tids
  assert all(tid != consume_tid for tid in submit_tids)


def test_archive_thread_title_role(monkeypatch):
  import hpcperfstats.listend as listend

  titles = []

  def capture_title(_title, *, script_name=None, role=None):
    titles.append((script_name, role))
    return "ok"

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.set_daemon_thread_title",
      capture_title,
  )
  listend.stop_listend_archive_pool()
  listend.start_listend_archive_pool(n_threads=2)
  time.sleep(0.2)
  listend.stop_listend_archive_pool()
  roles = {r for _s, r in titles if r and r.startswith("archive-")}
  assert "archive-0" in roles
  assert "archive-1" in roles
