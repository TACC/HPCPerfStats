"""RabbitMQ listener daemon. Consumes messages from the configured queue and appends payloads to per-host files under the archive directory. Single-instance via file lock.

"""
import os
import sys
import time
from collections import deque
from threading import Lock, Thread
from fcntl import LOCK_EX, LOCK_NB, flock

import pika
from pika.exceptions import StreamLostError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.print_utils import log_print

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds

_message_timestamps = deque()
_timestamps_lock = Lock()
_last_message_time = None
_last_idle_report_time = None
_channel_ref = None
_idle_thread_started = False


def on_message(channel, method_frame, header_frame, body):
  """Callback for each message: decode body, determine host, write/append to host's current file and optionally rotate. Acknowledges the message.

  Per-message logging of consumption/queue depth is avoided; instead, a
  background monitor thread reports aggregate rates every 10 minutes.
  """
  delivery_tag = getattr(method_frame, "delivery_tag", None)
  try:
    message = body.decode(errors="replace")
    if not message:
      raise ValueError("Empty message body")

    if message[0] == "$":
      parts = message.split("\n")
      if len(parts) < 2:
        raise ValueError("Malformed '$' message: missing host line")
      host_parts = parts[1].split()
      if len(host_parts) < 2:
        raise ValueError("Malformed '$' message: host line missing field")
      host = host_parts[1]
    else:
      msg_parts = message.split()
      if len(msg_parts) < 3:
        raise ValueError("Malformed message: not enough fields to get host")
      host = msg_parts[2]

    host_dir = os.path.join(cfg.get_archive_dir_path(), host)
    if not os.path.exists(host_dir):
      os.makedirs(host_dir)

    current_path = os.path.join(host_dir, "current")
    if message[0] == "$":
      if os.path.exists(current_path):
        os.unlink(current_path)

      with open(current_path, "w") as fd:
        link_path = os.path.join(host_dir, str(int(time.time())))
        if os.path.exists(link_path):
          os.remove(link_path)
        os.link(current_path, link_path)

    with open(current_path, "a") as fd:
      fd.write(message)

    now = time.time()
    with _timestamps_lock:
      global _last_message_time
      _last_message_time = now
      _message_timestamps.append(now)
      cutoff_window = now - MESSAGE_WINDOW_SECONDS
      while _message_timestamps and _message_timestamps[0] < cutoff_window:
        _message_timestamps.popleft()

    channel.basic_ack(delivery_tag=delivery_tag)
  except Exception as e:
    # Critical behavior: do not acknowledge on failure.
    # Requeue so the message remains on the server for later retry.
    log_print("Error processing message; leaving on server: %s" % e)
    try:
      if hasattr(channel, "basic_nack") and delivery_tag is not None:
        channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
    except Exception as nack_err:
      if DEBUG:
        log_print("Failed to nack message after processing error: %s" % nack_err)
    return


def _idle_monitor():
  """Periodically report messages consumed in the last 10 minutes and queue depth.

  Runs every IDLE_CHECK_INTERVAL seconds, but only logs once per
  MESSAGE_WINDOW_SECONDS window.
  """
  global _last_idle_report_time
  while True:
    time.sleep(IDLE_CHECK_INTERVAL)
    now = time.time()
    if (_last_idle_report_time is not None and
        (now - _last_idle_report_time) < MESSAGE_WINDOW_SECONDS):
      continue

    with _timestamps_lock:
      cutoff_10 = now - MESSAGE_WINDOW_SECONDS
      count_last_10 = sum(1 for ts in _message_timestamps if ts >= cutoff_10)

    # Also report how many messages are currently waiting in the queue.
    queue_depth = None
    try:
      # Use the shared channel reference if available; otherwise skip depth.
      channel = _channel_ref[0] if _channel_ref else None
      if channel is not None:
        q = channel.queue_declare(
            queue=cfg.get_rmq_queue(), durable=True, passive=True)
        queue_depth = q.method.message_count
    except Exception as e:
      if DEBUG:
        log_print("Failed to get queue depth in monitor: %s" % e)

    if queue_depth is not None:
      log_print(
          "Messages consumed in the last 10 minutes: %d; "
          "messages waiting to be consumed: %d" %
          (count_last_10, queue_depth))
    else:
      log_print(
          "Messages consumed in the last 10 minutes: %d; "
          "messages waiting to be consumed: unknown" %
          count_last_10)

    _last_idle_report_time = now


def main():
  global _channel_ref
  global _idle_thread_started
  lock_path = os.path.join(
      os.path.dirname(os.path.realpath(__file__)), "listend_lock")
  with open(lock_path, "w") as fd:
    try:
      flock(fd, LOCK_EX | LOCK_NB)
    except IOError:
      log_print("listend is already running")
      sys.exit()

    # Ensure the idle monitor thread is started exactly once for the lifetime
    # of the process so that it can continue reporting even across reconnects.
    if not _idle_thread_started:
      idle_thread = Thread(target=_idle_monitor, daemon=True)
      idle_thread.start()
      _idle_thread_started = True

    # Outer loop: keep the daemon running indefinitely by reconnecting to
    # RabbitMQ on failure instead of exiting. The process will typically only
    # terminate when it receives a signal (e.g. SIGTERM) from the service
    # manager.
    while True:
      log_print("Starting Connection")
      parameters = pika.ConnectionParameters(cfg.get_rmq_server())
      connection = None
      try:
        connection = pika.BlockingConnection(parameters)

        channel = connection.channel()
        _channel_ref = channel
        channel.queue_declare(queue=cfg.get_rmq_queue(), durable=True)
        # Report how many messages are waiting to be consumed at startup.
        try:
          q = channel.queue_declare(
              queue=cfg.get_rmq_queue(), durable=True, passive=True)
          log_print(
              "Messages waiting to be consumed at startup: %d" %
              q.method.message_count)
        except Exception as e:
          log_print("Failed to get startup queue depth: %s" % e)

        channel.basic_consume(cfg.get_rmq_queue(), on_message)
        log_print("Begining Consume from queue: " + cfg.get_rmq_queue())
        try:
          channel.start_consuming()
        except (KeyboardInterrupt, SystemExit):
          channel.stop_consuming()
          raise
        except StreamLostError as e:
          # Connection dropped (e.g. broker restart or idle timeout). Treat as a
          # normal shutdown condition and only log in DEBUG mode to avoid noisy
          # "Error while consuming" messages like "pop from an empty deque".
          if DEBUG:
            log_print("RabbitMQ stream lost while consuming: %s" % e)
        except Exception as e:
          # Handle other connection-level errors from pika without raising during
          # shutdown/cleanup. We will attempt to reconnect after a short delay.
          log_print("Error while consuming from RabbitMQ: %s" % e)
      except (KeyboardInterrupt, SystemExit):
        # Allow clean shutdown on explicit termination signals.
        log_print("Shutting down listend daemon on user request")
        break
      except Exception as e:
        log_print("Error establishing RabbitMQ connection: %s" % e)
      finally:
        try:
          # Guard against closing an already-closed connection, which would raise
          # ConnectionWrongStateError in recent pika versions.
          if connection and not connection.is_closed:
            connection.close()
        except Exception as e:
          if DEBUG:
            log_print("Error while closing RabbitMQ connection: %s" % e)

      # If we reach this point without breaking, sleep briefly before attempting
      # to reconnect. This avoids tight reconnect loops that could cause
      # excessive CPU usage or log spam.
      time.sleep(5)


def _is_shutting_down():
  # Helper to make the intent of the main loop clearer; currently always
  # returns False because shutdown is only triggered via KeyboardInterrupt or
  # SystemExit in the loop above.
  return False


if __name__ == "__main__":
  main()
