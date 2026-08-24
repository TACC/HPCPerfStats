"""Regression tests for listend quorum declare / consume-setup helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_declare_durable_quorum_queue_passes_x_queue_type():
  from hpcperfstats.lib.rmq_quorum_queue import declare_durable_quorum_queue

  channel = MagicMock()
  declare_durable_quorum_queue(channel, "stampede3")
  channel.queue_declare.assert_called_once_with(
      queue="stampede3",
      durable=True,
      arguments={"x-queue-type": "quorum"},
  )


def test_is_quorum_consume_setup_error_detects_541_text():
  from hpcperfstats.lib.rmq_quorum_queue import is_quorum_consume_setup_error

  exc = Exception(
      "(541, \"INTERNAL_ERROR - timed out consuming from quorum queue "
      "'stampede3' in vhost '/': {'%2F_stampede3', 'rabbit@rabbitmq-prod'}\")"
  )
  assert is_quorum_consume_setup_error(exc) is True
  assert is_quorum_consume_setup_error(OSError("disk full")) is False


def test_is_quorum_consume_setup_error_detects_reply_code():
  from hpcperfstats.lib.rmq_quorum_queue import is_quorum_consume_setup_error

  class _E(Exception):
    reply_code = 541

  assert is_quorum_consume_setup_error(_E("x")) is True


def test_next_amqp_reconnect_backoff_seconds_grows_and_caps():
  from hpcperfstats.lib.rmq_quorum_queue import (
      next_amqp_reconnect_backoff_seconds,
  )

  assert next_amqp_reconnect_backoff_seconds(5) == 10
  assert next_amqp_reconnect_backoff_seconds(10) == 20
  assert next_amqp_reconnect_backoff_seconds(40) == 60
  assert next_amqp_reconnect_backoff_seconds(60) == 60


def test_listend_amqp_connection_parameters_sets_heartbeat():
  from hpcperfstats.lib.rmq_quorum_queue import (
      LISTEND_AMQP_HEARTBEAT_SECONDS,
      listend_amqp_connection_parameters,
  )

  p = listend_amqp_connection_parameters("rabbitmq")
  assert p.host == "rabbitmq"
  assert p.heartbeat == LISTEND_AMQP_HEARTBEAT_SECONDS


def test_bind_consume_skips_cancel_on_fresh_channel(monkeypatch):
  """Fresh channel must not cancel before the first basic_consume."""
  import hpcperfstats.listend as listend

  channel = MagicMock()
  cancel_calls = []
  channel.cancel = lambda: cancel_calls.append(1)
  channel.basic_consume = MagicMock()

  listend._bind_listend_consume(channel, "stampede3", had_consumer=False)
  assert cancel_calls == []
  channel.basic_consume.assert_called_once_with("stampede3", listend.on_message)

  listend._bind_listend_consume(channel, "stampede3", had_consumer=True)
  assert cancel_calls == [1]


def test_classify_amqp_outer_error_logs_consume_setup_for_541(monkeypatch):
  import hpcperfstats.listend as listend

  logs = []
  monkeypatch.setattr(listend, "log_print", lambda m: logs.append(m))
  exc = Exception(
      "(541, \"INTERNAL_ERROR - timed out consuming from quorum queue "
      "'stampede3'\")"
  )
  kind = listend._log_amqp_outer_loop_error(exc)
  assert kind == "quorum_consume_setup"
  assert any("quorum consume-setup" in m for m in logs)
  assert not any("Error establishing RabbitMQ connection" in m for m in logs)


def test_is_quorum_consume_setup_error_detects_bare_541_internal_error():
  from hpcperfstats.lib.rmq_quorum_queue import is_quorum_consume_setup_error

  assert is_quorum_consume_setup_error(Exception("(541, 'INTERNAL_ERROR')")) is True

  class _TupleExc(Exception):
    def __init__(self) -> None:
      super().__init__(541, "INTERNAL_ERROR")

  assert is_quorum_consume_setup_error(_TupleExc()) is True


def test_is_amqp_peer_reset_reconnect_error_detects_connection_reset():
  from hpcperfstats.lib.rmq_quorum_queue import is_amqp_peer_reset_reconnect_error

  assert is_amqp_peer_reset_reconnect_error(
      ConnectionResetError(104, "Connection reset by peer")
  ) is True
  assert is_amqp_peer_reset_reconnect_error(
      Exception("Timeout during AMQP handshake")
  ) is True
  assert is_amqp_peer_reset_reconnect_error(OSError("disk full")) is False


def test_should_use_amqp_exponential_reconnect_backoff_peer_reset():
  from hpcperfstats.lib.rmq_quorum_queue import (
      should_use_amqp_exponential_reconnect_backoff,
  )

  assert should_use_amqp_exponential_reconnect_backoff(
      ConnectionResetError(104, "reset")
  ) is True


def test_apply_amqp_reconnect_backoff_grows(monkeypatch):
  import hpcperfstats.listend as listend

  listend._amqp_reconnect_backoff_seconds = 5
  assert listend._apply_amqp_reconnect_backoff() == 10
  assert listend._amqp_reconnect_backoff_seconds == 10
  assert listend._apply_amqp_reconnect_backoff() == 20


def test_maybe_reset_backoff_after_stable_consume(monkeypatch):
  import hpcperfstats.listend as listend

  listend._amqp_reconnect_backoff_seconds = 40
  listend._consume_attach_monotonic = 1000.0
  monkeypatch.setattr(listend.time, "monotonic", lambda: 1031.0)
  listend._maybe_reset_amqp_reconnect_backoff_after_stable_consume()
  assert listend._amqp_reconnect_backoff_seconds == 5

  listend._amqp_reconnect_backoff_seconds = 40
  listend._consume_attach_monotonic = 1000.0
  monkeypatch.setattr(listend.time, "monotonic", lambda: 1010.0)
  listend._maybe_reset_amqp_reconnect_backoff_after_stable_consume()
  assert listend._amqp_reconnect_backoff_seconds == 40


def test_request_amqp_full_reconnect_closes_channel_then_connection(monkeypatch):
  import hpcperfstats.listend as listend

  calls = []

  def _fake_close(channel, connection, *, stop_consuming=False):
    calls.append(("close", stop_consuming, channel, connection))

  monkeypatch.setattr(
      listend, "_close_amqp_channel_and_connection_gracefully", _fake_close
  )
  monkeypatch.setattr(listend, "log_print", lambda _m: None)

  channel = MagicMock()
  conn = MagicMock()
  channel.connection = conn
  listend._amqp_reconnect_requested = False
  listend._request_amqp_full_reconnect(channel, "Channel is closed.")
  assert calls == [("close", True, channel, conn)]


def test_classify_amqp_outer_error_logs_peer_reset(monkeypatch):
  import hpcperfstats.listend as listend

  logs = []
  monkeypatch.setattr(listend, "log_print", lambda m: logs.append(m))
  kind = listend._log_amqp_outer_loop_error(
      ConnectionResetError(104, "Connection reset by peer")
  )
  assert kind == "peer_reset"
  assert any("peer reset" in m.lower() for m in logs)


def test_classify_amqp_outer_error_logs_handshake_as_peer_reset(monkeypatch):
  import hpcperfstats.listend as listend

  logs = []
  monkeypatch.setattr(listend, "log_print", lambda m: logs.append(m))
  kind = listend._log_amqp_outer_loop_error(
      Exception("Timeout during AMQP handshake")
  )
  assert kind == "peer_reset"
  assert any("peer reset" in m.lower() for m in logs)


def test_classify_amqp_outer_error_logs_establishing_for_tcp(monkeypatch):
  import hpcperfstats.listend as listend

  logs = []
  monkeypatch.setattr(listend, "log_print", lambda m: logs.append(m))
  kind = listend._log_amqp_outer_loop_error(
      Exception("Connection refused")
  )
  assert kind == "connection"
  assert any("Error establishing RabbitMQ connection" in m for m in logs)


def test_declare_quorum_on_classic_raises_precondition_error():
  from hpcperfstats.lib.rmq_quorum_queue import (
      QuorumQueuePreconditionError,
      declare_durable_quorum_queue,
  )

  class _Ch:
    def queue_declare(self, **_kwargs):
      raise Exception(
          "(406, \"PRECONDITION_FAILED - inequivalent arg 'x-queue-type' "
          "for queue 'stampede3'\")"
      )

  with pytest.raises(QuorumQueuePreconditionError) as ei:
    declare_durable_quorum_queue(_Ch(), "stampede3")
  assert "Do not convert to classic" in str(ei.value)


def test_drain_declares_quorum_queue(monkeypatch):
  from unittest.mock import MagicMock, patch

  channel = MagicMock()
  connection = MagicMock()
  connection.is_closed = False
  connection.channel.return_value = channel
  channel.basic_get.return_value = (None, None, None)

  with (
      patch(
          "hpcperfstats.listend_drain.pika.BlockingConnection",
          return_value=connection,
      ),
      patch(
          "hpcperfstats.listend_drain.cfg.get_rmq_queue",
          return_value="test-q",
      ),
      patch(
          "hpcperfstats.listend_drain.cfg.get_rmq_server",
          return_value="localhost",
      ),
      patch(
          "hpcperfstats.listend_drain.append_monitor_payload_to_archive",
      ),
  ):
    from hpcperfstats.listend_drain import drain_queue_to_archive

    assert drain_queue_to_archive() == 0
  channel.queue_declare.assert_called_with(
      queue="test-q",
      durable=True,
      arguments={"x-queue-type": "quorum"},
  )