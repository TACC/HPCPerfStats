"""RabbitMQ listener daemon. Consumes messages from the configured queue and appends payloads to per-host files under the archive directory. Single-instance via file lock.

"""
import os
import signal
import sys
import time
from collections import deque
from threading import Event, Lock, Thread
from fcntl import LOCK_EX, LOCK_NB, flock

import pika
from pika.exceptions import StreamLostError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.print_utils import log_print
from hpcperfstats.shutdown_utils import send_sigchld_to_parent

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds

_message_timestamps = deque()
_unlink_timestamps = deque()
_timestamps_lock = Lock()
_last_message_time = None
_last_idle_report_time = None
_channel_ref = None
_idle_thread_started = False
_idle_monitor_stop_event = Event()


def _get_first_timestamp_seconds(file_path):
  """Return first unix timestamp seconds found in stats file content.

  Expects a line beginning with digits in the format: "<t> <jid> <host> ...".
  """
  try:
    with open(file_path, "r") as fd:
      for line in fd:
        if not line:
          continue
        if line[0].isdigit():
          t = line.split(maxsplit=1)[0]
          # The file may store floats (epoch with fractional seconds).
          return int(float(t))
  except Exception:
    pass
  return None


def _current_is_hardlinked_to_older_epoch(host_dir, current_path, cutoff_epoch_ts):
  """Return True if `current_path` shares an inode with an older epoch file.

  Older epoch files are numeric filenames whose epoch seconds are strictly
  less than `cutoff_epoch_ts`.
  """
  try:
    with os.scandir(host_dir) as it:
      for entry in it:
        if not entry.is_file():
          continue
        name = entry.name
        if not name.isdigit():
          continue
        try:
          epoch_ts = int(name)
        except ValueError:
          continue
        if epoch_ts >= cutoff_epoch_ts:
          continue
        try:
          if os.path.samefile(current_path, entry.path):
            return True
        except OSError:
          continue
  except Exception:
    return False
  return False


def _ensure_current_hardlinked_to_timestamp(host_dir, current_path):
  """Hardlink current_path to the epoch seconds of the first timestamp line.

  This is a safety fallback for cases where `current` exists but is not yet
  linked to an epoch-named file (i.e. sync_timedb can't reliably detect the
  live segment).
  """
  first_ts_sec = _get_first_timestamp_seconds(current_path)
  if first_ts_sec is None:
    raise RuntimeError("Unable to find timestamp in current file")

  link_path = os.path.join(host_dir, str(first_ts_sec))
  if os.path.exists(link_path):
    # If it's already the same inode, we're done.
    if os.path.samefile(current_path, link_path):
      return
    raise RuntimeError(
        "Timestamp link path exists but is not hardlinked to current: %s" %
        link_path
    )

  os.link(current_path, link_path)


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

    # `$`-prefixed messages are *schema/header* dumps from `hpcperfstatsd`:
    # they are emitted when `rotate_timer_cb()` runs (immediately at daemon
    # start, then every 86400s). Regular sampling messages do not include
    # these `$` lines; if sending fails during a rotate, the same `$` payload
    # is later resent from the in-memory ring buffer or dumpfile.
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
    unlinked_current = False
    if message[0] == "$":
      # Use a single epoch timestamp for both the pre-unlink check and the
      # post-unlink epoch hardlink to keep time.time() call counts stable.
      epoch_ts = int(time.time())
      if os.path.exists(current_path):
        if not _current_is_hardlinked_to_older_epoch(
            host_dir, current_path, epoch_ts):
          _ensure_current_hardlinked_to_timestamp(host_dir, current_path)
          if not _current_is_hardlinked_to_older_epoch(
              host_dir, current_path, epoch_ts):
            raise RuntimeError(
                "current is not linked to an older epoch before unlink")

        os.unlink(current_path)
        unlinked_current = True

      with open(current_path, "w") as fd:
        link_path = os.path.join(host_dir, str(epoch_ts))
        if os.path.exists(link_path):
          os.remove(link_path)
        os.link(current_path, link_path)
        # Epoch name and current share an inode until the next ``$`` rotation.
        # sync_timedb skips epoch files same-inode-as-current to avoid read races.

    with open(current_path, "a") as fd:
      fd.write(message)

    now = time.time()
    with _timestamps_lock:
      global _last_message_time
      _last_message_time = now
      _message_timestamps.append(now)
      if unlinked_current:
        _unlink_timestamps.append(now)
      cutoff_window = now - MESSAGE_WINDOW_SECONDS
      while _message_timestamps and _message_timestamps[0] < cutoff_window:
        _message_timestamps.popleft()
      while _unlink_timestamps and _unlink_timestamps[0] < cutoff_window:
        _unlink_timestamps.popleft()

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
  while not _idle_monitor_stop_event.is_set():
    time.sleep(IDLE_CHECK_INTERVAL)
    if _idle_monitor_stop_event.is_set():
      break
    now = time.time()
    if (_last_idle_report_time is not None and
        (now - _last_idle_report_time) < MESSAGE_WINDOW_SECONDS):
      continue

    with _timestamps_lock:
      cutoff_10 = now - MESSAGE_WINDOW_SECONDS
      count_last_10 = sum(1 for ts in _message_timestamps if ts >= cutoff_10)
      unlink_count_last_10 = sum(
          1 for ts in _unlink_timestamps if ts >= cutoff_10
      )

    # Also report how many messages are currently waiting in the queue.
    queue_depth = 0
    try:
      # Use the shared channel reference if available; otherwise skip depth.
      channel = _channel_ref
      if channel is not None:
        try:
          # passive=True avoids modifying the queue, but can fail if the queue
          # doesn't exist yet during reconnect windows.
          q = channel.queue_declare(
              queue=cfg.get_rmq_queue(), durable=True, passive=True)
          queue_depth = q.method.message_count
        except Exception:
          # Fall back to a non-passive declare so we can still read the
          # queue depth without logging "unknown".
          q = channel.queue_declare(
              queue=cfg.get_rmq_queue(), durable=True, passive=False)
          queue_depth = q.method.message_count
    except Exception as e:
      if DEBUG:
        log_print("Failed to get queue depth in monitor: %s" % e)
      # Keep default 0 so logs always include a number.

    log_print(
        "Messages consumed in the last 10 minutes: %d; "
        "messages waiting to be consumed: %d; "
        "current file unlinks (last 10 minutes): %d" %
        (count_last_10, queue_depth, unlink_count_last_10))

    _last_idle_report_time = now


def main():
  global _channel_ref
  global _idle_thread_started
  sigterm_received = False
  connection = None

  def _sigterm_handler(signum, frame):
    nonlocal sigterm_received
    sigterm_received = True
    _idle_monitor_stop_event.set()
    raise SystemExit(143)

  previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
  signal.signal(signal.SIGTERM, _sigterm_handler)
  try:
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
          except AttributeError as e:
            # Some pika versions raise an AttributeError like "'NoneType' object
            # has no attribute 'poll'" during shutdown when the underlying poller
            # has already been torn down. This is effectively equivalent to a
            # lost stream and should not be treated as a hard error.
            msg = str(e)
            if "NoneType" in msg and "poll" in msg:
              if DEBUG:
                log_print(
                    "RabbitMQ connection poller torn down during consume: %s" % e)
            else:
              log_print("Error while consuming from RabbitMQ: %s" % e)
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
  finally:
    _idle_monitor_stop_event.set()
    try:
      if connection and not connection.is_closed:
        connection.close()
    except Exception:
      pass
    if sigterm_received:
      send_sigchld_to_parent()
    signal.signal(signal.SIGTERM, previous_sigterm_handler)


def _is_shutting_down():
  # Helper to make the intent of the main loop clearer; currently always
  # returns True once SIGTERM handler asks the process to stop.
  return _idle_monitor_stop_event.is_set()


if __name__ == "__main__":
  main()
