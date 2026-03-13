"""RabbitMQ listener daemon. Consumes messages from the configured queue and appends payloads to per-host files under the archive directory. Single-instance via file lock.

"""
import os
import signal
import sys
import time
from collections import deque
from threading import Lock, Thread
from fcntl import LOCK_EX, LOCK_NB, flock

import pika

import hpcperfstats.conf_parser as cfg
from hpcperfstats.print_utils import log_print

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds

_message_timestamps = deque()
_timestamps_lock = Lock()
_last_message_time = None
_last_idle_report_time = None

# Set by main loop so SIGTERM handler can request shutdown.
_channel_ref = []


def on_message(channel, method_frame, header_frame, body):
  """Callback for each message: decode body, determine host, write/append to host's current file and optionally rotate. Acknowledges the message.

    """
  log_print("found message: %s" % header_frame)
  try:
    message = body.decode(errors='replace')
  except Exception:
    log_print("Unexpected error at decode:", sys.exc_info()[0])
    #print(body)
    return

  if message[0] == '$':
    host = message.split('\n')[1].split()[1]
  else:
    host = message.split()[2]

  #if host == "localhost.localdomain": return
  host_dir = os.path.join(cfg.get_archive_dir_path(), host)
  if not os.path.exists(host_dir):
    os.makedirs(host_dir)

  current_path = os.path.join(host_dir, "current")
  if message[0] == '$':
    if os.path.exists(current_path):
      os.unlink(current_path)

    with open(current_path, 'w') as fd:
      link_path = os.path.join(host_dir, str(int(time.time())))
      if os.path.exists(link_path):
        os.remove(link_path)
      os.link(current_path, link_path)

  with open(current_path, 'a') as fd:
    fd.write(message)

  now = time.time()
  with _timestamps_lock:
    global _last_message_time
    _last_message_time = now
    _message_timestamps.append(now)
    cutoff_window = now - MESSAGE_WINDOW_SECONDS
    while _message_timestamps and _message_timestamps[0] < cutoff_window:
      _message_timestamps.popleft()

    # Count messages in the last 10 minutes using the 10-minute window.
    cutoff_10 = now - MESSAGE_WINDOW_SECONDS
    count_last_10 = 0
    for ts in _message_timestamps:
      if ts >= cutoff_10:
        count_last_10 += 1

  # Also report how many messages are currently waiting in the queue.
  queue_depth = None
  try:
    q = channel.queue_declare(
        queue=cfg.get_rmq_queue(), durable=True, passive=True)
    queue_depth = q.method.message_count
  except Exception as e:
    log_print("Failed to get queue depth: %s" % e)

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

  channel.basic_ack(delivery_tag=method_frame.delivery_tag)



def _handle_sigterm(signum, frame):
  """On SIGTERM, stop consuming so we exit cleanly and release the lock."""
  log_print("Received SIGTERM, stopping consumer")
  if _channel_ref:
    try:
      _channel_ref[0].stop_consuming()
    except Exception:
      pass


def _idle_monitor():
  """Periodically log if no messages have been consumed in the last 10 minutes."""
  global _last_idle_report_time
  while True:
    time.sleep(IDLE_CHECK_INTERVAL)
    now = time.time()
    with _timestamps_lock:
      last_msg = _last_message_time

    if last_msg is None:
      # No messages yet; treat startup as activity.
      continue

    idle_duration = now - last_msg
    if idle_duration >= MESSAGE_WINDOW_SECONDS:
      # Only report once per 10-minute idle window.
      if (_last_idle_report_time is None or
          (now - _last_idle_report_time) >= MESSAGE_WINDOW_SECONDS):
        _last_idle_report_time = now
        log_print("No messages consumed in the last 10 minutes")


with open(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "listend_lock"),
    "w") as fd:
  try:
    flock(fd, LOCK_EX | LOCK_NB)
  except IOError:
    log_print("listend is already running")
    sys.exit()

  signal.signal(signal.SIGTERM, _handle_sigterm)
  log_print("Starting Connection")
  parameters = pika.ConnectionParameters(cfg.get_rmq_server())
  connection = pika.BlockingConnection(parameters)
  try:
    # Start idle monitor thread before consuming.
    idle_thread = Thread(target=_idle_monitor, daemon=True)
    idle_thread.start()

    channel = connection.channel()
    _channel_ref.append(channel)
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
  finally:
    connection.close()
