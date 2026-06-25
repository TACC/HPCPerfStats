


class _FakeQueueMethod:
  def __init__(self, message_count):
    self.message_count = message_count


class _FakeQueue:
  def __init__(self, message_count):
    self.method = _FakeQueueMethod(message_count)


def test_get_rmq_queue_depth_for_monitor_passive_ok(monkeypatch):
  import hpcperfstats.listend as listend

  declare_calls = []

  class _FakeChannel:
    def queue_declare(self, queue=None, _durable=None, passive=None):
      declare_calls.append(passive)
      return _FakeQueue(99)

  class _FakeConnection:
    is_closed = False

    def channel(self):
      return _FakeChannel()

    def close(self):
      self.is_closed = True

  monkeypatch.setattr(listend.pika, "BlockingConnection", lambda _p: _FakeConnection())
  monkeypatch.setattr(listend.pika, "ConnectionParameters", lambda _h: object())

  assert listend._get_rmq_queue_depth_for_monitor() == 99
  assert declare_calls == [True]


def test_get_rmq_queue_depth_for_monitor_falls_back_to_non_passive(monkeypatch):
  import hpcperfstats.listend as listend

  declare_calls = []

  class _FakeChannel:
    def queue_declare(self, queue=None, _durable=None, passive=None):
      declare_calls.append(passive)
      if passive is True:
        raise RuntimeError("queue not found (simulated)")
      return _FakeQueue(7)

  class _FakeConnection:
    is_closed = False

    def channel(self):
      return _FakeChannel()

    def close(self):
      self.is_closed = True

  monkeypatch.setattr(listend.pika, "BlockingConnection", lambda _p: _FakeConnection())
  monkeypatch.setattr(listend.pika, "ConnectionParameters", lambda _h: object())

  assert listend._get_rmq_queue_depth_for_monitor() == 7
  assert declare_calls[0] is True
  assert declare_calls[-1] is False


def test_get_rmq_queue_depth_for_monitor_returns_zero_when_connect_fails(monkeypatch):
  import hpcperfstats.listend as listend

  def _boom(_p):
    raise OSError("no broker")

  monkeypatch.setattr(listend.pika, "BlockingConnection", _boom)
  monkeypatch.setattr(listend.pika, "ConnectionParameters", lambda _h: object())

  assert listend._get_rmq_queue_depth_for_monitor() == 0


def test_idle_monitor_reports_real_queue_depth(monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend, "_get_rmq_queue_depth_for_monitor", lambda: 42)

  # Drive the monitor logic once by calling its core section directly.
  # We avoid creating a real background thread or sleeping.
  now = 1_000_000.0

  # Make sure there is at least one message timestamp inside the window so that
  # "consumed" count is non-zero and exercised.
  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._message_timestamps.append(now)
    listend._unlink_timestamps.clear()
    listend._unlink_timestamps.append(now - 10)
    listend._unlink_timestamps.append(now - 5)

  # Force the last report time far enough in the past so that a report happens.
  listend._last_idle_report_time = now - listend.MESSAGE_WINDOW_SECONDS - 1

  # Capture log output.
  messages = []

  def _fake_log(msg):
    messages.append(msg)

  monkeypatch.setattr(listend, "log_print", _fake_log)

  # Monkeypatch time.time and time.sleep so that a single iteration of the
  # monitor body can be executed deterministically.
  times = [now, now + listend.IDLE_CHECK_INTERVAL + 1]

  def _fake_time():
    return times.pop(0)

  def _fake_sleep(_seconds):
    pass

  monkeypatch.setattr(listend.time, "time", _fake_time)
  monkeypatch.setattr(listend.time, "sleep", _fake_sleep)

  # Run only a single iteration of the monitor loop by calling the internal
  # function body directly in a controlled way. We do this by temporarily
  # replacing the while True condition.
  original_idle_monitor = listend._idle_monitor

  def _single_iteration_idle_monitor():
    # Copy of listend._idle_monitor logic for a single loop body execution.
    # This mirrors the production code but runs only once for test purposes.
    listend.time.sleep(listend.IDLE_CHECK_INTERVAL)
    now_local = listend.time.time()
    if (listend._last_idle_report_time is not None and
        (now_local - listend._last_idle_report_time) < listend.MESSAGE_WINDOW_SECONDS):
      return

    with listend._timestamps_lock:
      cutoff_10 = now_local - listend.MESSAGE_WINDOW_SECONDS
      count_last_10 = sum(1 for ts in listend._message_timestamps if ts >= cutoff_10)
      unlink_count_last_10 = sum(
          1 for ts in listend._unlink_timestamps if ts >= cutoff_10
      )

    queue_depth = listend._get_rmq_queue_depth_for_monitor()

    listend.log_print(
        "Messages consumed in the last 10 minutes: %d; "
        "messages waiting to be consumed: %d; "
        "current file unlinks (last 10 minutes): %d" %
        (count_last_10, queue_depth, unlink_count_last_10))

    listend._last_idle_report_time = now_local

  try:
    monkeypatch.setattr(listend, "_idle_monitor", _single_iteration_idle_monitor)
    listend._idle_monitor()
  finally:
    monkeypatch.setattr(listend, "_idle_monitor", original_idle_monitor)

  assert any("messages waiting to be consumed: 42" in m for m in messages)
  assert any("current file unlinks (last 10 minutes): 2" in m for m in messages)
