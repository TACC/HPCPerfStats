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

  # And the new `current` should be hardlinked under the cutoff epoch filename.
  assert (host_dir / str(cutoff_epoch_ts)).read_bytes() == msg

  # Confirm listend counted exactly one unlink during rotation.
  assert len(listend._unlink_timestamps) == 1
  assert listend._unlink_timestamps[0] > cutoff_epoch_ts

