#!/usr/bin/env python3
"""
Drain the configured RabbitMQ queue once into the archive (non-daemon).

Uses the same archive write path as ``listend.py`` but ``basic_get`` loops until
the queue is empty, then exits. Intended for tests and one-shot backfills; does
not take ``listend_lock``.
"""
from __future__ import annotations

from typing import Any

import sys

import pika

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.listend import append_monitor_payload_to_archive
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.lib.rmq_quorum_queue import (
    declare_durable_quorum_queue,
    listend_amqp_connection_parameters,
)


def drain_queue_to_archive() -> Any:
  """
  Pull all messages from ``cfg.get_rmq_queue()`` and append each to archive.

  Returns:
    Any: Count of messages drained (integer).

  Examples:
    >>> drain_queue_to_archive()  # doctest: +SKIP
  """
  parameters = listend_amqp_connection_parameters(cfg.get_rmq_server())
  connection = pika.BlockingConnection(parameters)
  channel = connection.channel()
  queue_name = cfg.get_rmq_queue()
  channel = declare_durable_quorum_queue(channel, queue_name)
  drained = 0
  try:
    while True:
      method_frame, _properties, body = channel.basic_get(
          queue=queue_name, auto_ack=False)
      if method_frame is None:
        break
      delivery_tag = method_frame.delivery_tag
      try:
        message = body.decode(errors="replace")
        append_monitor_payload_to_archive(message)
        channel.basic_ack(delivery_tag=delivery_tag)
      except Exception as e:
        log_print("listend_drain: error processing message: %s" % e)
        try:
          channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        except Exception:
          pass
      drained += 1
  finally:
    try:
      if connection and not connection.is_closed:
        connection.close()
    except Exception:
      pass
  log_print("listend_drain: drained %d message(s)" % drained)
  return drained


def main() -> None:
  """
  Run this module's command-line entrypoint.
  
  Returns:
    None
  
  Examples:
    >>> main()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_script_process_title

  set_script_process_title()
  drain_queue_to_archive()


if __name__ == "__main__":
  main()
  sys.exit(0)
