"""Host tests for competing AMQP consumers, prefetch split, and reorder."""
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
  """Runs add_callback_threadsafe callbacks immediately."""

  def __init__(self):
    self.threadsafe = []

  def add_callback_threadsafe(self, callback):
    self.threadsafe.append(callback)
    callback()


@pytest.fixture
def archive_pool_env(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  listend.stop_listend_archive_pool()
  listend._amqp_consumer_count = 1
  listend._amqp_applied_prefetch = 0
  channel = _FakeChannel()
  conn = _FakeConnection()
  listend.set_amqp_connection(conn, channel)
  listend.start_listend_archive_pool(n_threads=4)
  yield listend, channel, conn, tmp_path
  listend.stop_listend_archive_pool()
  listend.clear_amqp_connection()
  listend._amqp_consumer_count = 1
  listend._archive_inflight_add(-listend.archive_inflight_count())


def test_amqp_prefetch_split_across_consumers():
  import hpcperfstats.listend as listend

  assert listend.split_listend_amqp_prefetch(128, 8) == 16
  assert listend.split_listend_amqp_prefetch(128, 1) == 128
  assert listend.split_listend_amqp_prefetch(7, 8) == 1
  assert listend.split_listend_amqp_prefetch(0, 8) == 1


def test_n1_skips_reorder(archive_pool_env, monkeypatch):
  listend, channel, _conn, _tmp = archive_pool_env
  order = []
  listend._amqp_consumer_count = 1

  def tracking_append(message):
    order.append(message.split()[0])
    return listend.ArchiveAppendResult(
        host="samehost.example.com", path="/tmp/x", offset=0, length=1,
    )

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", tracking_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )
  later = b"1710000121.0 1 samehost.example.com y\n"
  earlier = b"1710000001.0 1 samehost.example.com x\n"
  listend.on_message(channel, _FakeMethodFrame(1), None, later)
  listend.on_message(channel, _FakeMethodFrame(2), None, earlier)
  deadline = time.time() + 5.0
  while time.time() < deadline and len(channel.acked) < 2:
    time.sleep(0.01)
  assert channel.acked == [1, 2]
  assert order == [b"1710000121.0", b"1710000001.0"] or order == [
      "1710000121.0", "1710000001.0",
  ]


def test_same_host_later_timestamp_waits_for_earlier(archive_pool_env, monkeypatch):
  listend, channel, _conn, _tmp = archive_pool_env
  order = []
  listend._amqp_consumer_count = 8

  def tracking_append(message):
    tok = message.split()[0]
    if isinstance(tok, (bytes, bytearray)):
      tok = tok.decode("utf-8")
    order.append(tok)
    return listend.ArchiveAppendResult(
        host="samehost.example.com", path="/tmp/x", offset=0, length=1,
    )

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", tracking_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )
  later = b"1710000121.0 1 samehost.example.com y\n"
  earlier = b"1710000001.0 1 samehost.example.com x\n"
  listend.on_message(channel, _FakeMethodFrame(1), None, later)
  listend.on_message(channel, _FakeMethodFrame(2), None, earlier)
  deadline = time.time() + 5.0
  while time.time() < deadline and len(order) < 2:
    time.sleep(0.01)
  assert order == ["1710000001.0", "1710000121.0"]
  assert sorted(channel.acked) == [1, 2]


def test_dollar_rotates_before_digit_same_second(archive_pool_env, monkeypatch):
  listend, channel, _conn, _tmp = archive_pool_env
  order = []
  listend._amqp_consumer_count = 8

  def tracking_append(message):
    if isinstance(message, (bytes, bytearray)):
      text = bytes(message).decode("utf-8", errors="replace")
    else:
      text = str(message)
    order.append("$" if text.lstrip().startswith("$") else "digit")
    return listend.ArchiveAppendResult(
        host="c001.example.edu", path="/tmp/x", offset=0, length=1,
    )

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", tracking_append)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )
  digit = b"1710000001.0 1 c001.example.edu extra\n"
  dollar = b"$\n1 c001.example.edu\n1710000001.0 1 c001.example.edu extra\n"
  listend.on_message(channel, _FakeMethodFrame(1), None, digit)
  listend.on_message(channel, _FakeMethodFrame(2), None, dollar)
  deadline = time.time() + 5.0
  while time.time() < deadline and len(order) < 2:
    time.sleep(0.01)
  assert order == ["$", "digit"]


def test_ack_uses_originating_connection(archive_pool_env, monkeypatch):
  listend, _global_ch, _global_conn, _tmp = archive_pool_env
  listend._amqp_consumer_count = 8
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda *a, **k: True,
  )

  ch0, conn0 = _FakeChannel(), _FakeConnection()
  ch1, conn1 = _FakeChannel(), _FakeConnection()
  s0 = listend._AmqpConsumeSession(0)
  s0.bind(conn0, ch0)
  s1 = listend._AmqpConsumeSession(1)
  s1.bind(conn1, ch1)
  body0 = b"1710000001.0 1 host-a.example.com x\n"
  body1 = b"1710000001.0 1 host-b.example.com x\n"
  listend._dispatch_to_archive_pool(
      10, body0, "host-a.example.com",
      session=s0, unix_second=1710000001, is_dollar=False,
  )
  listend._dispatch_to_archive_pool(
      11, body1, "host-b.example.com",
      session=s1, unix_second=1710000001, is_dollar=False,
  )
  deadline = time.time() + 5.0
  while time.time() < deadline and not (ch0.acked and ch1.acked):
    time.sleep(0.01)
  assert ch0.acked == [10]
  assert ch1.acked == [11]
  assert _global_ch.acked == []


def test_idle_monitor_archive_suffix_includes_amqp_consumers(monkeypatch):
  import hpcperfstats.listend as listend

  listend._archive_pool_started = True
  listend._archive_queues = []
  listend._amqp_consumer_count = 8
  listend._amqp_applied_prefetch = 16
  listend._amqp_consumer_threads = []
  listend._archive_inflight_add(-listend.archive_inflight_count())
  suffix = listend._format_listend_idle_archive_suffix()
  assert "archive_q_depth=" in suffix
  assert "inflight=" in suffix
  assert "amqp_consumers=" in suffix
  assert "prefetch=16" in suffix
  listend._archive_pool_started = False
  listend._amqp_applied_prefetch = 0
  listend._amqp_consumer_count = 1


def test_parse_monitor_payload_archive_hint_dollar_and_digit():
  from hpcperfstats.dbload.lib.listend_db_ingest import (
      parse_monitor_payload_archive_hint,
  )

  host, ts, is_dollar = parse_monitor_payload_archive_hint(
      "1710000001.0 1 host.example.edu extra\n"
  )
  assert host == "host.example.edu"
  assert ts == 1710000001
  assert is_dollar is False
  host, ts, is_dollar = parse_monitor_payload_archive_hint(
      "$\n1 c001.example.edu\n1710000001.0 1 c001.example.edu extra\n"
  )
  assert host == "c001.example.edu"
  assert ts == 1710000001
  assert is_dollar is True
