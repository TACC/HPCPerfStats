import hpcperfstats.listend as listend


def test_message_timestamps_window_bounded(monkeypatch):
  # Across several windows the deque keeps only in-window timestamps.
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

  listend._message_timestamps.clear()

  total_messages = 500
  step = listend.MESSAGE_WINDOW_SECONDS / 10.0
  for i in range(total_messages):
    body = f"foo bar host{i}\n".encode()
    listend.on_message(
      channel, type("M", (), {"delivery_tag": i})(), None, body
    )
    test_message_timestamps_window_bounded.offset += step

  assert len(listend._message_timestamps) < total_messages / 2
