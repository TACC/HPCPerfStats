"""
RabbitMQ listener daemon. Consumes messages from the configured queue and
appends payloads to per-host files under the archive directory. Single- instance
via file lock.

Attributes:
  DEBUG: Attribute.
  IDLE_CHECK_INTERVAL: Attribute.
  MESSAGE_WINDOW_SECONDS: Attribute.
  RECENT_HOST_TTL_SECONDS: Attribute.
  _idle_monitor_stop_event: Attribute.
  _idle_thread_started: Attribute.
  _last_idle_report_time: Attribute.
  _last_message_time: Attribute.
  _message_timestamps: Attribute.
  _db_backpressure_pause: Attribute.
  _amqp_reconnect_requested: Attribute.
  _recent_host_queue: Attribute.
  _recent_host_redis_client: Attribute.
  _recent_host_worker_stop_event: Attribute.
  _recent_host_worker_thread_started: Attribute.
  _timestamps_lock: Attribute.
  _unlink_timestamps: Attribute.
  _MIN_PLAUSIBLE_UNIX_SECONDS: Lowest unix seconds accepted as a stats
    timestamp (schema host line ``1 <fqdn>`` is below this).
  _DIGIT_EPOCH_NAME_MAX_ATTEMPTS: Max digit names to probe when finding a
    free epoch filename during ``$`` rotate.
"""
from __future__ import annotations

from typing import Any

import os
import queue
import signal
import sys
import time
from collections import deque
from threading import Event, Lock, Thread
from fcntl import LOCK_EX, LOCK_NB, flock

import pika
import redis
from pika.exceptions import (
    AMQPChannelError,
    ChannelWrongStateError,
    ConnectionWrongStateError,
    StreamLostError,
)

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.file_locking import file_read_lock_wait, file_write_lock
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.shutdown_utils import send_sigchld_to_parent
from hpcperfstats.lib.monitor_identity import (
    parse_monitor_identity_from_dollar_message,
    set_monitor_identity,
)

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds
RECENT_HOST_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week
# Monitor schema dumps start with ``$`` then ``1 <fqdn>``; that ``1`` is not
# a sample unix second. Real host_data timestamps are post-2001.
_MIN_PLAUSIBLE_UNIX_SECONDS = 1_000_000_000
_DIGIT_EPOCH_NAME_MAX_ATTEMPTS = 10_000

_message_timestamps = deque()
_unlink_timestamps = deque()
_timestamps_lock = Lock()
_last_message_time = None
_last_idle_report_time = None
_idle_thread_started = False
_idle_monitor_stop_event = Event()
_recent_host_worker_thread_started = False
_recent_host_worker_stop_event = Event()
_recent_host_queue = queue.Queue(maxsize=100000)
_recent_host_redis_client = None
# Set when on_message stops consume for live-DB queue backpressure.
_db_backpressure_pause = False
# Set when on_message detects a dead AMQP channel/connection and requests
# a full BlockingConnection restart via the outer reconnect loop.
_amqp_reconnect_requested = False


def _parse_plausible_unix_seconds(token: str) -> int | None:
  """Return truncated unix seconds when ``token`` is a plausible sample time.

  Schema host lines (``1 <fqdn>``) and other small integers are not sample
  timestamps. Accept only values at or above
  ``_MIN_PLAUSIBLE_UNIX_SECONDS``.

  Args:
    token (str): Leading field from a digit-starting stats line.

  Returns:
    int | None: Truncated unix seconds, or ``None`` when not plausible.

  Examples:
    >>> _parse_plausible_unix_seconds("1786487860.470903")
    1786487860
    >>> _parse_plausible_unix_seconds("1") is None
    True
  """
  try:
    ts = int(float(token))
  except (TypeError, ValueError):
    return None
  if ts < _MIN_PLAUSIBLE_UNIX_SECONDS:
    return None
  return ts


def _first_plausible_unix_seconds_from_open_file(fd: Any) -> int | None:
  """Scan an open stats file for the first plausible unix-second token.

  Args:
    fd (Any): Text file object already opened for reading.

  Returns:
    int | None: First plausible timestamp seconds, or ``None`` if none.

  Examples:
    >>> from io import StringIO
    >>> _first_plausible_unix_seconds_from_open_file(
    ...     StringIO("$\\n1 host.example\\n1786487860 1 host.example\\n")
    ... )
    1786487860
  """
  for line in fd:
    if not line:
      continue
    s = line.lstrip()
    if not s or not s[0].isdigit():
      continue
    parsed = _parse_plausible_unix_seconds(s.split(maxsplit=1)[0])
    if parsed is not None:
      return parsed
  return None


def _get_first_timestamp_seconds(
    file_path: str,
    use_lock: bool = True,
) -> int | None:
  """Return first plausible unix timestamp seconds in a stats file.

  Skips ``$`` schema host lines (``1 <fqdn>``). Expects a sample line
  ``"<t> <jid> <host> ..."`` with ``t >= 1e9``.

  Args:
    file_path (str): Path to ``current`` or a closed epoch file.
    use_lock (bool): When True, take a read lock before scanning.

  Returns:
    int | None: First plausible unix seconds, or ``None`` if none / I/O
    error.

  Examples:
    >>> _get_first_timestamp_seconds("/no/such/file", False) is None
    True
  """
  try:
    if use_lock:
      with file_read_lock_wait(file_path):
        with open(file_path, "r") as fd:
          return _first_plausible_unix_seconds_from_open_file(fd)
    with open(file_path, "r") as fd:
      return _first_plausible_unix_seconds_from_open_file(fd)
  except Exception:
    return None


def _current_is_hardlinked_to_digit_epoch(
    host_dir: str,
    current_path: str,
) -> bool:
  """Return True if ``current_path`` shares an inode with any digit epoch file.

  Any samefile digit name is durable enough to unlink ``current`` as long
  as the new-current link uses a different unused name (never
  ``os.remove`` of that inode).

  Args:
    host_dir (str): Per-host archive directory.
    current_path (str): Path to the live ``current`` file.

  Returns:
    bool: True when a digit-named sibling is the same inode.

  Examples:
    >>> _current_is_hardlinked_to_digit_epoch("/no/host", "/no/current")
    False
  """
  try:
    with os.scandir(host_dir) as it:
      for entry in it:
        if not entry.is_file():
          continue
        name = entry.name
        if name.endswith(".lock") or not name.isdigit():
          continue
        try:
          if os.path.samefile(current_path, entry.path):
            return True
        except OSError:
          continue
  except Exception:
    return False
  return False


def _link_current_to_unique_digit_epoch(
    host_dir: str,
    current_path: str,
    start_ts: int,
    step: int,
) -> int:
  """Hardlink ``current_path`` to an unused digit epoch name.

  Walks ``start_ts, start_ts+step, ...`` until a name is free or already
  samefile with ``current_path``. Never ``os.remove`` a different inode.

  Args:
    host_dir (str): Per-host archive directory.
    current_path (str): Path to the live ``current`` file.
    start_ts (int): First candidate unix-second filename.
    step (int): ``+1`` after creating new current, ``-1`` when ensuring
      a pre-unlink safety link.

  Returns:
    int: Digit epoch seconds used for the hardlink.

  Raises:
    ValueError: ``step`` is 0.
    RuntimeError: No free name within ``_DIGIT_EPOCH_NAME_MAX_ATTEMPTS``.

  Examples:
    >>> _link_current_to_unique_digit_epoch("/x", "/x/current", 1, 0)
    Traceback (most recent call last):
        ...
    ValueError: step must be non-zero
  """
  if step == 0:
    raise ValueError("step must be non-zero")
  ts = int(start_ts)
  for _ in range(_DIGIT_EPOCH_NAME_MAX_ATTEMPTS):
    if ts <= 0:
      break
    link_path = os.path.join(host_dir, str(ts))
    if os.path.exists(link_path):
      try:
        if os.path.samefile(current_path, link_path):
          return ts
      except OSError:
        pass
      ts += step
      continue
    os.link(current_path, link_path)
    return ts
  raise RuntimeError(
      "Unable to find unused digit epoch name in %s (start=%s step=%s)"
      % (host_dir, start_ts, step)
  )


def _ensure_current_hardlinked_to_timestamp(
    host_dir: str,
    current_path: str,
    cutoff_epoch_ts: int,
) -> None:
  """Hardlink ``current`` to a free digit name at or below first sample ts.

  Safety fallback when ``current`` exists but is not yet linked to any
  digit epoch file. If ``str(first_ts)`` is a closed different inode,
  walk downward from ``min(first_ts, cutoff-1)`` instead of raising
  ``Timestamp link path exists but is not hardlinked to current``.

  Args:
    host_dir (str): Per-host archive directory.
    current_path (str): Path to the live ``current`` file.
    cutoff_epoch_ts (int): Rotate-time unix seconds; safety link names
      stay strictly below this when first_ts is at/after cutoff.

  Returns:
    None

  Raises:
    RuntimeError: No plausible timestamp in ``current``, or no free
      digit name in the walk budget.

  Examples:
    >>> _ensure_current_hardlinked_to_timestamp("/x", "/x/current", 1)
    Traceback (most recent call last):
        ...
    RuntimeError: Unable to find timestamp in current file
  """
  # Called from on_message() while holding write lock for current_path.
  first_ts_sec = _get_first_timestamp_seconds(current_path, use_lock=False)
  if first_ts_sec is None:
    raise RuntimeError("Unable to find timestamp in current file")

  start_ts = min(int(first_ts_sec), int(cutoff_epoch_ts) - 1)
  if start_ts <= 0:
    raise RuntimeError("Unable to find unused digit epoch name in %s" % host_dir)
  _link_current_to_unique_digit_epoch(
      host_dir, current_path, start_ts, step=-1
  )


def _get_recent_host_redis_client() -> Any:
  """
  Get or create the Redis client used for recent-host timestamps.
  
  Returns:
    Any: Open return polymorphism from ``_get_recent_host_redis_client``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _get_recent_host_redis_client()  # doctest: +SKIP
  """
  global _recent_host_redis_client
  if _recent_host_redis_client is not None:
    return _recent_host_redis_client
  try:
    _recent_host_redis_client = redis.from_url(
        cfg.get_redis_location(), decode_responses=True)
  except Exception:
    _recent_host_redis_client = None
  return _recent_host_redis_client


def _set_recent_host_timestamp(redis_client: Any, host: Any) -> None:
  """
  Set `recent_host:<fqdn>` to current epoch seconds.
  
  Args:
    redis_client (Any): Redis client passed to this helper.
    host (Any): Host passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _set_recent_host_timestamp(None, None)  # doctest: +SKIP
  """
  if not host or "." not in host:
    return
  redis_client.set(
      "recent_host:%s" % host,
      str(int(time.time())),
      ex=RECENT_HOST_TTL_SECONDS,
  )


def _enqueue_recent_host_update(host: Any) -> None:
  """
  Queue a best-effort Redis host timestamp update.
  
  Args:
    host (Any): Host FQDN string (must contain ``.``).
  
  Returns:
    None
  
  Examples:
    >>> _enqueue_recent_host_update(None)  # doctest: +SKIP
  """
  if not host or "." not in host:
    return
  try:
    _recent_host_queue.put_nowait(host)
  except queue.Full:
    if DEBUG:
      log_print("Recent-host Redis queue is full; dropping update for %s" % host)


def _enqueue_monitor_identity_update(identity: Any) -> None:
  """
  Queue a best-effort Redis ``monitor_identity:{fqdn}`` SET.

  Identity writes ride the same background worker as ``recent_host`` so
  RabbitMQ ack / archive I/O timing is unchanged. Missing ``$build`` is
  already tolerated by the parser (slug may be null).

  Args:
    identity (Any): Mapping from
      ``parse_monitor_identity_from_dollar_message`` (must include ``fqdn``).

  Returns:
    None

  Examples:
    >>> _enqueue_monitor_identity_update(None)  # doctest: +SKIP
  """
  if not isinstance(identity, dict):
    return
  fqdn = identity.get("fqdn")
  if not fqdn or "." not in str(fqdn):
    return
  try:
    _recent_host_queue.put_nowait(identity)
  except queue.Full:
    if DEBUG:
      log_print(
          "Recent-host Redis queue is full; dropping monitor_identity for %s"
          % fqdn
      )


def _recent_host_worker() -> None:
  """
  Background worker that writes recent-host and monitor-identity keys to Redis.

  Queue items are either an FQDN ``str`` (``recent_host`` timestamp) or an
  identity ``dict`` (``monitor_identity`` JSON). Redis-only; no DB writes,
  pause/ack, or archive-gate changes.

  Returns:
    None
  
  Examples:
    >>> _recent_host_worker()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title("", script_name="listend.py", role="recent-host-worker")
  while not _recent_host_worker_stop_event.is_set():
    try:
      item = _recent_host_queue.get(timeout=1.0)
    except queue.Empty:
      continue

    try:
      redis_client = _get_recent_host_redis_client()
      if redis_client is not None:
        if isinstance(item, dict):
          set_monitor_identity(
              redis_client,
              item,
              ttl_seconds=RECENT_HOST_TTL_SECONDS,
          )
        else:
          _set_recent_host_timestamp(redis_client, item)
    except Exception as e:
      if DEBUG:
        label = (
            item.get("fqdn")
            if isinstance(item, dict)
            else item
        )
        log_print(
            "Failed to update recent-host/monitor_identity Redis for %s: %s"
            % (label, e)
        )
    finally:
      _recent_host_queue.task_done()


def append_monitor_payload_to_archive(message: Any) -> Any:
  """
  Decode-safe: append one monitor payload string to the per-host archive (same.
  
    as listend).
  
  Used by the long-running daemon and by ``listend_drain`` integration tests.
  Returns the FQDN host string parsed from the payload (for metrics / logging).
  
  Args:
    message (Any): Message passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    RuntimeError: Raised when ``append_monitor_payload_to_archive`` hits a
    ``RuntimeError`` failure path.
    ValueError: Raised when ``append_monitor_payload_to_archive`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> append_monitor_payload_to_archive(None)  # doctest: +SKIP
  """
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
    with file_write_lock(current_path):
      if os.path.exists(current_path):
        if not _current_is_hardlinked_to_digit_epoch(host_dir, current_path):
          _ensure_current_hardlinked_to_timestamp(
              host_dir, current_path, epoch_ts)
          if not _current_is_hardlinked_to_digit_epoch(
              host_dir, current_path):
            raise RuntimeError(
                "current is not hardlinked to a digit epoch before unlink")

        os.unlink(current_path)
        unlinked_current = True

      with open(current_path, "w") as fd:
        _link_current_to_unique_digit_epoch(
            host_dir, current_path, epoch_ts, step=1)
        # Epoch name and current share an inode until the next ``$`` rotation.
        # sync_timedb skips epoch files same-inode-as-current to avoid read races.

      with open(current_path, "a") as fd:
        fd.write(message)
  else:
    with file_write_lock(current_path):
      with open(current_path, "a") as fd:
        fd.write(message)
  _enqueue_recent_host_update(host)
  # Redis-only identity snapshot on ``$`` rotation (tolerant if ``$build``
  # absent). Does not change ack, pause/resume, or archive/DB gate semantics.
  if message[0] == "$":
    try:
      identity = parse_monitor_identity_from_dollar_message(
          message if isinstance(message, str) else message.decode("utf-8", "replace"),
          updated_at=int(time.time()),
      )
    except Exception:
      identity = None
    if identity is not None:
      _enqueue_monitor_identity_update(identity)

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

  return host


def _get_rmq_queue_depth_for_monitor() -> Any:
  """
  Return ``message_count`` for the configured queue.
  
  Uses a **separate** short-lived connection. Pika ``BlockingConnection`` and
  its channels are not thread-safe; the idle monitor runs in a background
  thread and must not touch the channel used by ``start_consuming()`` in the
  main thread (that sharing caused ``Channel is closed``, transport state
  errors, and ``IndexError: pop from an empty deque`` in pika).
  
  Returns:
    Any: Open return polymorphism from ``_get_rmq_queue_depth_for_monitor``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _get_rmq_queue_depth_for_monitor()  # doctest: +SKIP
  """
  parameters = pika.ConnectionParameters(cfg.get_rmq_server())
  connection = None
  try:
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    try:
      q = channel.queue_declare(
          queue=cfg.get_rmq_queue(), durable=True, passive=True)
    except Exception:
      q = channel.queue_declare(
          queue=cfg.get_rmq_queue(), durable=True, passive=False)
    return q.method.message_count
  except Exception as e:
    if DEBUG:
      log_print("Failed to get queue depth in monitor: %s" % e)
    return 0
  finally:
    if connection is not None:
      try:
        if not connection.is_closed:
          connection.close()
      except Exception:
        pass


def _live_db_ingest_pool_active() -> Any:
  """
  Return the started live DB ingest pool, or ``None``.

  Returns:
    Any: ``ListendDbIngestPool`` when enabled and started, else ``None``.

  Examples:
    >>> _live_db_ingest_pool_active()  # doctest: +SKIP
  """
  try:
    from hpcperfstats.dbload.lib.listend_db_ingest import get_listend_db_ingest_pool

    pool = get_listend_db_ingest_pool()
  except Exception:
    return None
  if pool is None or not pool.enabled or not pool._started:
    return None
  return pool


def _listend_db_backpressure_mode_is_pause() -> bool:
  """
  Return True when INI backpressure mode is ``pause`` (not ``drop``).

  Defaults to False (``drop``) when the getter fails so archive+ack remains
  the fail-open path.

  Returns:
    bool: True when consume should pause on DB-full; False for drop-on-full.

  Examples:
    >>> isinstance(_listend_db_backpressure_mode_is_pause(), bool)
    True
  """
  try:
    return cfg.get_listend_db_ingest_backpressure() == "pause"
  except Exception:
    return False


def _is_amqp_channel_or_connection_dead(
    exc: BaseException | None = None,
    channel: Any = None,
) -> bool:
  """
  Return True when the AMQP channel or connection is unusable.

  Matches closed-channel/connection messages, pika wrong-state / stream-lost
  errors, and ``channel.is_closed`` / ``channel.connection.is_closed`` when
  those attributes exist. Idle-monitor depth probes use a separate short-lived
  connection and must not share this consumer channel.

  Args:
    exc (BaseException | None): Exception from ack/nack/consume, if any.
    channel (Any): Pika channel to inspect, or ``None``.

  Returns:
    bool: ``True`` when a full connection restart is required.

  Examples:
    >>> _is_amqp_channel_or_connection_dead(Exception("Channel is closed."))
    True
    >>> _is_amqp_channel_or_connection_dead(OSError("disk full"))
    False
  """
  if channel is not None:
    try:
      if getattr(channel, "is_closed", False):
        return True
    except Exception:
      pass
    try:
      conn = getattr(channel, "connection", None)
      if conn is not None and getattr(conn, "is_closed", False):
        return True
    except Exception:
      pass
  if exc is None:
    return False
  if isinstance(
      exc,
      (
          ChannelWrongStateError,
          ConnectionWrongStateError,
          StreamLostError,
          AMQPChannelError,
      ),
  ):
    return True
  msg = str(exc).lower()
  return "channel is closed" in msg or "connection is closed" in msg


def _request_amqp_full_reconnect(channel: Any, reason: str) -> None:
  """
  Stop consuming and close the BlockingConnection for a clean reconnect.

  Sets ``_amqp_reconnect_requested`` and logs at most once while the flag is
  already set (avoids per-message ERROR storms after the channel dies). Does
  not set ``_db_backpressure_pause``; pause/resume remains same-connection.

  Args:
    channel (Any): Pika channel whose connection should be torn down.
    reason (str): Short reason string for the reconnect log line.

  Returns:
    None

  Examples:
    >>> _request_amqp_full_reconnect(None, "Channel is closed.")  # doctest: +SKIP
  """
  global _amqp_reconnect_requested
  already = _amqp_reconnect_requested
  _amqp_reconnect_requested = True
  if already:
    return
  log_print(
      "AMQP reconnect requested (channel/connection dead): %s" % reason
  )
  try:
    if channel is not None and hasattr(channel, "stop_consuming"):
      channel.stop_consuming()
  except Exception as stop_err:
    if DEBUG:
      log_print("Failed to stop_consuming on AMQP reconnect: %s" % stop_err)
  try:
    conn = getattr(channel, "connection", None) if channel is not None else None
    if conn is not None and not getattr(conn, "is_closed", True):
      conn.close()
  except Exception as close_err:
    if DEBUG:
      log_print("Failed to close connection on AMQP reconnect: %s" % close_err)


def _request_db_backpressure_pause(channel: Any, delivery_tag: Any) -> None:
  """
  Nack+requeue without archive and stop consuming for DB backpressure.

  Args:
    channel (Any): Pika channel used by the consumer.
    delivery_tag (Any): Delivery tag to nack, or ``None``.

  Returns:
    None

  Examples:
    >>> _request_db_backpressure_pause(None, None)  # doctest: +SKIP
  """
  global _db_backpressure_pause
  pool = _live_db_ingest_pool_active()
  if pool is not None and not _db_backpressure_pause:
    try:
      pool.note_pause_enter()
    except Exception:
      pass
    # Pause duration is reported on the 10-minute idle-monitor line
    # (pause_s / paused) — no per-flap INFO here.
  _db_backpressure_pause = True
  try:
    if hasattr(channel, "basic_nack") and delivery_tag is not None:
      channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
  except Exception as nack_err:
    if DEBUG:
      log_print("Failed to nack on db backpressure pause: %s" % nack_err)
  try:
    channel.stop_consuming()
  except Exception as stop_err:
    if DEBUG:
      log_print("Failed to stop_consuming on db backpressure: %s" % stop_err)


def _wait_for_db_backpressure_resume(connection: Any) -> bool:
  """
  Wait until live DB queues drain below the resume watermark.

  Pumps ``process_data_events`` so RabbitMQ heartbeats stay alive.

  Args:
    connection (Any): Open pika ``BlockingConnection``.

  Returns:
    bool: ``True`` when resume is allowed; ``False`` when the connection is
    unusable or shutdown was requested.

  Examples:
    >>> _wait_for_db_backpressure_resume(None)  # doctest: +SKIP
  """
  global _db_backpressure_pause
  while _db_backpressure_pause:
    if _idle_monitor_stop_event.is_set():
      return False
    if connection is None or getattr(connection, "is_closed", True):
      return False
    pool = _live_db_ingest_pool_active()
    if pool is None or pool.should_resume_consume():
      _db_backpressure_pause = False
      if pool is not None:
        try:
          pool.note_pause_exit()
        except Exception:
          pass
      # Resume is visible via idle-monitor pause_s / paused=0 — no INFO.
      return True
    try:
      connection.process_data_events(time_limit=1)
    except Exception as exc:
      if DEBUG:
        log_print("process_data_events during db pause wait: %s" % exc)
      return False
  return True


def on_message(
  channel: Any,
  method_frame: Any,
  _header_frame: Any,
  body: Any,
) -> None:
  """
  Callback for each message: decode body, determine host, write/append to.
  
    host's.
  
    current file and optionally rotate. Acknowledges the message.
  
  When live DB ingest is on and backpressure mode is ``pause``, high-watermark
  queues cause nack+requeue without archive until the resume watermark. Mode
  ``drop`` (default) always archives and acks, shedding live DB enqueue.

  When ack/nack/ops hit a dead AMQP channel or connection, requests a full
  BlockingConnection restart (stop consume + close) instead of per-message
  ERROR spam; non-AMQP archive failures keep nack+requeue.

  Per-message logging of consumption/queue depth is avoided; instead, a
  background monitor thread reports aggregate rates every 10 minutes.

  Args:
    channel (Any): Channel passed to this helper.
    method_frame (Any): Method frame passed to this helper.
    _header_frame (Any):  header frame passed to this helper.
    body (Any): Value to inspect (typically a numeric scalar).

  Returns:
    None

  Examples:
    >>> on_message(None, None, None, None)  # doctest: +SKIP
  """
  global _db_backpressure_pause
  delivery_tag = getattr(method_frame, "delivery_tag", None)
  try:
    message = body.decode(errors="replace")
    pool = _live_db_ingest_pool_active()
    if pool is not None and _listend_db_backpressure_mode_is_pause():
      from hpcperfstats.dbload.lib.listend_db_ingest import (
          parse_host_from_monitor_payload,
      )

      try:
        peek_host = parse_host_from_monitor_payload(message)
      except Exception:
        peek_host = ""
      if (
          pool.should_pause_consume()
          or (peek_host and not pool.can_enqueue(peek_host, message))
      ):
        _request_db_backpressure_pause(channel, delivery_tag)
        return
    host = append_monitor_payload_to_archive(message)
    # Best-effort live DB dual-write; never blocks or fails the ack path.
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import submit_listend_db_ingest

      submit_listend_db_ingest(host, message)
    except Exception as submit_err:
      if DEBUG:
        log_print("listend db ingest submit error: %s" % submit_err)
    channel.basic_ack(delivery_tag=delivery_tag)
  except Exception as e:
    # Critical behavior: do not acknowledge on failure.
    if _is_amqp_channel_or_connection_dead(e, channel):
      # Dead channel: stop consume + close connection once; skip futile nack.
      _request_amqp_full_reconnect(channel, str(e))
      return
    # Requeue so the message remains on the server for later retry.
    log_print("Error processing message; leaving on server: %s" % e)
    try:
      if hasattr(channel, "basic_nack") and delivery_tag is not None:
        channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
    except Exception as nack_err:
      if DEBUG:
        log_print("Failed to nack message after processing error: %s" % nack_err)
      if _is_amqp_channel_or_connection_dead(nack_err, channel):
        _request_amqp_full_reconnect(channel, str(nack_err))
    return


def _idle_monitor() -> None:
  """
  Periodically report messages consumed in the last 10 minutes and queue depth.
  
  Runs every IDLE_CHECK_INTERVAL seconds, but only logs once per
  MESSAGE_WINDOW_SECONDS window.
  
  Returns:
    None
  
  Examples:
    >>> _idle_monitor()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title("", script_name="listend.py", role="idle-monitor")
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

    # Queue depth via a dedicated connection (see _get_rmq_queue_depth_for_monitor).
    queue_depth = _get_rmq_queue_depth_for_monitor()

    db_suffix = ""
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import get_listend_db_ingest_pool

      pool = get_listend_db_ingest_pool()
      if pool is not None and pool.enabled and pool._started:
        db_suffix = "; " + pool.format_idle_monitor_suffix()
    except Exception:
      pass

    log_print(
        "Messages consumed in the last 10 minutes: %d; "
        "messages waiting to be consumed: %d; "
        "current file unlinks (last 10 minutes): %d%s" %
        (count_last_10, queue_depth, unlink_count_last_10, db_suffix))

    _last_idle_report_time = now


def main() -> None:
  """
  Run this module's command-line entrypoint.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``main`` hits a ``Exception`` failure path.
  
  Examples:
    >>> main()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_process_title

  set_daemon_process_title(name="listend.py", role="main")
  global _idle_thread_started
  global _recent_host_worker_thread_started
  global _amqp_reconnect_requested
  # Use a mutable container so the SIGTERM handler can update state without
  # relying on `nonlocal` (which is only valid for enclosing function scopes).
  sigterm_received = {"value": False}
  connection = None

  def _sigterm_handler(signum: Any, frame: Any) -> None:
    """
    Internal helper to handle sigterm handler.
    
    Args:
      signum (Any): Signum passed to this helper.
      frame (Any): Frame passed to this helper.
    
    Returns:
      None
    
    Raises:
      SystemExit: Raised when ``_sigterm_handler`` hits a ``SystemExit``
      failure path.
    
    Examples:
      >>> _sigterm_handler(None, None)  # doctest: +SKIP
    """
    sigterm_received["value"] = True
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
      if not _recent_host_worker_thread_started:
        recent_host_thread = Thread(target=_recent_host_worker, daemon=True)
        recent_host_thread.start()
        _recent_host_worker_thread_started = True

      try:
        from hpcperfstats.dbload.lib.listend_db_ingest import (
            start_listend_db_ingest_pool,
        )

        start_listend_db_ingest_pool()
      except Exception as pool_err:
        log_print("Failed to start listend db ingest pool: %s" % pool_err)

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

          live_pool = _live_db_ingest_pool_active()
          if live_pool is not None and _listend_db_backpressure_mode_is_pause():
            # Prefetch=1 so pause leaves work on the broker ready queue.
            channel.basic_qos(prefetch_count=1)

          # Log consume-start once per RabbitMQ connection; pause/resume
          # rebinds must not re-emit (that was log spam with backpressure).
          consume_start_logged = False

          # Inner loop: consume until connection error, or pause for DB
          # backpressure then resume on the same connection.
          while not _idle_monitor_stop_event.is_set():
            if _db_backpressure_pause:
              if not _wait_for_db_backpressure_resume(connection):
                break
              if connection.is_closed:
                break
              # Channel may still be open after stop_consuming; re-bind.
              try:
                if (
                    live_pool is not None
                    and _listend_db_backpressure_mode_is_pause()
                ):
                  channel.basic_qos(prefetch_count=1)
              except Exception:
                break

            # Cancel any prior consumer tags before (re)registering.
            try:
              channel.cancel()
            except Exception:
              pass
            channel.basic_consume(cfg.get_rmq_queue(), on_message)
            if not consume_start_logged:
              log_print("Begining Consume from queue: " + cfg.get_rmq_queue())
              consume_start_logged = True
            try:
              channel.start_consuming()
            except (KeyboardInterrupt, SystemExit):
              try:
                channel.stop_consuming()
              except Exception:
                pass
              raise
            except StreamLostError as e:
              # Connection dropped (e.g. broker restart or idle timeout). Treat as a
              # normal shutdown condition and only log in DEBUG mode to avoid noisy
              # "Error while consuming" messages like "pop from an empty deque".
              if DEBUG:
                log_print("RabbitMQ stream lost while consuming: %s" % e)
              break
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
              break
            except Exception as e:
              # Handle other connection-level errors from pika without raising during
              # shutdown/cleanup. We will attempt to reconnect after a short delay.
              log_print("Error while consuming from RabbitMQ: %s" % e)
              break

            if _db_backpressure_pause:
              # stop_consuming from on_message — wait then resume.
              continue
            # Dead channel/connection or unexpected stop_consuming → reconnect.
            if _amqp_reconnect_requested:
              _amqp_reconnect_requested = False
            break
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
          _amqp_reconnect_requested = False

        # If we reach this point without breaking, sleep briefly before attempting
        # to reconnect. This avoids tight reconnect loops that could cause
        # excessive CPU usage or log spam.
        time.sleep(5)
  finally:
    _idle_monitor_stop_event.set()
    _recent_host_worker_stop_event.set()
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import stop_listend_db_ingest_pool

      stop_listend_db_ingest_pool()
    except Exception:
      pass
    try:
      if connection and not connection.is_closed:
        connection.close()
    except Exception:
      pass
    if sigterm_received["value"]:
      send_sigchld_to_parent()
    signal.signal(signal.SIGTERM, previous_sigterm_handler)


if __name__ == "__main__":
  main()
