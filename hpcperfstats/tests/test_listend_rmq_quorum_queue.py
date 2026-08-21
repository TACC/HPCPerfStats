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


def test_classify_amqp_outer_error_logs_establishing_for_tcp(monkeypatch):
  import hpcperfstats.listend as listend

  logs = []
  monkeypatch.setattr(listend, "log_print", lambda m: logs.append(m))
  kind = listend._log_amqp_outer_loop_error(
      Exception("Timeout during AMQP handshake")
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