


class _FakeQueueMethod:
  def __init__(self, message_count):
    self.message_count = message_count


class _FakeQueue:
  def __init__(self, message_count):
    self.method = _FakeQueueMethod(message_count)


def _management_http_unavailable(monkeypatch):
  def _boom(*_a, **_k):
    raise OSError("management unavailable")

  monkeypatch.setattr("urllib.request.urlopen", _boom)


def test_get_rmq_queue_depth_uses_management_messages_ready_when_amqp_is_zero(
    monkeypatch,
):
  """Quorum AMQP message_count is 0; waiting must match rabbitmqctl ready."""
  import json

  import hpcperfstats.listend as listend

  captured = {}

  class _HttpResp:
    def read(self):
      return json.dumps(
          {
              "messages_ready": 126034,
              "messages_unacknowledged": 768,
              "messages": 126802,
          }
      ).encode()

    def __enter__(self):
      return self

    def __exit__(self, *_exc):
      return False

  def _urlopen(req, timeout=None):
    captured["url"] = getattr(req, "full_url", str(req))
    captured["timeout"] = timeout
    return _HttpResp()

  def _amqp_must_not_run(_p):
    raise AssertionError(
        "AMQP declare must not hide management messages_ready"
    )

  monkeypatch.setattr("urllib.request.urlopen", _urlopen)
  monkeypatch.setattr(listend.pika, "BlockingConnection", _amqp_must_not_run)
  monkeypatch.setattr(
      listend,
      "listend_amqp_connection_parameters",
      lambda _h: object(),
  )
  monkeypatch.setattr(listend.cfg, "get_rmq_server", lambda: "rabbitmq")
  monkeypatch.setattr(listend.cfg, "get_rmq_queue", lambda: "stampede3")

  assert listend._get_rmq_queue_depth_for_monitor() == 126034
  assert "/api/queues/%2F/stampede3" in captured["url"]


def test_get_rmq_queue_depth_for_monitor_passive_ok(monkeypatch):
  import hpcperfstats.listend as listend

  _management_http_unavailable(monkeypatch)
  declare_calls = []

  class _FakeChannel:
    def queue_declare(self, queue=None, durable=None, passive=None):
      _ = durable
      declare_calls.append(passive)
      return _FakeQueue(99)

  class _FakeConnection:
    is_closed = False

    def channel(self):
      return _FakeChannel()

    def close(self):
      self.is_closed = True

  monkeypatch.setattr(
      listend.pika, "BlockingConnection", lambda _p: _FakeConnection()
  )
  monkeypatch.setattr(
      listend,
      "listend_amqp_connection_parameters",
      lambda _h: object(),
  )

  assert listend._get_rmq_queue_depth_for_monitor() == 99
  assert declare_calls == [True]


def test_get_rmq_queue_depth_for_monitor_passive_only_returns_na_on_fail(
    monkeypatch,
):
  """Idle depth must not non-passive-declare; probe failure is n/a not 0."""
  import hpcperfstats.listend as listend

  _management_http_unavailable(monkeypatch)
  declare_calls = []

  class _FakeChannel:
    def queue_declare(self, queue=None, durable=None, passive=None):
      _ = durable
      declare_calls.append(passive)
      raise RuntimeError("queue not found (simulated)")

  class _FakeConnection:
    is_closed = False

    def channel(self):
      return _FakeChannel()

    def close(self):
      self.is_closed = True

  monkeypatch.setattr(
      listend.pika, "BlockingConnection", lambda _p: _FakeConnection()
  )
  monkeypatch.setattr(
      listend,
      "listend_amqp_connection_parameters",
      lambda _h: object(),
  )

  assert listend._get_rmq_queue_depth_for_monitor() == "n/a"
  assert declare_calls == [True]


def test_get_rmq_queue_depth_for_monitor_returns_na_when_connect_fails(monkeypatch):
  import hpcperfstats.listend as listend

  _management_http_unavailable(monkeypatch)

  def _boom(_p):
    raise OSError("no broker")

  monkeypatch.setattr(listend.pika, "BlockingConnection", _boom)
  monkeypatch.setattr(
      listend,
      "listend_amqp_connection_parameters",
      lambda _h: object(),
  )

  assert listend._get_rmq_queue_depth_for_monitor() == "n/a"


def test_idle_monitor_reports_real_queue_depth(monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend, "_get_rmq_queue_depth_for_monitor", lambda: 42)
  monkeypatch.setattr(listend, "_format_listend_idle_archive_suffix", lambda: "")

  now = 1_000_000.0

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._message_timestamps.append(now - 10)
    listend._message_timestamps.append(now - 5)
    listend._message_timestamps.append(
        now - listend.MESSAGE_WINDOW_SECONDS - 1
    )
    listend._unlink_timestamps.clear()
    listend._unlink_timestamps.append(now - 10)
    listend._unlink_timestamps.append(now - 5)

  listend._last_idle_report_time = now - listend.MESSAGE_WINDOW_SECONDS - 1
  messages = []
  monkeypatch.setattr(listend, "log_print", messages.append)

  listend._emit_idle_monitor_report(now)

  assert any("messages waiting to be consumed: 42" in m for m in messages)
  assert any("current file unlinks (last 10 minutes): 2" in m for m in messages)
  assert any(
      "Messages consumed in the last 10 minutes: 2;" in m for m in messages
  )
  assert not any("bytes)" in m for m in messages)

  messages.clear()
  listend._emit_idle_monitor_report(now + 1)
  assert messages == []


def test_consume_window_totals_counts_in_window_messages():
  import hpcperfstats.listend as listend

  now = 2_000_000.0
  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
    listend._message_timestamps.append(now - 1)
    listend._message_timestamps.append(now - 2)
    listend._message_timestamps.append(
        now - listend.MESSAGE_WINDOW_SECONDS - 5
    )
    listend._unlink_timestamps.append(now - 1)

  messages, unlinks = listend._consume_window_totals(now)
  assert messages == 2
  assert unlinks == 1

  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()
  messages, unlinks = listend._consume_window_totals(now)
  assert (messages, unlinks) == (0, 0)


def test_append_records_timestamp_not_payload_bytes(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  with listend._timestamps_lock:
    listend._message_timestamps.clear()
    listend._unlink_timestamps.clear()

  body = b"1710000001.0 1 bytehost.example.com extra\n"
  listend.append_monitor_payload_to_archive(body)

  now = listend.time.time()
  messages, unlinks = listend._consume_window_totals(now)
  assert messages == 1
  assert unlinks == 0
  with listend._timestamps_lock:
    entry = listend._message_timestamps[-1]
    assert isinstance(entry, float)
    assert not isinstance(entry, tuple)
