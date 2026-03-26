import time

import pytest

import hpcperfstats.listend as listend


def test_message_timestamps_window_bounded(monkeypatch):
  # Simulate many messages over a long period of time while advancing the
  # clock. The internal deque should only keep timestamps within the configured
  # MESSAGE_WINDOW_SECONDS window and therefore remain bounded in size.
  base_time = 1_000_000.0

  def _fake_time():
    return base_time + test_message_timestamps_window_bounded.offset

  test_message_timestamps_window_bounded.offset = 0.0
  monkeypatch.setattr(listend.time, "time", _fake_time)

  channel = type(
      "Ch",
      (),
      {
          "basic_ack": lambda self, delivery_tag=None: None,
          "basic_nack": lambda self, delivery_tag=None, requeue=False: None,
      },
  )()

  # Ensure we start from a clean slate for this test.
  listend._message_timestamps.clear()

  # Push messages over several windows worth of time.
  total_messages = 500
  step = listend.MESSAGE_WINDOW_SECONDS / 10.0
  for i in range(total_messages):
    body = f"foo bar host{i}\n".encode()
    listend.on_message(channel, type("M", (), {"delivery_tag": i})(), None, body)
    test_message_timestamps_window_bounded.offset += step

  # The deque should not hold one entry per message; instead it should be
  # limited to roughly the number of messages occurring within the last
  # MESSAGE_WINDOW_SECONDS.
  assert len(listend._message_timestamps) < total_messages / 2

