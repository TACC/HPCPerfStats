import builtins
import itertools

import pytest


@pytest.fixture(autouse=True)
def _remove_listen_lock_after_test():
  yield
  try:
    import os
    import hpcperfstats.listend as listend

    base_dir = os.path.dirname(os.path.realpath(listend.__file__))
    for name in ("listend_lock", "listen_lock"):
      lock_path = os.path.join(base_dir, name)
      if os.path.exists(lock_path):
        os.remove(lock_path)
  except Exception:
    # Best-effort cleanup only; tests should not fail due to lock cleanup.
    pass


class _FakeMethodFrame:
  def __init__(self, delivery_tag=1):
    self.delivery_tag = delivery_tag


class _FakeChannel:
  def __init__(self):
    self.acked = []
    self.nacked = []

  def basic_ack(self, delivery_tag=None):
    self.acked.append(delivery_tag)

  def basic_nack(self, delivery_tag=None, requeue=False):
    self.nacked.append((delivery_tag, requeue))


def test_on_message_acks_on_success(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  channel = _FakeChannel()
  method_frame = _FakeMethodFrame(delivery_tag=123)

  body = b"foo bar myhost baz\n"
  listend.on_message(channel, method_frame, None, body)

  assert channel.acked == [123]
  assert channel.nacked == []
  assert (tmp_path / "myhost" / "current").exists()


def test_on_message_enqueues_recent_host_redis_update(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  captured = []
  monkeypatch.setattr(
      listend,
      "_enqueue_recent_host_update",
      lambda host: captured.append(host),
  )
  channel = _FakeChannel()
  method_frame = _FakeMethodFrame(delivery_tag=222)

  body = b"foo bar node1.example.com baz\n"
  listend.on_message(channel, method_frame, None, body)

  assert channel.acked == [222]
  assert captured == ["node1.example.com"]


def test_on_message_dollar_enqueues_monitor_identity_without_build(
    tmp_path, monkeypatch
):
  """``$`` rotation SETs identity even when ``$build`` is absent (old RPM)."""
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend.time, "time", lambda: 1710000000.0)
  recent = []
  identities = []
  monkeypatch.setattr(
      listend,
      "_enqueue_recent_host_update",
      lambda host: recent.append(host),
  )
  monkeypatch.setattr(
      listend,
      "_enqueue_monitor_identity_update",
      lambda identity: identities.append(identity),
  )
  channel = _FakeChannel()
  body = (
      b"$\n"
      b"1 node1.example.com\n"
      b"$hpcperfstats 3.0\n"
      b"$uname Linux x86_64\n"
      b"!host_cpu user,E\n"
  )
  listend.on_message(channel, _FakeMethodFrame(delivery_tag=333), None, body)

  assert channel.acked == [333]
  assert recent == ["node1.example.com"]
  assert len(identities) == 1
  assert identities[0]["fqdn"] == "node1.example.com"
  assert identities[0]["package_version"] == "3.0"
  assert identities[0]["capability_slug"] is None
  assert identities[0]["schema_types"] == ["host_cpu"]


def test_recent_host_worker_sets_monitor_identity_dict(monkeypatch):
  import hpcperfstats.listend as listend

  class _FakeRedis:
    def __init__(self):
      self.writes = []

    def set(self, key, value, ex=None):
      self.writes.append((key, ex, value))

  fake = _FakeRedis()
  monkeypatch.setattr(listend, "_get_recent_host_redis_client", lambda: fake)
  # Drain one identity item then stop.
  items = [
      {
          "fqdn": "node1.example.com",
          "package_version": "3.0",
          "uname": "Linux",
          "capability_slug": "arch_x86_64",
          "schema_types": ["host_cpu"],
          "updated_at": 1710000000,
      }
  ]

  def _get(timeout=1.0):
    if items:
      return items.pop(0)
    listend._recent_host_worker_stop_event.set()
    raise listend.queue.Empty

  monkeypatch.setattr(listend._recent_host_queue, "get", _get)
  monkeypatch.setattr(listend._recent_host_queue, "task_done", lambda: None)
  listend._recent_host_worker_stop_event.clear()
  listend._recent_host_worker()
  assert fake.writes
  assert fake.writes[0][0] == "monitor_identity:node1.example.com"
  assert fake.writes[0][1] == listend.RECENT_HOST_TTL_SECONDS
  assert b"arch_x86_64" in fake.writes[0][2].encode() or "arch_x86_64" in fake.writes[0][2]


def test_on_message_nacks_and_requeues_on_write_failure(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))

  real_open = builtins.open

  def _failing_open(path, mode="r", *args, **kwargs):
    if str(path).endswith("/current") and "a" in mode:
      raise OSError("disk full")
    return real_open(path, mode, *args, **kwargs)

  monkeypatch.setattr(builtins, "open", _failing_open)

  channel = _FakeChannel()
  method_frame = _FakeMethodFrame(delivery_tag=7)
  body = b"foo bar myhost baz\n"

  listend.on_message(channel, method_frame, None, body)

  assert channel.acked == []
  assert channel.nacked == [(7, True)]


def test_on_message_nacks_and_requeues_on_malformed_message(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  channel = _FakeChannel()
  method_frame = _FakeMethodFrame(delivery_tag=9)

  body = b"only-two-fields\n"
  listend.on_message(channel, method_frame, None, body)

  assert channel.acked == []
  assert channel.nacked == [(9, True)]


def test_set_recent_host_timestamp_writes_expected_redis_key(monkeypatch):
  import hpcperfstats.listend as listend

  class _FakeRedis:
    def __init__(self):
      self.writes = []

    def set(self, key, value, ex=None):
      self.writes.append((key, ex, value))

  monkeypatch.setattr(listend.time, "time", lambda: 1710000000.9)
  fake_redis = _FakeRedis()

  listend._set_recent_host_timestamp(fake_redis, "node1.example.com")

  assert fake_redis.writes == [
      (
          "recent_host:node1.example.com",
          listend.RECENT_HOST_TTL_SECONDS,
          "1710000000",
      )
  ]


def test_on_message_archives_previous_current_on_dollar_switch(
    tmp_path, monkeypatch
):
  import hpcperfstats.listend as listend

  # Keep deterministic, monotonic timestamps while allowing extra time.time()
  # calls from lock wait logic.
  times = itertools.count(1000.1, 0.1)

  def _fake_time():
    return round(next(times), 1)

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend.time, "time", _fake_time)

  # Reset globals that could be affected by previous tests.
  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  channel = _FakeChannel()

  host = "myhost"
  # '$' messages must contain a newline and a host line whose 2nd token is the host.
  msg1 = b"$\n1 " + host.encode("ascii") + b"\nfirst-segment\n"
  msg2 = b"$\n1 " + host.encode("ascii") + b"\nsecond-segment\n"

  method_frame1 = _FakeMethodFrame(delivery_tag=1)
  listend.on_message(channel, method_frame1, None, msg1)

  method_frame2 = _FakeMethodFrame(delivery_tag=2)
  listend.on_message(channel, method_frame2, None, msg2)

  assert channel.acked == [1, 2]
  assert channel.nacked == []

  host_dir = tmp_path / host
  assert host_dir.exists()

  current_contents = (host_dir / "current").read_bytes()
  epoch_files = sorted(p for p in host_dir.iterdir() if p.name.isdigit())
  assert len(epoch_files) == 2
  epoch_contents = [p.read_bytes() for p in epoch_files]

  # After the second '$', 'current' should contain only the second segment,
  # while the first segment should remain archived under its epoch filename.
  assert current_contents == msg2
  assert msg1 in epoch_contents
  assert msg2 in epoch_contents


def test_on_message_counts_current_unlink_on_dollar_switch(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  times = itertools.count(1000.1, 0.1)

  def _fake_time():
    return round(next(times), 1)

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend.time, "time", _fake_time)

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  channel = _FakeChannel()
  host = "myhost"

  # '$' messages must contain a newline and a host line whose 2nd token is the host.
  msg1 = b"$\n1 " + host.encode("ascii") + b"\nfirst-segment\n"
  msg2 = b"$\n1 " + host.encode("ascii") + b"\nsecond-segment\n"

  method_frame1 = _FakeMethodFrame(delivery_tag=1)
  listend.on_message(channel, method_frame1, None, msg1)

  method_frame2 = _FakeMethodFrame(delivery_tag=2)
  listend.on_message(channel, method_frame2, None, msg2)

  # The second '$' should unlink the existing `current` file before rotating.
  assert len(listend._unlink_timestamps) == 1
  assert listend._unlink_timestamps[0] > 1000.0


def test_on_message_hardlinks_missing_epoch_before_unlink(tmp_path, monkeypatch):
  """If `current` exists without an older hardlinked epoch file, listend should create one first."""
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))

  host = "myhost"
  host_dir = tmp_path / host
  host_dir.mkdir()

  # Create a `current` file that contains a parseable first timestamp line,
  # but do NOT hardlink it to any epoch-named file yet.
  first_ts_sec = 1773864970
  old_current = (
      "header-before-first-ts\n"
      "1773864970.470903 2946877 c571-001.stampede3.tacc.utexas.edu\n"
      "rest-of-segment\n"
  )
  (host_dir / "current").write_text(old_current)
  assert not (host_dir / str(first_ts_sec)).exists()

  # Force listend rotation at a later epoch, so `first_ts_sec` is older.
  cutoff_epoch_ts = first_ts_sec + 1
  times = itertools.count(cutoff_epoch_ts + 0.1, 0.1)

  def _fake_time():
    return round(next(times), 1)

  monkeypatch.setattr(listend.time, "time", _fake_time)

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  channel = _FakeChannel()
  msg = b"$\n1 " + host.encode("ascii") + b"\nrotated-segment\n"
  method_frame = _FakeMethodFrame(delivery_tag=3)
  listend.on_message(channel, method_frame, None, msg)

  assert channel.acked == [3]
  assert channel.nacked == []

  # The old `current` inode must now be reachable under `first_ts_sec`.
  assert (host_dir / str(first_ts_sec)).read_bytes() == old_current.encode()

  # After unlink+rotate, `current` must contain only the new segment.
  assert (host_dir / "current").read_bytes() == msg

  # Schema-only `$` has no sample ts → fallback digit name is wall-clock cutoff.
  assert (host_dir / str(cutoff_epoch_ts)).read_bytes() == msg

  # Confirm listend counted exactly one unlink during rotation.
  assert len(listend._unlink_timestamps) == 1
  assert listend._unlink_timestamps[0] > cutoff_epoch_ts


def test_on_message_hardlinks_when_first_ts_epoch_is_other_inode(
    tmp_path, monkeypatch
):
  """$ rotate must ack when first_ts name is a closed different inode.

  Production (hpcperfstats04 2026-08-11): ``Timestamp link path exists but
  is not hardlinked to current`` then nack/requeue.
  """
  import os

  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))

  host = "c104-028.horizon.tacc.utexas.edu"
  host_dir = tmp_path / host
  host_dir.mkdir()

  first_ts_sec = 1786487860
  closed_bytes = b"closed-previous-segment-other-inode\n"
  (host_dir / str(first_ts_sec)).write_bytes(closed_bytes)

  old_current = (
      "header-before-first-ts\n"
      "1786487860.470903 2946877 %s\n"
      "live-segment-body\n"
  ) % host
  (host_dir / "current").write_text(old_current)
  assert not os.path.samefile(
      host_dir / "current", host_dir / str(first_ts_sec)
  )

  cutoff_epoch_ts = first_ts_sec + 100
  times = itertools.count(cutoff_epoch_ts + 0.1, 0.1)

  def _fake_time():
    return round(next(times), 1)

  monkeypatch.setattr(listend.time, "time", _fake_time)

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  channel = _FakeChannel()
  msg = b"$\n1 " + host.encode("ascii") + b"\nrotated-segment\n"
  listend.on_message(channel, _FakeMethodFrame(delivery_tag=11), None, msg)

  assert channel.acked == [11]
  assert channel.nacked == []
  assert (host_dir / str(first_ts_sec)).read_bytes() == closed_bytes

  old_current_bytes = old_current.encode()
  digit_epochs = [
      p for p in host_dir.iterdir() if p.is_file() and p.name.isdigit()
  ]
  preserved = [p for p in digit_epochs if p.read_bytes() == old_current_bytes]
  assert preserved, "old current bytes must remain under a digit epoch name"
  assert not os.path.samefile(preserved[0], host_dir / str(first_ts_sec))

  current_path = host_dir / "current"
  assert current_path.read_bytes() == msg
  current_partners = [
      p
      for p in digit_epochs
      if os.path.samefile(current_path, p)
  ]
  assert current_partners, "new current must have a digit samefile epoch"


def test_on_message_same_second_double_dollar_preserves_closed_epoch(
    tmp_path, monkeypatch
):
  """Two $ rotates in the same unix second must not delete the first inode."""
  import os

  import hpcperfstats.listend as listend

  frozen = 1786488768.4
  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend.time, "time", lambda: frozen)

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  channel = _FakeChannel()
  host = "myhost"
  msg1 = b"$\n1 " + host.encode("ascii") + b"\nfirst-segment\n"
  msg2 = b"$\n1 " + host.encode("ascii") + b"\nsecond-segment\n"

  listend.on_message(channel, _FakeMethodFrame(delivery_tag=1), None, msg1)
  listend.on_message(channel, _FakeMethodFrame(delivery_tag=2), None, msg2)

  assert channel.acked == [1, 2]
  assert channel.nacked == []

  host_dir = tmp_path / host
  current_contents = (host_dir / "current").read_bytes()
  epoch_files = sorted(
      p for p in host_dir.iterdir() if p.is_file() and p.name.isdigit()
  )
  assert len(epoch_files) >= 2
  assert all(int(p.name) >= 1_000_000_000 for p in epoch_files), [
      p.name for p in epoch_files
  ]
  first_epoch = host_dir / "1786488768"
  assert first_epoch.is_file()
  assert first_epoch.read_bytes() == msg1
  epoch_inodes = {p.stat().st_ino for p in epoch_files}
  assert len(epoch_inodes) >= 2
  epoch_contents = [p.read_bytes() for p in epoch_files]
  assert current_contents == msg2
  assert msg1 in epoch_contents
  assert msg2 in epoch_contents
  assert os.path.samefile(host_dir / "current", [
      p for p in epoch_files if p.read_bytes() == msg2
  ][0])
  assert not os.path.samefile(first_epoch, host_dir / "current")


def test_dollar_rotate_digit_epoch_from_first_timestamp(tmp_path, monkeypatch):
  """New ``$`` current digit name uses first sample ts in the file, not wall clock."""
  import os

  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  wall_clock = 1900000000
  monkeypatch.setattr(listend.time, "time", lambda: float(wall_clock))

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  host = "c104-028.horizon.tacc.utexas.edu"
  sample_ts = 1786487860
  msg = (
      "$\n"
      "1 %s\n"
      "!cpu a,E\n"
      "%s.470903 2946877 %s\n"
      "cpu 0 1 2\n"
  ) % (host, sample_ts, host)

  channel = _FakeChannel()
  listend.on_message(
      channel, _FakeMethodFrame(delivery_tag=21), None, msg.encode("ascii")
  )

  assert channel.acked == [21]
  assert channel.nacked == []
  host_dir = tmp_path / host
  current = host_dir / "current"
  digit = host_dir / str(sample_ts)
  assert digit.is_file()
  assert digit.read_text() == msg
  assert os.path.samefile(current, digit)
  assert not (host_dir / str(wall_clock)).exists()


def test_dollar_rotate_digit_epoch_collision_plus_one(tmp_path, monkeypatch):
  """When first-sample digit name exists, new ``$`` current uses sample_ts + 1."""
  import os

  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  wall_clock = 1900000000
  monkeypatch.setattr(listend.time, "time", lambda: float(wall_clock))

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._last_message_time = None

  host = "c104-028.horizon.tacc.utexas.edu"
  sample_ts = 1786487860
  host_dir = tmp_path / host
  host_dir.mkdir()
  (host_dir / str(sample_ts)).write_text("occupied-other-inode\n")

  msg = (
      "$\n"
      "1 %s\n"
      "!cpu a,E\n"
      "%s.470903 2946877 %s\n"
      "cpu 0 1 2\n"
  ) % (host, sample_ts, host)

  channel = _FakeChannel()
  listend.on_message(
      channel, _FakeMethodFrame(delivery_tag=22), None, msg.encode("ascii")
  )

  assert channel.acked == [22]
  assert channel.nacked == []
  current = host_dir / "current"
  conflicted = host_dir / str(sample_ts)
  stepped = host_dir / str(sample_ts + 1)
  assert conflicted.read_text() == "occupied-other-inode\n"
  assert stepped.is_file()
  assert stepped.read_text() == msg
  assert os.path.samefile(current, stepped)
  assert not os.path.samefile(current, conflicted)
  assert not (host_dir / str(wall_clock)).exists()


def test_get_first_timestamp_seconds_skips_dollar_host_line(tmp_path):
  """Schema host line ``1 <fqdn>`` is not a unix-second timestamp."""
  import hpcperfstats.listend as listend

  dollar_only = tmp_path / "dollar_only"
  dollar_only.write_text("$\n1 c104-028.horizon.tacc.utexas.edu\n!cpu a,E\n")
  assert listend._get_first_timestamp_seconds(str(dollar_only), use_lock=False) is None

  mixed = tmp_path / "mixed"
  mixed.write_text(
      "$\n"
      "1 c104-028.horizon.tacc.utexas.edu\n"
      "!cpu a,E\n"
      "1786487860.470903 2946877 c104-028.horizon.tacc.utexas.edu\n"
      "cpu 0 1 2\n"
  )
  assert listend._get_first_timestamp_seconds(str(mixed), use_lock=False) == 1786487860


class _FakeConn:
  def __init__(self):
    self.is_closed = False
    self.close_calls = 0

  def close(self):
    self.close_calls += 1
    self.is_closed = True


class _ChannelClosedOnAck(_FakeChannel):
  """Channel that dies on ack (production Channel is closed storm)."""

  def __init__(self):
    super().__init__()
    self.stop_calls = 0
    self.connection = _FakeConn()
    self.is_closed = False

  def basic_ack(self, delivery_tag=None):
    raise Exception("Channel is closed.")

  def basic_nack(self, delivery_tag=None, requeue=False):
    raise Exception("Channel is closed.")

  def stop_consuming(self):
    self.stop_calls += 1


def test_is_amqp_channel_or_connection_dead_detects_channel_closed_message():
  import hpcperfstats.listend as listend

  assert listend._is_amqp_channel_or_connection_dead(
      Exception("Channel is closed."), None
  )
  assert listend._is_amqp_channel_or_connection_dead(
      Exception("Connection is closed"), None
  )
  assert not listend._is_amqp_channel_or_connection_dead(
      OSError("disk full"), None
  )


def test_on_message_channel_closed_on_ack_requests_reconnect_once(
    tmp_path, monkeypatch
):
  """Dead channel on ack must stop consume and request full reconnect once."""
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  listend._amqp_reconnect_requested = False
  logs = []
  monkeypatch.setattr(listend, "log_print", lambda msg: logs.append(msg))

  channel = _ChannelClosedOnAck()
  body = b"foo bar myhost baz\n"
  listend.on_message(channel, _FakeMethodFrame(delivery_tag=55), None, body)

  assert listend._amqp_reconnect_requested is True
  assert channel.stop_calls == 1
  assert channel.connection.close_calls == 1
  assert channel.acked == []
  assert channel.nacked == []
  reconnect_logs = [m for m in logs if "AMQP reconnect" in m or "Channel is closed" in m]
  assert len(reconnect_logs) == 1
  assert "Error processing message; leaving on server" not in "\n".join(logs)

  # Second dead-channel callback must not re-log / re-stop storm.
  listend.on_message(channel, _FakeMethodFrame(delivery_tag=56), None, body)
  assert channel.stop_calls == 1
  reconnect_logs2 = [m for m in logs if "AMQP reconnect" in m]
  assert len(reconnect_logs2) == 1

  listend._amqp_reconnect_requested = False


def test_on_message_write_failure_nacks_without_amqp_reconnect(
    tmp_path, monkeypatch
):
  """Archive I/O failure keeps nack+requeue; must not set reconnect flag."""
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  listend._amqp_reconnect_requested = False

  real_open = builtins.open

  def _failing_open(path, mode="r", *args, **kwargs):
    if str(path).endswith("/current") and "a" in mode:
      raise OSError("disk full")
    return real_open(path, mode, *args, **kwargs)

  monkeypatch.setattr(builtins, "open", _failing_open)
  channel = _FakeChannel()
  channel.stop_consuming = lambda: (_ for _ in ()).throw(
      AssertionError("stop_consuming must not run on archive IOError")
  )
  listend.on_message(
      channel, _FakeMethodFrame(delivery_tag=8), None, b"foo bar myhost baz\n"
  )
  assert listend._amqp_reconnect_requested is False
  assert channel.nacked == [(8, True)]


def test_db_backpressure_pause_does_not_set_amqp_reconnect(monkeypatch):
  """Pause path must stop consume without full AMQP reconnect flag."""
  import hpcperfstats.listend as listend

  listend._amqp_reconnect_requested = False
  listend._db_backpressure_pause = False

  class _Pool:
    def note_pause_enter(self):
      return None

  monkeypatch.setattr(
      listend, "_live_db_ingest_pool_active", lambda: _Pool()
  )
  channel = _FakeChannel()
  channel.stop_consuming = lambda: setattr(channel, "stopped", True)
  listend._request_db_backpressure_pause(channel, 1)
  assert listend._db_backpressure_pause is True
  assert listend._amqp_reconnect_requested is False
  listend._db_backpressure_pause = False



def test_listend_amqp_prefetch_defaults():
  import hpcperfstats.dbload.lib.conf_parser as cfg

  assert cfg.get_listend_archive_worker_threads() >= 1
  assert cfg.get_listend_amqp_prefetch() >= 1
  # Registry defaults.
  assert cfg.INI_OPTION_DEFAULTS["listend_archive_worker_threads"] == "8"
  assert cfg.INI_OPTION_DEFAULTS["listend_amqp_prefetch"] == "32"


def test_drop_mode_uses_ini_prefetch_not_only_pause(monkeypatch):
  """Documented contract: drop mode applies get_listend_amqp_prefetch()."""
  import inspect
  import hpcperfstats.listend as listend

  src = inspect.getsource(listend.main)
  assert "get_listend_amqp_prefetch" in src
  assert "prefetch_count=1" in src
