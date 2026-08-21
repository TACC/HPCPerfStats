"""
RabbitMQ durable quorum queue helpers for listend / listend_drain.

Classic queues OOM under thousands of monitor publisher connections; compose
defaults new queues to quorum. Clients must declare ``x-queue-type=quorum`` on
non-passive declares so arguments match an existing quorum queue.

Attributes:
  QUORUM_QUEUE_TYPE: AMQP ``x-queue-type`` value (``quorum``).
  LISTEND_AMQP_HEARTBEAT_SECONDS: Pika heartbeat interval for listend paths.
  LISTEND_AMQP_BLOCKED_CONNECTION_TIMEOUT_SECONDS: Pika blocked-connection
    timeout.
  AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS: First reconnect sleep after 541.
  AMQP_RECONNECT_BACKOFF_CAP_SECONDS: Max exponential reconnect sleep.
"""
from __future__ import annotations

from typing import Any

import pika

QUORUM_QUEUE_TYPE = "quorum"
LISTEND_AMQP_HEARTBEAT_SECONDS = 60
LISTEND_AMQP_BLOCKED_CONNECTION_TIMEOUT_SECONDS = 300
AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS = 5
AMQP_RECONNECT_BACKOFF_CAP_SECONDS = 60


class QuorumQueuePreconditionError(RuntimeError):
  """Raised when declare quorum args conflict with an existing classic queue."""


def declare_durable_quorum_queue(channel: Any, queue_name: str) -> Any:
  """
  Declare a durable quorum queue (non-passive).

  Args:
    channel (Any): Open pika channel.
    queue_name (str): Queue name (typically ``cfg.get_rmq_queue()``).

  Returns:
    Any: The pika ``queue_declare`` method frame / OK payload for the queue.

  Raises:
    QuorumQueuePreconditionError: When the broker rejects quorum args because
      an existing queue has a different type (for example classic). Do not
      delete/recreate as classic from listend; recreate as quorum offline.
    Exception: Propagates other broker/channel failures from
      ``channel.queue_declare`` unchanged.

  Examples:
    >>> class _Ch:
    ...     def queue_declare(self, **kwargs):
    ...         return kwargs
    >>> declare_durable_quorum_queue(_Ch(), "stampede3")["arguments"]
    {'x-queue-type': 'quorum'}
  """
  try:
    return channel.queue_declare(
        queue=queue_name,
        durable=True,
        arguments={"x-queue-type": QUORUM_QUEUE_TYPE},
    )
  except Exception as exc:
    msg = str(exc).lower()
    reply = getattr(exc, "reply_code", None)
    args = getattr(exc, "args", ())
    code = reply if reply is not None else (args[0] if args else None)
    if code == 406 or "precondition_failed" in msg or "inequivalent arg" in msg:
      raise QuorumQueuePreconditionError(
          "Queue %r exists with incompatible type; declare requires "
          "x-queue-type=quorum. Do not convert to classic (classic OOMs under "
          "many monitor connections). Recreate the queue as quorum offline, "
          "then restart listend. Broker error: %s" % (queue_name, exc)
      ) from exc
    raise


def listend_amqp_connection_parameters(host: str) -> pika.ConnectionParameters:
  """
  Build BlockingConnection parameters with heartbeat and blocked timeout.

  Args:
    host (str): RabbitMQ hostname (compose service name or DNS).

  Returns:
    pika.ConnectionParameters: Parameters for listend / drain / idle depth.

  Examples:
    >>> p = listend_amqp_connection_parameters("rabbitmq")
    >>> p.host
    'rabbitmq'
    >>> p.heartbeat
    60
  """
  return pika.ConnectionParameters(
      host,
      heartbeat=LISTEND_AMQP_HEARTBEAT_SECONDS,
      blocked_connection_timeout=LISTEND_AMQP_BLOCKED_CONNECTION_TIMEOUT_SECONDS,
  )


def is_quorum_consume_setup_error(exc: BaseException) -> bool:
  """
  Return True when ``exc`` is a quorum consume-setup / Ra checkout failure.

  Matches broker ``INTERNAL_ERROR`` 541 text (timed out consuming from quorum
  queue) and pika objects that expose ``reply_code == 541``.

  Args:
    exc (BaseException): Exception from connect, declare, or ``basic_consume``.

  Returns:
    bool: ``True`` when exponential reconnect backoff should apply.

  Examples:
    >>> is_quorum_consume_setup_error(
    ...     Exception(
    ...         "INTERNAL_ERROR - timed out consuming from quorum queue "
    ...         "'stampede3'"
    ...     )
    ... )
    True
    >>> is_quorum_consume_setup_error(OSError("disk full"))
    False
  """
  reply = getattr(exc, "reply_code", None)
  if reply == 541:
    return True
  args = getattr(exc, "args", ())
  if args and args[0] == 541:
    return True
  msg = str(exc).lower()
  return "timed out consuming from quorum" in msg or (
      "541" in msg and "quorum" in msg
  )


def next_amqp_reconnect_backoff_seconds(current: int) -> int:
  """
  Double reconnect sleep, capped at ``AMQP_RECONNECT_BACKOFF_CAP_SECONDS``.

  Args:
    current (int): Previous sleep seconds (typically 5 after a successful
      consume attach).

  Returns:
    int: Next sleep duration in seconds.

  Examples:
    >>> next_amqp_reconnect_backoff_seconds(5)
    10
    >>> next_amqp_reconnect_backoff_seconds(60)
    60
  """
  if current < AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS:
    current = AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS
  nxt = min(current * 2, AMQP_RECONNECT_BACKOFF_CAP_SECONDS)
  return int(nxt)
