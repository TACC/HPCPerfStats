import builtins

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


def test_on_message_archives_previous_current_on_dollar_switch(
    tmp_path, monkeypatch
):
  import hpcperfstats.listend as listend

  # Make timestamps deterministic so we can assert on epoch filenames.
  # on_message() calls time.time() twice for '$' messages:
  # 1) when creating the epoch link
  # 2) when updating the in-memory timestamp deque
  times = iter([1000.1, 1000.2, 1001.1, 1001.2])

  def _fake_time():
    return next(times)

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend.time, "time", _fake_time)

  # Reset globals that could be affected by previous tests.
  with listend._timestamps_lock:
    listend._message_timestamps.clear()
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
  epoch_1000_contents = (host_dir / "1000").read_bytes()
  epoch_1001_contents = (host_dir / "1001").read_bytes()

  # After the second '$', 'current' should contain only the second segment,
  # while the first segment should remain archived under its epoch filename.
  assert current_contents == msg2
  assert epoch_1000_contents == msg1
  assert epoch_1001_contents == msg2

