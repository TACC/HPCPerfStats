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

