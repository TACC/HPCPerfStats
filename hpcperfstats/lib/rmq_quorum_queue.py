"""
RabbitMQ durable quorum queue helpers for listend / listend_drain.

Classic queues OOM under thousands of monitor publisher connections; compose
defaults new queues to quorum. listend attaches to an existing queue of any
type (passive declare) and only sends ``x-queue-type=quorum`` when creating a
missing queue. Sending quorum args against an existing classic queue is 406
and closes the AMQP channel.

Attributes:
  QUORUM_QUEUE_TYPE: AMQP ``x-queue-type`` value (``quorum``).
  LISTEND_AMQP_HEARTBEAT_SECONDS: Pika heartbeat interval for listend paths.
  LISTEND_AMQP_BLOCKED_CONNECTION_TIMEOUT_SECONDS: Pika blocked-connection
    timeout.
  AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS: First reconnect sleep after 541.
  AMQP_RECONNECT_BACKOFF_CAP_SECONDS: Max exponential reconnect sleep.
  AMQP_RECONNECT_STABLE_CONSUME_SECONDS: Consume duration before backoff reset.
"""
from __future__ import annotations

from typing import Any

import pika
from pika.exceptions import StreamLostError

from hpcperfstats.dbload.lib.print_utils import log_print

QUORUM_QUEUE_TYPE = "quorum"
LISTEND_AMQP_HEARTBEAT_SECONDS = 60
LISTEND_AMQP_BLOCKED_CONNECTION_TIMEOUT_SECONDS = 300
AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS = 5
AMQP_RECONNECT_BACKOFF_CAP_SECONDS = 60
AMQP_RECONNECT_STABLE_CONSUME_SECONDS = 30


class QuorumQueuePreconditionError(RuntimeError):
  """Raised when create-path declare hits a non-type AMQP 406."""


def _amqp_reply_code(exc: BaseException) -> int | None:
  """
  Return the AMQP reply code from ``exc`` when present.

  Args:
    exc (BaseException): Broker or pika exception.

  Returns:
    int | None: ``reply_code``, else ``args[0]`` when that is an int, else
      None.

  Examples:
    >>> class _E(Exception):
    ...     reply_code = 404
    >>> _amqp_reply_code(_E("x"))
    404
  """
  reply = getattr(exc, "reply_code", None)
  if isinstance(reply, int):
    return reply
  args = getattr(exc, "args", ())
  if args and isinstance(args[0], int):
    return int(args[0])
  return None


def is_amqp_not_found_error(exc: BaseException) -> bool:
  """
  Return True when ``exc`` is AMQP 404 NOT_FOUND (missing queue).

  A passive declare of an absent queue closes the channel.

  Args:
    exc (BaseException): Exception from ``queue_declare``.

  Returns:
    bool: True when the broker reported the queue is missing.

  Examples:
    >>> class _E(Exception):
    ...     reply_code = 404
    >>> is_amqp_not_found_error(_E("NOT_FOUND - no queue"))
    True
    >>> is_amqp_not_found_error(OSError("disk full"))
    False
  """
  if _amqp_reply_code(exc) == 404:
    return True
  msg = str(exc).lower()
  return "not_found" in msg or "no queue" in msg


def is_inequivalent_x_queue_type_error(exc: BaseException) -> bool:
  """
  Return True when ``exc`` is 406 inequivalent ``x-queue-type``.

  Args:
    exc (BaseException): Exception from ``queue_declare``.

  Returns:
    bool: True when the existing queue type does not match quorum args.

  Examples:
    >>> is_inequivalent_x_queue_type_error(
    ...     Exception(
    ...         "PRECONDITION_FAILED - inequivalent arg 'x-queue-type' "
    ...         "received 'quorum' but current is 'classic'"
    ...     )
    ... )
    True
    >>> is_inequivalent_x_queue_type_error(Exception("inequivalent arg durable"))
    False
  """
  msg = str(exc).lower()
  if "x-queue-type" not in msg:
    return False
  if _amqp_reply_code(exc) == 406:
    return True
  return "precondition_failed" in msg or "inequivalent arg" in msg


def _open_replacement_channel(channel: Any) -> Any:
  """
  Open a new AMQP channel on the same connection after a channel close.

  Args:
    channel (Any): Closed or closing pika channel that still exposes
      ``connection``.

  Returns:
    Any: A new channel from ``channel.connection.channel()``.

  Raises:
    RuntimeError: When the original channel has no usable connection.
    Exception: Propagates ``connection.channel()`` failures.

  Examples:
    >>> class _Conn:
    ...     def channel(self):
    ...         return "new"
    >>> class _Ch:
    ...     connection = _Conn()
    >>> _open_replacement_channel(_Ch())
    'new'
  """
  connection = getattr(channel, "connection", None)
  if connection is None:
    raise RuntimeError(
        "Cannot replace AMQP channel: original channel has no connection"
    )
  opener = getattr(connection, "channel", None)
  if not callable(opener):
    raise RuntimeError(
        "Cannot replace AMQP channel: connection has no channel() method"
    )
  return opener()


def declare_durable_quorum_queue(channel: Any, queue_name: str) -> Any:
  """
  Attach to an existing ingest queue, or create it as durable quorum.

  Passive-declares first (no ``x-queue-type``) so an existing classic or
  quorum queue is used without a 406 channel close. When the queue is
  absent (404), opens a replacement channel and active-declares durable
  quorum. A create-path 406 on ``x-queue-type`` (race: queue appeared as
  another type) passive-attaches on a second replacement channel.

  Args:
    channel (Any): Open pika channel. After 404/406 this object is dead;
      callers must use the returned channel.
    queue_name (str): Queue name (typically ``cfg.get_rmq_queue()``).

  Returns:
    Any: The live pika channel to consume or drain (the input channel, or
      a replacement after 404/406).

  Raises:
    QuorumQueuePreconditionError: Create-path 406 that is not
      ``x-queue-type`` (for example durable/exclusive mismatch).
    RuntimeError: 404/406 closed the channel and no connection is
      available to open a replacement.
    Exception: Propagates other broker/channel failures from
      ``channel.queue_declare`` unchanged.

  Examples:
    >>> class _Ch:
    ...     def queue_declare(self, **kwargs):
    ...         self.last = kwargs
    ...         return kwargs
    >>> ch = _Ch()
    >>> out = declare_durable_quorum_queue(ch, "stampede3")
    >>> out is ch
    True
    >>> ch.last.get("passive")
    True
  """
  try:
    channel.queue_declare(queue=queue_name, durable=True, passive=True)
    log_print(
        "AMQP attached to existing queue %r (passive declare; "
        "type unchanged)" % queue_name
    )
    return channel
  except Exception as exc:
    if not is_amqp_not_found_error(exc):
      raise
  channel = _open_replacement_channel(channel)
  try:
    channel.queue_declare(
        queue=queue_name,
        durable=True,
        arguments={"x-queue-type": QUORUM_QUEUE_TYPE},
    )
    log_print("AMQP declared durable quorum queue %r" % queue_name)
    return channel
  except Exception as exc:
    if not is_inequivalent_x_queue_type_error(exc):
      msg = str(exc).lower()
      code = _amqp_reply_code(exc)
      if (
          code == 406
          or "precondition_failed" in msg
          or "inequivalent arg" in msg
      ):
        raise QuorumQueuePreconditionError(
            "Queue %r declare failed with inequivalent args (not an "
            "x-queue-type mismatch). Do not convert to classic from "
            "listend. Broker error: %s" % (queue_name, exc)
        ) from exc
      raise
  channel = _open_replacement_channel(channel)
  channel.queue_declare(queue=queue_name, durable=True, passive=True)
  log_print(
      "AMQP attached to existing queue %r after quorum declare type "
      "mismatch (passive; type unchanged)" % queue_name
  )
  return channel


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
  if "timed out consuming from quorum" in msg:
    return True
  if "541" in msg and "quorum" in msg:
    return True
  return "541" in msg and "internal_error" in msg


def is_amqp_peer_reset_reconnect_error(exc: BaseException) -> bool:
  """
  Return True when ``exc`` is a peer-reset / stream-lost / handshake failure.

  Matches ``ConnectionResetError``, pika ``StreamLostError``, and common
  broker/client strings (connection reset, handshake timeout).

  Args:
    exc (BaseException): Exception from consume, ack, or connect.

  Returns:
    bool: ``True`` when exponential reconnect backoff should apply.

  Examples:
    >>> is_amqp_peer_reset_reconnect_error(ConnectionResetError(104, "reset"))
    True
    >>> is_amqp_peer_reset_reconnect_error(OSError("disk full"))
    False
  """
  if isinstance(exc, (ConnectionResetError, StreamLostError)):
    return True
  msg = str(exc).lower()
  if "connectionreset" in msg or "connection reset" in msg:
    return True
  if "stream" in msg and "lost" in msg:
    return True
  if "handshake" in msg and "timeout" in msg:
    return True
  return False


def should_use_amqp_exponential_reconnect_backoff(exc: BaseException) -> bool:
  """
  Return True when listend should grow outer-loop reconnect sleep.

  Combines quorum consume-setup (541) and peer-reset / handshake failures.

  Args:
    exc (BaseException): Exception from connect, declare, or consume.

  Returns:
    bool: ``True`` when ``next_amqp_reconnect_backoff_seconds`` applies.

  Examples:
    >>> should_use_amqp_exponential_reconnect_backoff(
    ...     ConnectionResetError(104, "Connection reset by peer")
    ... )
    True
    >>> should_use_amqp_exponential_reconnect_backoff(OSError("disk full"))
    False
  """
  return (
      is_quorum_consume_setup_error(exc)
      or is_amqp_peer_reset_reconnect_error(exc)
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
