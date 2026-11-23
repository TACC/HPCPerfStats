"""listend raw-archive duplicate-sample guard and requeue-path counters.

hs04 2026-11-22: ``c115-001.../1795325986`` held a run of samples written
twice; the copy reappeared after newer samples with identical header
timestamps. listend archived a message whose ack was then dropped (stale
consume generation), and the broker redelivered it.
"""

from __future__ import annotations

import pytest

import hpcperfstats.listend as ld

HOST = "c115-001.horizon.tacc.utexas.edu"


def _sample(ts: str, jid: str = "100258", value: int = 1) -> bytes:
  return f"\n{ts} {jid} {HOST}\n%jobs jids={jid}\ncpu 0 {value} 2 3\n".encode()


@pytest.fixture
def archive(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(ld, "_enqueue_recent_host_update", lambda h: None)
  monkeypatch.setattr(ld, "_enqueue_monitor_identity_update", lambda i: None)
  monkeypatch.setattr(ld, "_enqueue_rotation_record", lambda r: None)
  monkeypatch.setattr(
    ld, "_listend_dup_stats", dict.fromkeys(ld._LISTEND_DUP_STAT_KEYS, 0)
  )
  monkeypatch.setattr(ld, "_listend_dup_hosts", set())
  ld._release_sticky_archive_writer()
  yield tmp_path / HOST / "current"
  ld._release_sticky_archive_writer()


def test_c115_resent_run_after_newer_samples_is_skipped(archive):
  """The hs04 shape: a run re-sent after newer samples is not re-appended."""
  run = [
    _sample("1795343696.933871"),
    _sample("1795343699.018533"),
    _sample("1795343757.934249"),
    _sample("1795343817.923799"),
  ]
  newer = [
    _sample("1795343877.933650"),
    _sample("1795343937.914126"),
    _sample("1795343939.988251"),
  ]
  for body in run + newer:
    ld.append_monitor_payload_to_archive(body)
  expected = archive.read_bytes()
  for body in run:
    result = ld.append_monitor_payload_to_archive(body)
    assert result.length == 0
    assert result.dup_samples == 1
  merged = ld.append_monitor_payload_to_archive(b"".join(run))
  assert (merged.length, merged.dup_samples) == (0, 4)
  assert archive.read_bytes() == expected
  assert archive.read_bytes().count(b"1795343696.933871") == 1
  assert ld._listend_dup_stats["skipped"] == 8
  assert ld._listend_dup_stats["trimmed_msgs"] == 0


def test_mixed_payload_appends_only_new_samples(archive):
  """A merged re-send of archived samples plus new ones appends the new."""
  a, b = _sample("1795343696.933871"), _sample("1795343757.934249")
  c, d = _sample("1795343817.923799"), _sample("1795343877.933650")
  ld.append_monitor_payload_to_archive(a)
  ld.append_monitor_payload_to_archive(b)
  before = archive.stat().st_size
  result = ld.append_monitor_payload_to_archive(a + b + c + d)
  assert result.offset == before
  assert result.length == len(c + d)
  assert result.dup_samples == 2
  assert archive.read_bytes() == a + b + c + d
  assert archive.read_bytes()[result.offset :] == c + d
  assert ld._listend_dup_stats["trimmed_msgs"] == 1
  # The trimmed suffix is remembered as its own span: re-sending the whole
  # merged body again appends nothing.
  again = ld.append_monitor_payload_to_archive(a + b + c + d)
  assert (again.length, again.dup_samples) == (0, 4)
  assert archive.read_bytes() == a + b + c + d


def test_redelivered_multi_sample_message_is_skipped_whole(archive):
  """An identical redelivery of a merged message appends nothing."""
  body = _sample("1795343696.933871") + _sample("1795343757.934249")
  ld.append_monitor_payload_to_archive(body)
  result = ld.append_monitor_payload_to_archive(body)
  assert (result.length, result.dup_samples) == (0, 2)
  assert archive.read_bytes() == body


def test_distinct_same_second_samples_are_kept(archive):
  """Same second with another fraction, or another jid, is not a dup."""
  bodies = [
    _sample("1795343939.988251"),
    _sample("1795343939.988252"),
    _sample("1795343939.100000"),
    # Shared-node burst: one frozen timestamp, per-jid payloads plus '-'.
    _sample("1795343939.988251", jid="100259"),
    _sample("1795343939.988251", jid="-"),
  ]
  for body in bodies:
    assert ld.append_monitor_payload_to_archive(body).dup_samples == 0
  same_ts_burst = _sample("1795344000.5", jid="7") + _sample(
    "1795344000.5", jid="8"
  )
  assert ld.append_monitor_payload_to_archive(same_ts_burst).dup_samples == 0
  assert archive.read_bytes() == b"".join(bodies) + same_ts_burst
  assert ld._listend_dup_stats["skipped"] == 0


def _dollar(ts: str) -> bytes:
  return (
    f"$hpcperfstats 3.0\n1 {HOST}\n!cpu user,E\n{ts} 100258 {HOST}\ncpu 0 5\n"
  ).encode()


def test_dollar_rotation_keeps_guard(archive, monkeypatch):
  """``$`` rotates normally; the guard survives it and dedupes a ``$``."""
  unlinks = []
  real_unlink = ld.os.unlink

  def _spy_unlink(path):
    unlinks.append(path)
    real_unlink(path)

  monkeypatch.setattr(ld.os, "unlink", _spy_unlink)
  old = _sample("1795343696.933871")
  ld.append_monitor_payload_to_archive(old)
  rot = _dollar("1795343757.934249")
  first = ld.append_monitor_payload_to_archive(rot)
  assert (first.length, first.dup_samples) == (len(rot), 0)
  assert archive.read_bytes() == rot
  assert len(unlinks) == 1
  # A pre-rotation sample redelivered after the $ stays out of the new segment.
  assert ld.append_monitor_payload_to_archive(old).length == 0
  after = _sample("1795343817.923799")
  ld.append_monitor_payload_to_archive(after)
  # A redelivered identical $ neither rotates nor re-appends its sample.
  again = ld.append_monitor_payload_to_archive(rot)
  assert (again.length, again.dup_samples) == (0, 1)
  assert len(unlinks) == 1
  assert archive.read_bytes() == rot + after
  # A new $ (new sample) still rotates; one without a sample always does.
  ld.append_monitor_payload_to_archive(_dollar("1795343877.933650"))
  ld.append_monitor_payload_to_archive(
    f"$hpcperfstats 3.0\n1 {HOST}\n".encode()
  )
  assert len(unlinks) == 3


class _Frame:
  def __init__(self, tag: int, redelivered: bool) -> None:
    self.delivery_tag = tag
    self.redelivered = redelivered


class _Channel:
  def __init__(self) -> None:
    self.acked: list = []
    self.nacked: list = []

  def basic_ack(self, delivery_tag=None):
    self.acked.append(delivery_tag)

  def basic_nack(self, delivery_tag=None, requeue=False):
    self.nacked.append((delivery_tag, requeue))


def test_redelivered_counter_and_summary_line(archive, monkeypatch):
  """Duplicates are acked, split by AMQP redelivered, and summarized once."""
  submitted = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
    lambda *a, **k: submitted.append(k),
  )
  ch = _Channel()
  body = _sample("1795343696.933871")
  ld.on_message(ch, _Frame(1, False), None, body)
  ld.on_message(ch, _Frame(2, True), None, body)
  ld.on_message(ch, _Frame(3, False), None, body)
  assert ch.acked == [1, 2, 3]
  assert ch.nacked == []
  assert len(submitted) == 1, "a skipped duplicate is not sent to live DB"
  stats = ld._listend_dup_stats
  assert (stats["skipped"], stats["redelivered"], stats["fresh"]) == (2, 1, 1)
  lines: list[str] = []
  monkeypatch.setattr(ld, "log_print", lambda msg, **_k: lines.append(msg))
  ld._emit_listend_dup_summary()
  assert lines == [
    "listend dup_samples skipped=2 redelivered=1 fresh=1 trimmed_msgs=0 "
    f"skipped_bytes={2 * len(body)} hosts=1 stale_unarchived=0 "
    "stale_acks=0 backpressure_nacks=0 error_nacks=0"
  ]
  ld._emit_listend_dup_summary()
  assert len(lines) == 1, "a window with no events logs nothing"


class _ImmediateConn:
  is_closed = False

  def add_callback_threadsafe(self, callback):
    callback()


def test_stale_generation_item_is_not_archived(archive, monkeypatch):
  """Root cause: an item from a closed consume channel is not archived.

  The broker redelivers it after the channel closes; archiving it first
  wrote the sample twice.
  """
  monkeypatch.setattr(ld, "log_print", lambda *a, **k: None)
  session = ld._AmqpConsumeSession(0)
  old_ch = _Channel()
  session.bind(_ImmediateConn(), old_ch)
  enqueued = session.generation
  session.bind(None, None)
  ld._archive_and_submit_then_ack(
    5, _sample("1795343696.933871"), session=session, ack_generation=enqueued
  )
  assert not archive.exists()
  assert old_ch.acked == [] and old_ch.nacked == []
  session.bind(_ImmediateConn(), _Channel())
  enqueued = session.generation
  session.reconnect_requested = True
  ld._archive_and_submit_then_ack(
    6, _sample("1795343696.933871"), session=session, ack_generation=enqueued
  )
  assert not archive.exists()
  assert ld._listend_dup_stats["stale_unarchived"] == 2


def test_live_generation_item_is_archived_and_acked(archive, monkeypatch):
  """The pre-archive stale check leaves a live session's item alone."""
  session = ld._AmqpConsumeSession(0)
  ch = _Channel()
  session.bind(_ImmediateConn(), ch)
  body = _sample("1795343696.933871")
  ld._archive_and_submit_then_ack(
    7, body, session=session, ack_generation=session.generation
  )
  assert archive.read_bytes() == body
  assert ch.acked == [7]
  assert ld._listend_dup_stats["stale_unarchived"] == 0


def test_stale_ack_and_nack_paths_are_counted(archive, monkeypatch):
  """Ack dropped after archive, backpressure nack and error nack counters."""
  monkeypatch.setattr(ld, "log_print", lambda *a, **k: None)
  session = ld._AmqpConsumeSession(0)
  session.bind(_ImmediateConn(), _Channel())
  enqueued = session.generation
  session.bind(_ImmediateConn(), _Channel())
  ld._threadsafe_basic_ack(9, session=session, ack_generation=enqueued)
  ch = _Channel()
  monkeypatch.setattr(ld, "_live_db_ingest_pool_active", lambda: None)
  monkeypatch.setattr(ld, "_db_backpressure_pause", False)
  ch.stop_consuming = lambda: None
  ld._request_db_backpressure_pause(ch, 10)
  ld.on_message(ch, _Frame(11, False), None, b"\n")
  stats = ld._listend_dup_stats
  assert stats["stale_acks"] == 1
  assert stats["backpressure_nacks"] == 1
  assert stats["error_nacks"] == 1
  assert ch.nacked == [(10, True), (11, True)]
  assert not archive.exists(), "a backpressure nack never archives"


def test_digit_payload_host_matches_parse_host(archive, monkeypatch):
  """The append takes a digit payload's host from its header line."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ingest

  bodies = [
    _sample("1795343696.933871"),
    f"1795343697.5 1 {HOST} extra\ncpu 0 1\n".encode(),
    b"\r\n \t" + _sample("1795343698.5"),
  ]
  expected = [ingest.parse_host_from_monitor_payload(b) for b in bodies]

  def _not_called(_payload):
    raise AssertionError("digit payloads must not re-parse the host")

  with monkeypatch.context() as patch:
    patch.setattr(ingest, "parse_host_from_monitor_payload", _not_called)
    hosts = [ld.append_monitor_payload_to_archive(b).host for b in bodies]
  assert hosts == expected == [HOST] * 3
  with pytest.raises(ValueError, match="not enough fields"):
    ld.append_monitor_payload_to_archive(b"1795343699.5 1\ncpu 0 1\n")
