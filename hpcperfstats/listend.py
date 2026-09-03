"""
RabbitMQ listener daemon. Consumes messages from the configured queue and
appends payloads to per-host files under the archive directory. Single- instance
via file lock.

Attributes:
  DEBUG: Attribute.
  IDLE_CHECK_INTERVAL: Attribute.
  MESSAGE_WINDOW_SECONDS: Attribute.
  RECENT_HOST_TTL_SECONDS: Attribute.
  ArchiveAppendResult: Result of a durable archive append (host + byte range).
  _idle_monitor_stop_event: Attribute.
  _idle_thread_started: Attribute.
  _last_idle_report_time: Attribute.
  _last_message_time: Attribute.
  _message_timestamps: Attribute.
  _db_backpressure_pause: Attribute.
  _amqp_reconnect_requested: Attribute.
  _amqp_connection_generation: Consume-session id; stale ack/nack callbacks
    no-op after ``set_amqp_connection`` so leftover tags cannot 406.
  _amqp_reconnect_backoff_seconds: Attribute.
  _consume_attach_monotonic: Attribute.
  _amqp_connection: Current BlockingConnection for threadsafe ack/nack.
  _amqp_channel: Current consume channel (ack/nack via connection callbacks).
  _archive_queues: Host-affine threading.Queue list for archive workers.
  _archive_threads: Daemon archive Thread list.
  _archive_pool_stop: Event to stop archive workers.
  _archive_pool_started: Whether the archive thread pool is running.
  _archive_pool_n: Number of archive worker threads.
  _archive_pool_lock: Guard for start/stop of the archive pool.
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

from typing import Any, NamedTuple

import os
import queue
import signal
import sys
import time
from collections import deque
from threading import Event, Lock, Thread, current_thread, main_thread
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
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.shutdown_utils import send_sigchld_to_parent
from hpcperfstats.lib.monitor_identity import (
    parse_monitor_identity_from_dollar_message,
    set_monitor_identity,
)
from hpcperfstats.lib.rmq_quorum_queue import (
    AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS,
    AMQP_RECONNECT_STABLE_CONSUME_SECONDS,
    declare_durable_quorum_queue,
    is_amqp_peer_reset_reconnect_error,
    is_quorum_consume_setup_error,
    listend_amqp_connection_parameters,
    next_amqp_reconnect_backoff_seconds,
    should_use_amqp_exponential_reconnect_backoff,
)

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds
RECENT_HOST_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week
# Monitor schema dumps start with ``$`` then ``1 <fqdn>``; that ``1`` is not
# a sample unix second. Real host_data timestamps are post-2001.
_MIN_PLAUSIBLE_UNIX_SECONDS = 1_000_000_000
_DIGIT_EPOCH_NAME_MAX_ATTEMPTS = 10_000


class ArchiveAppendResult(NamedTuple):
  """Durable archive append outcome for ack and path+range DB enqueue.

  Attributes:
    host: FQDN host token from the monitor payload.
    path: Absolute path of the ``current`` file written.
    offset: Byte offset of this payload in ``path`` (0 for ``$`` rewrite).
    length: UTF-8 byte length of the written payload.
  """

  host: str
  path: str
  offset: int
  length: int


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
# Outer-loop reconnect sleep after 541 / connection failure (reset on consume).
_amqp_reconnect_backoff_seconds = AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS
# Monotonic time when the current consume session started (Begining Consume).
_consume_attach_monotonic: float | None = None
# Consume BlockingConnection / channel for archive-thread threadsafe ack/nack.
_amqp_connection: Any = None
_amqp_channel: Any = None
# Bumped on every ``set_amqp_connection`` so deferred acks from a prior
# BlockingConnection cannot ``basic_ack`` an unknown delivery tag (406).
_amqp_connection_generation = 0
# Host-affine archive thread pool (off AMQP callback thread).
_archive_queues: list = []
_archive_threads: list = []
_archive_pool_stop = Event()
_archive_pool_started = False
_archive_pool_n = 0
_archive_pool_lock = Lock()


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


def _digit_epoch_start_ts_for_new_current(
    current_path: str,
    *,
    fallback_ts: int,
) -> int:
  """Return digit epoch start for a newly written ``current`` file.

  Prefers the first plausible sample unix second in ``current_path``.
  Falls back to ``fallback_ts`` when the ``$`` payload has no sample
  timestamp yet (schema-only rotate).

  Args:
    current_path (str): Path to the live ``current`` file (already written).
    fallback_ts (int): Wall-clock unix seconds when no sample ts is found.

  Returns:
    int: Starting digit epoch seconds (caller steps +1 on name conflict).

  Examples:
    >>> _digit_epoch_start_ts_for_new_current("/no/file", fallback_ts=9)
    9
  """
  first = _get_first_timestamp_seconds(current_path, use_lock=False)
  if first is not None:
    return int(first)
  return int(fallback_ts)


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


def append_monitor_payload_to_archive(message: Any) -> ArchiveAppendResult:
  """
  Append one monitor payload to the per-host archive ``current`` file.

  Used by the long-running daemon and by ``listend_drain`` integration tests.
  Returns host plus the byte range written so live-DB enqueue can fall back
  to path+offset without re-pickling the body.

  Args:
    message (Any): Monitor payload string (UTF-8 text).

  Returns:
    ArchiveAppendResult: Host, path, byte offset, and UTF-8 length.

  Raises:
    RuntimeError: When ``$`` rotate cannot hardlink ``current`` safely.
    ValueError: When the payload is empty or malformed.

  Examples:
    >>> append_monitor_payload_to_archive("")  # doctest: +SKIP
  """
  if not message:
    raise ValueError("Empty message body")
  if not isinstance(message, str):
    message = message.decode("utf-8", errors="replace")

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
  payload_bytes = message.encode("utf-8")
  payload_len = len(payload_bytes)
  write_offset = 0
  unlinked_current = False
  if message[0] == "$":
    # Wall clock is only the safety-link cutoff / fallback when the ``$``
    # payload has no sample timestamp. New digit names prefer the first
    # plausible sample second in the newly written ``current`` file.
    rotate_wall_ts = int(time.time())
    with file_write_lock(current_path):
      if os.path.exists(current_path):
        if not _current_is_hardlinked_to_digit_epoch(host_dir, current_path):
          _ensure_current_hardlinked_to_timestamp(
              host_dir, current_path, rotate_wall_ts)
          if not _current_is_hardlinked_to_digit_epoch(
              host_dir, current_path):
            raise RuntimeError(
                "current is not hardlinked to a digit epoch before unlink")

        os.unlink(current_path)
        unlinked_current = True

      with open(current_path, "wb") as fd:
        fd.write(payload_bytes)
      write_offset = 0
      # Name the new segment from first sample ts in the file; +1 on conflict.
      epoch_ts = _digit_epoch_start_ts_for_new_current(
          current_path, fallback_ts=rotate_wall_ts,
      )
      _link_current_to_unique_digit_epoch(
          host_dir, current_path, epoch_ts, step=1)
      # Epoch name and current share an inode until the next ``$`` rotation.
      # sync_timedb skips epoch files same-inode-as-current to avoid read races.
  else:
    with file_write_lock(current_path):
      with open(current_path, "ab") as fd:
        write_offset = fd.tell()
        fd.write(payload_bytes)
  _enqueue_recent_host_update(host)
  # Redis-only identity snapshot on ``$`` rotation (tolerant if ``$build``
  # absent). Does not change ack, pause/resume, or archive/DB gate semantics.
  if message[0] == "$":
    try:
      identity = parse_monitor_identity_from_dollar_message(
          message,
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

  return ArchiveAppendResult(
      host=host,
      path=current_path,
      offset=int(write_offset),
      length=int(payload_len),
  )


def _get_rmq_queue_depth_for_monitor() -> int | str:
  """
  Return ``message_count`` for the configured queue.

  Uses a **separate** short-lived connection. Pika ``BlockingConnection`` and
  its channels are not thread-safe; the idle monitor runs in a background
  thread and must not touch the channel used by ``start_consuming()`` in the
  main thread (that sharing caused ``Channel is closed``, transport state
  errors, and ``IndexError: pop from an empty deque`` in pika).

  Depth probe is **passive-only** so the idle thread never non-passive-declares
  (avoids Ra checkout contention with consume setup).

  Returns:
    int | str: Message count integer, or ``\"n/a\"`` when the probe fails.

  Examples:
    >>> _get_rmq_queue_depth_for_monitor()  # doctest: +SKIP
  """
  parameters = listend_amqp_connection_parameters(cfg.get_rmq_server())
  connection = None
  try:
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    q = channel.queue_declare(
        queue=cfg.get_rmq_queue(), durable=True, passive=True)
    return q.method.message_count
  except Exception as e:
    if DEBUG:
      log_print("Failed to get queue depth in monitor: %s" % e)
    return "n/a"
  finally:
    if connection is not None:
      try:
        if not connection.is_closed:
          connection.close()
      except Exception:
        pass


def _maybe_reset_amqp_reconnect_backoff_after_stable_consume() -> None:
  """
  Reset reconnect backoff after a stable consume session (≥30s).

  Backoff must not reset on ``Begining Consume`` alone; only after the
  consumer has been attached long enough to indicate broker health.

  Returns:
    None

  Examples:
    >>> _maybe_reset_amqp_reconnect_backoff_after_stable_consume()  # doctest: +SKIP
  """
  global _amqp_reconnect_backoff_seconds
  global _consume_attach_monotonic
  if _consume_attach_monotonic is None:
    return
  if (
      time.monotonic() - _consume_attach_monotonic
      >= AMQP_RECONNECT_STABLE_CONSUME_SECONDS
  ):
    _amqp_reconnect_backoff_seconds = AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS


def _apply_amqp_reconnect_backoff() -> int:
  """
  Grow outer-loop reconnect sleep and return the new value.

  Returns:
    int: Next reconnect sleep in seconds.

  Examples:
    >>> _apply_amqp_reconnect_backoff()  # doctest: +SKIP
  """
  global _amqp_reconnect_backoff_seconds
  nxt = next_amqp_reconnect_backoff_seconds(_amqp_reconnect_backoff_seconds)
  _amqp_reconnect_backoff_seconds = nxt
  return nxt


def _close_amqp_channel_and_connection_gracefully(
    channel: Any,
    connection: Any,
    *,
    stop_consuming: bool = False,
) -> None:
  """
  Tear down channel then connection to reduce broker termination timeouts.

  Args:
    channel (Any): Pika channel, or ``None``.
    connection (Any): Pika ``BlockingConnection``, or ``None``.
    stop_consuming (bool): When ``True``, call ``stop_consuming`` first.

  Returns:
    None

  Examples:
    >>> _close_amqp_channel_and_connection_gracefully(None, None)  # doctest: +SKIP
  """
  if stop_consuming and channel is not None:
    try:
      if hasattr(channel, "stop_consuming"):
        channel.stop_consuming()
    except Exception:
      pass
  if channel is not None:
    try:
      if not getattr(channel, "is_closed", True):
        channel.close()
    except Exception:
      pass
  if connection is not None:
    try:
      if not getattr(connection, "is_closed", True):
        connection.close()
    except Exception:
      pass


def _bind_listend_consume(
  channel: Any,
  queue_name: str,
  *,
  had_consumer: bool,
) -> None:
  """
  Register ``on_message``; cancel only when a prior consumer was bound.

  Skipping ``cancel()`` on a fresh channel avoids an extra quorum Ra checkout
  before the first ``basic_consume`` (541 consume-setup timeouts).

  Args:
    channel (Any): Open pika channel.
    queue_name (str): Queue to consume from.
    had_consumer (bool): ``True`` when this channel already had an active
      consumer that should be cancelled before rebinding.

  Returns:
    None

  Examples:
    >>> _bind_listend_consume(None, "q", had_consumer=False)  # doctest: +SKIP
  """
  if had_consumer:
    try:
      channel.cancel()
    except Exception:
      pass
  channel.basic_consume(queue_name, on_message)


def _format_amqp_consume_error(exc: BaseException) -> str:
  """
  Return a non-empty consume-error string for logs.

  Some pika/AMQP failures stringify to ``\"\"`` (production: bare
  ``Error while consuming from RabbitMQ:``). Fall back to the exception
  type and ``repr(args)`` so the reconnect loop still records a reason.

  Args:
    exc (BaseException): Exception raised from connect or consume.

  Returns:
    str: ``str(exc)`` when non-empty; otherwise type name plus args.

  Examples:
    >>> _format_amqp_consume_error(Exception("Channel is closed."))
    'Channel is closed.'
    >>> _format_amqp_consume_error(Exception())
    'Exception'
  """
  text = str(exc).strip()
  if text:
    return text
  args = getattr(exc, "args", ())
  if args:
    return "%s %r" % (type(exc).__name__, args)
  return type(exc).__name__


def _log_amqp_outer_loop_error(exc: BaseException) -> str:
  """
  Log an outer reconnect-loop failure; return a coarse error kind.

  Args:
    exc (BaseException): Exception from connect, declare, or consume setup.

  Returns:
    str: ``\"quorum_consume_setup\"`` for 541 / Ra checkout timeouts,
    ``\"peer_reset\"`` for connection reset / stream lost / handshake timeout,
    else ``\"connection\"``.

  Examples:
    >>> _log_amqp_outer_loop_error(Exception("x"))  # doctest: +SKIP
  """
  if is_quorum_consume_setup_error(exc):
    log_print("AMQP quorum consume-setup timeout (541): %s" % exc)
    return "quorum_consume_setup"
  if is_amqp_peer_reset_reconnect_error(exc):
    log_print("AMQP peer reset / stream lost / handshake timeout: %s" % exc)
    return "peer_reset"
  log_print("Error establishing RabbitMQ connection: %s" % exc)
  return "connection"


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

  Matches closed-channel/connection messages (including pika
  ``closed or closing connection`` and 406 ``unknown delivery tag`` /
  ``PRECONDITION_FAILED``), pika wrong-state / stream-lost errors, and
  ``channel.is_closed`` / ``connection.is_closed`` / ``is_closing`` when
  those attributes exist. Idle-monitor depth probes use a separate
  short-lived connection and must not share this consumer channel.

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
      if conn is not None and (
          getattr(conn, "is_closed", False)
          or getattr(conn, "is_closing", False)
      ):
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
  dead_markers = (
      "channel is closed",
      "connection is closed",
      "closed or closing connection",
      "unknown delivery tag",
      "precondition_failed",
  )
  return any(marker in msg for marker in dead_markers)


def _is_amqp_io_thread() -> bool:
  """
  Return True when this thread owns the consume BlockingConnection I/O.

  ``listend.main`` drives ``process_data_events`` / ``start_consuming`` on the
  process MainThread. Archive and idle-monitor threads must not close that
  connection: pika ``BlockingConnection`` is not thread-safe, and
  ``add_callback_threadsafe()`` on a closed or closing connection raises
  ``ConnectionWrongStateError``.

  Returns:
    bool: ``True`` on the process MainThread.

  Examples:
    >>> isinstance(_is_amqp_io_thread(), bool)
    True
  """
  try:
    return current_thread() is main_thread()
  except Exception:
    return False


def _consume_connection_unusable(conn: Any, channel: Any = None) -> bool:
  """
  Return True when the consume connection cannot accept callbacks or acks.

  Args:
    conn (Any): Stored ``BlockingConnection``, or ``None``.
    channel (Any): Consume channel, or ``None``.

  Returns:
    bool: ``True`` when ``conn`` is missing, closed, closing, or the
    channel is already dead.

  Examples:
    >>> _consume_connection_unusable(None)
    True
  """
  if conn is None:
    return True
  try:
    if getattr(conn, "is_closed", False):
      return True
  except Exception:
    return True
  try:
    if getattr(conn, "is_closing", False):
      return True
  except Exception:
    pass
  return _is_amqp_channel_or_connection_dead(None, channel)


def _consume_amqp_for_threadsafe_op() -> tuple[Any, Any, int] | None:
  """
  Return the current consume connection snapshot, or ``None`` if unusable.

  Marks a connection-scoped reconnect when the stored connection is already
  closed/closing so archive threads never call ``add_callback_threadsafe``
  on a dead ``BlockingConnection``.

  Returns:
    tuple[Any, Any, int] | None: ``(connection, channel, generation)`` when
    an ack/nack may be scheduled; ``None`` when the consume session is
    missing or already dead.

  Examples:
    >>> _consume_amqp_for_threadsafe_op() is None
    True
  """
  conn = _amqp_connection
  ch = _amqp_channel
  gen = _amqp_connection_generation
  if conn is None or ch is None:
    return None
  if _amqp_reconnect_requested or _consume_connection_unusable(conn, ch):
    _request_amqp_full_reconnect(
        ch, "consume connection unusable before ack/nack"
    )
    return None
  return conn, ch, gen


def _consume_amqp_callback_stale(conn: Any, generation: int) -> bool:
  """
  Return True when a deferred ack/nack belongs to a prior consume session.

  Args:
    conn (Any): Connection captured when the callback was scheduled.
    generation (int): ``_amqp_connection_generation`` at schedule time.

  Returns:
    bool: ``True`` when reconnect ran or the stored connection changed.

  Examples:
    >>> _consume_amqp_callback_stale(object(), -1)
    True
  """
  return (
      _amqp_connection_generation != generation
      or _amqp_reconnect_requested
      or _amqp_connection is not conn
  )


def _request_amqp_full_reconnect(channel: Any, reason: str) -> None:
  """
  Request a consume-connection rebuild; close only on the AMQP I/O thread.

  Sets ``_amqp_reconnect_requested`` and logs at most once while the flag is
  already set (avoids per-message ERROR storms after the channel dies). Does
  not set ``_db_backpressure_pause``; pause/resume remains same-connection.

  Archive workers (and any non-MainThread caller) only set the flag: pika
  ``BlockingConnection`` is not thread-safe, so ``stop_consuming`` /
  ``close()`` from ``listend-archive-N`` races the consume loop and can
  leave leftover ``add_callback_threadsafe`` acks that 406 on the next
  channel. MainThread still tears down channel then connection.

  Args:
    channel (Any): Pika channel whose connection should be torn down on
      the I/O thread, or inspected for ``connection``.
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
  if _is_amqp_io_thread():
    conn = getattr(channel, "connection", None) if channel is not None else None
    _close_amqp_channel_and_connection_gracefully(
        channel, conn, stop_consuming=True)
    return
  _schedule_amqp_reconnect_teardown_on_io_thread(channel)


def _schedule_amqp_reconnect_teardown_on_io_thread(channel: Any) -> None:
  """
  Ask the consume I/O thread to stop consuming and close the connection.

  Archive threads must not call ``stop_consuming`` or ``close`` themselves.
  When the consume connection is already closed or closing, skip scheduling
  (``add_callback_threadsafe`` would raise; ``start_consuming`` will exit).

  Args:
    channel (Any): Channel that reported the failure, or ``None``.

  Returns:
    None

  Examples:
    >>> _schedule_amqp_reconnect_teardown_on_io_thread(None)
  """

  def _teardown() -> None:
    """
    Close the consume channel/connection on the pika I/O thread.

    Returns:
      None

    Examples:
      >>> _teardown()  # doctest: +SKIP
    """
    if not _amqp_reconnect_requested:
      return
    ch = _amqp_channel if _amqp_channel is not None else channel
    conn = (
        _amqp_connection
        if _amqp_connection is not None
        else (getattr(ch, "connection", None) if ch is not None else None)
    )
    _close_amqp_channel_and_connection_gracefully(
        ch, conn, stop_consuming=True
    )

  conn = _amqp_connection
  if conn is None and channel is not None:
    conn = getattr(channel, "connection", None)
  if conn is None:
    return
  try:
    if getattr(conn, "is_closed", False) or getattr(conn, "is_closing", False):
      return
  except Exception:
    return
  try:
    if hasattr(conn, "add_callback_threadsafe"):
      conn.add_callback_threadsafe(_teardown)
  except Exception:
    pass


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


def set_amqp_connection(connection: Any, channel: Any) -> None:
  """
  Store the consume BlockingConnection/channel for archive-thread ack/nack.

  Increments ``_amqp_connection_generation`` so deferred ack/nack callbacks
  from a prior session no-op (avoids 406 unknown delivery tag).

  Args:
    connection (Any): Pika ``BlockingConnection``, or ``None`` to clear.
    channel (Any): Consume channel, or ``None`` to clear.

  Returns:
    None

  Examples:
    >>> set_amqp_connection(None, None)
  """
  global _amqp_connection, _amqp_channel, _amqp_connection_generation
  _amqp_connection = connection
  _amqp_channel = channel
  _amqp_connection_generation += 1


def clear_amqp_connection() -> None:
  """
  Clear stored AMQP connection/channel (reconnect or shutdown).

  Returns:
    None

  Examples:
    >>> clear_amqp_connection()
  """
  set_amqp_connection(None, None)


def archive_queue_depth() -> int:
  """
  Return total pending items across host-affine archive queues.

  Returns:
    int: Sum of ``qsize()`` across archive queues (0 when pool stopped).

  Examples:
    >>> archive_queue_depth() >= 0
    True
  """
  total = 0
  for q in _archive_queues:
    try:
      total += int(q.qsize())
    except Exception:
      pass
  return total


def _threadsafe_basic_ack(delivery_tag: Any) -> None:
  """
  Schedule ``basic_ack`` on the consume connection I/O thread.

  Skips ``add_callback_threadsafe`` when the consume connection is already
  closed/closing (pika raises ``ConnectionWrongStateError``). Deferred
  callbacks no-op after ``set_amqp_connection`` so leftover tags cannot
  406 on a new channel.

  Args:
    delivery_tag (Any): AMQP delivery tag to acknowledge.

  Returns:
    None

  Examples:
    >>> _threadsafe_basic_ack(1)  # doctest: +SKIP
  """
  if delivery_tag is None:
    return
  snapshot = _consume_amqp_for_threadsafe_op()
  if snapshot is None:
    return
  conn, ch, gen = snapshot

  def _ack() -> None:
    """
    Run ``basic_ack`` on the connection thread.

    Returns:
      None

    Examples:
      >>> _ack()  # doctest: +SKIP
    """
    if _consume_amqp_callback_stale(conn, gen):
      return
    try:
      ch.basic_ack(delivery_tag=delivery_tag)
    except Exception as exc:
      if _is_amqp_channel_or_connection_dead(exc, ch):
        _request_amqp_full_reconnect(ch, str(exc))
      elif DEBUG:
        log_print("threadsafe basic_ack failed: %s" % exc)

  try:
    if hasattr(conn, "add_callback_threadsafe"):
      conn.add_callback_threadsafe(_ack)
    else:
      _ack()
  except Exception as exc:
    if _is_amqp_channel_or_connection_dead(exc, ch):
      _request_amqp_full_reconnect(ch, str(exc))
    elif DEBUG:
      log_print("add_callback_threadsafe ack failed: %s" % exc)


def _threadsafe_basic_nack(delivery_tag: Any, *, requeue: bool = True) -> None:
  """
  Schedule ``basic_nack`` on the consume connection I/O thread.

  Same closed-connection and generation guards as ``_threadsafe_basic_ack``.

  Args:
    delivery_tag (Any): AMQP delivery tag to negatively acknowledge.
    requeue (bool): When ``True``, requeue the message on the broker.

  Returns:
    None

  Examples:
    >>> _threadsafe_basic_nack(1)  # doctest: +SKIP
  """
  if delivery_tag is None:
    return
  snapshot = _consume_amqp_for_threadsafe_op()
  if snapshot is None:
    return
  conn, ch, gen = snapshot

  def _nack() -> None:
    """
    Run ``basic_nack`` on the connection thread.

    Returns:
      None

    Examples:
      >>> _nack()  # doctest: +SKIP
    """
    if _consume_amqp_callback_stale(conn, gen):
      return
    try:
      if hasattr(ch, "basic_nack"):
        ch.basic_nack(delivery_tag=delivery_tag, requeue=requeue)
    except Exception as exc:
      if _is_amqp_channel_or_connection_dead(exc, ch):
        _request_amqp_full_reconnect(ch, str(exc))
      elif DEBUG:
        log_print("threadsafe basic_nack failed: %s" % exc)

  try:
    if hasattr(conn, "add_callback_threadsafe"):
      conn.add_callback_threadsafe(_nack)
    else:
      _nack()
  except Exception as exc:
    if _is_amqp_channel_or_connection_dead(exc, ch):
      _request_amqp_full_reconnect(ch, str(exc))
    elif DEBUG:
      log_print("add_callback_threadsafe nack failed: %s" % exc)


def _archive_and_submit_then_ack(delivery_tag: Any, message: str) -> None:
  """
  Archive payload, best-effort live-DB submit, then ack (or nack on I/O error).

  Hard order: durable filesystem archive → submit → ACK. Never ACK without
  a successful archive append.

  Args:
    delivery_tag (Any): AMQP delivery tag.
    message (str): Decoded monitor payload.

  Returns:
    None

  Examples:
    >>> _archive_and_submit_then_ack(1, "x")  # doctest: +SKIP
  """
  try:
    result = append_monitor_payload_to_archive(message)
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import (
          submit_listend_db_ingest,
      )

      submit_listend_db_ingest(
          result.host,
          message,
          archive_path=result.path,
          offset=result.offset,
          length=result.length,
      )
    except Exception as submit_err:
      if DEBUG:
        log_print("listend db ingest submit error: %s" % submit_err)
    _threadsafe_basic_ack(delivery_tag)
  except Exception as e:
    if _is_amqp_channel_or_connection_dead(e, _amqp_channel):
      _request_amqp_full_reconnect(_amqp_channel, str(e))
      return
    log_print("Error processing message; leaving on server: %s" % e)
    _threadsafe_basic_nack(delivery_tag, requeue=True)


def _archive_worker_main(worker_idx: int, work_queue: queue.Queue) -> None:
  """
  Host-affine archive thread: archive → submit → threadsafe ack/nack.

  Args:
    worker_idx (int): Archive worker index (0..N-1).
    work_queue (queue.Queue): Per-worker ``(delivery_tag, message)`` queue.

  Returns:
    None

  Examples:
    >>> _archive_worker_main(0, queue.Queue())  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title(
      "",
      script_name="listend.py",
      role="archive-%d" % int(worker_idx),
  )
  while not _archive_pool_stop.is_set():
    try:
      item = work_queue.get(timeout=1.0)
    except queue.Empty:
      continue
    if item is None:
      work_queue.task_done()
      break
    try:
      delivery_tag, message = item
      _archive_and_submit_then_ack(delivery_tag, message)
    finally:
      try:
        work_queue.task_done()
      except Exception:
        pass


def start_listend_archive_pool(n_threads: int | None = None) -> int:
  """
  Start host-affine archive writer threads (idempotent).

  Args:
    n_threads (int | None): Worker count; default from INI
      ``listend_archive_worker_threads``.

  Returns:
    int: Number of archive worker threads started (or already running).

  Examples:
    >>> start_listend_archive_pool(1)  # doctest: +SKIP
  """
  global _archive_pool_started, _archive_pool_n
  with _archive_pool_lock:
    if _archive_pool_started:
      return int(_archive_pool_n)
    n = int(
        n_threads
        if n_threads is not None
        else cfg.get_listend_archive_worker_threads()
    )
    n = max(1, n)
    _archive_pool_stop.clear()
    _archive_queues.clear()
    _archive_threads.clear()
    for i in range(n):
      q: queue.Queue = queue.Queue()
      t = Thread(
          target=_archive_worker_main,
          args=(i, q),
          name="listend-archive-%d" % i,
          daemon=True,
      )
      _archive_queues.append(q)
      _archive_threads.append(t)
      t.start()
    _archive_pool_n = n
    _archive_pool_started = True
    log_print(
        "listend archive pool started threads=%d" % n,
        flush=True,
    )
    return n


def stop_listend_archive_pool(*, join_timeout: float = 15.0) -> None:
  """
  Stop archive workers and drain queues (best-effort).

  Args:
    join_timeout (float): Seconds to wait for each worker join.

  Returns:
    None

  Examples:
    >>> stop_listend_archive_pool()
  """
  global _archive_pool_started, _archive_pool_n
  with _archive_pool_lock:
    if not _archive_pool_started:
      return
    _archive_pool_stop.set()
    for q in _archive_queues:
      try:
        q.put_nowait(None)
      except Exception:
        pass
    deadline = time.monotonic() + max(0.1, float(join_timeout))
    for t in _archive_threads:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        break
      try:
        t.join(timeout=remaining)
      except Exception:
        pass
    _archive_queues.clear()
    _archive_threads.clear()
    _archive_pool_n = 0
    _archive_pool_started = False


def _dispatch_to_archive_pool(
  delivery_tag: Any,
  message: str,
  host: str,
) -> None:
  """
  Put ``(delivery_tag, message)`` on the host-affine archive queue.

  Args:
    delivery_tag (Any): AMQP delivery tag.
    message (str): Decoded monitor payload.
    host (str): Host token for affine index.

  Returns:
    None

  Examples:
    >>> _dispatch_to_archive_pool(1, "x", "h")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.listend_db_ingest import host_affine_worker_index

  n = max(1, int(_archive_pool_n) if _archive_pool_n else 1)
  idx = host_affine_worker_index(host, n)
  _archive_queues[idx].put((delivery_tag, message))


def on_message(
  channel: Any,
  method_frame: Any,
  _header_frame: Any,
  body: Any,
) -> None:
  """
  Consume callback: pause-check, then host-affine archive dispatch (or sync).

  When the archive pool is running, parse host cheaply, enqueue
  ``(delivery_tag, message)``, and return without archive/ack on this thread.
  Archive workers perform durable append → best-effort DB submit → threadsafe
  ack. Without a started pool (unit tests), process synchronously on this
  thread.

  When live DB ingest is on and backpressure mode is ``pause``, high-watermark
  queues cause nack+requeue without archive until the resume watermark. Mode
  ``drop`` (default) always archives and acks, shedding live DB enqueue.

  Args:
    channel (Any): Pika channel delivering the message.
    method_frame (Any): Method frame with ``delivery_tag``.
    _header_frame (Any): Unused AMQP header frame.
    body (Any): Raw message body bytes.

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

    if _archive_pool_started and _archive_queues:
      from hpcperfstats.dbload.lib.listend_db_ingest import (
          parse_host_from_monitor_payload,
      )

      host = parse_host_from_monitor_payload(message)
      _dispatch_to_archive_pool(delivery_tag, message, host)
      return

    # Sync path (tests / pool not started): archive → submit → ack.
    result = append_monitor_payload_to_archive(message)
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import submit_listend_db_ingest

      submit_listend_db_ingest(
          result.host,
          message,
          archive_path=result.path,
          offset=result.offset,
          length=result.length,
      )
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

    archive_suffix = ""
    try:
      if _archive_pool_started:
        archive_suffix = "; archive_q_depth=%d" % archive_queue_depth()
    except Exception:
      pass

    log_print(
        "Messages consumed in the last 10 minutes: %d; "
        "messages waiting to be consumed: %s; "
        "current file unlinks (last 10 minutes): %d%s%s" %
        (count_last_10, queue_depth, unlink_count_last_10, db_suffix,
         archive_suffix))

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
  from hpcperfstats.dbload.lib.python_abi_startup_log import log_python_abi_startup

  set_daemon_process_title(name="listend.py", role="main")
  log_python_abi_startup()
  global _idle_thread_started
  global _recent_host_worker_thread_started
  global _amqp_reconnect_requested
  global _amqp_reconnect_backoff_seconds
  global _consume_attach_monotonic
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

      try:
        start_listend_archive_pool()
      except Exception as arch_err:
        log_print("Failed to start listend archive pool: %s" % arch_err)

      # Outer loop: keep the daemon running indefinitely by reconnecting to
      # RabbitMQ on failure instead of exiting. The process will typically only
      # terminate when it receives a signal (e.g. SIGTERM) from the service
      # manager.
      while True:
        log_print("Starting Connection")
        parameters = listend_amqp_connection_parameters(cfg.get_rmq_server())
        reconnect_sleep_s = AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS
        channel = None
        try:
          connection = pika.BlockingConnection(parameters)

          channel = connection.channel()
          set_amqp_connection(connection, channel)
          queue_name = cfg.get_rmq_queue()
          channel = declare_durable_quorum_queue(channel, queue_name)
          set_amqp_connection(connection, channel)
          # Report how many messages are waiting to be consumed at startup.
          try:
            q = channel.queue_declare(
                queue=queue_name, durable=True, passive=True)
            log_print(
                "Messages waiting to be consumed at startup: %d" %
                q.method.message_count)
          except Exception as e:
            log_print("Failed to get startup queue depth: %s" % e)

          live_pool = _live_db_ingest_pool_active()
          if live_pool is not None and _listend_db_backpressure_mode_is_pause():
            # Prefetch=1 so pause leaves work on the broker ready queue.
            channel.basic_qos(prefetch_count=1)
          else:
            channel.basic_qos(
                prefetch_count=int(cfg.get_listend_amqp_prefetch()))

          # Log consume-start once per RabbitMQ connection; pause/resume
          # rebinds must not re-emit (that was log spam with backpressure).
          consume_start_logged = False
          # Fresh channel: skip cancel before first basic_consume (quorum Ra).
          had_consumer = False

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
                else:
                  channel.basic_qos(
                      prefetch_count=int(cfg.get_listend_amqp_prefetch()))
              except Exception:
                break
              # stop_consuming already cleared the consumer tag.
              had_consumer = False

            _bind_listend_consume(
                channel, queue_name, had_consumer=had_consumer)
            had_consumer = True
            if not consume_start_logged:
              log_print("Begining Consume from queue: " + queue_name)
              consume_start_logged = True
              _consume_attach_monotonic = time.monotonic()
            try:
              channel.start_consuming()
            except (KeyboardInterrupt, SystemExit):
              try:
                channel.stop_consuming()
              except Exception:
                pass
              raise
            except StreamLostError as e:
              if DEBUG:
                log_print("RabbitMQ stream lost while consuming: %s" % e)
              _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
              reconnect_sleep_s = _apply_amqp_reconnect_backoff()
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
                _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
                reconnect_sleep_s = _apply_amqp_reconnect_backoff()
              else:
                log_print(
                    "Error while consuming from RabbitMQ: %s"
                    % _format_amqp_consume_error(e)
                )
              break
            except Exception as e:
              if should_use_amqp_exponential_reconnect_backoff(e):
                if is_quorum_consume_setup_error(e):
                  log_print(
                      "AMQP quorum consume-setup timeout (541): %s" % e)
                elif is_amqp_peer_reset_reconnect_error(e):
                  log_print(
                      "AMQP peer reset / stream lost during consume: %s" % e)
                _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
                reconnect_sleep_s = _apply_amqp_reconnect_backoff()
              else:
                log_print(
                    "Error while consuming from RabbitMQ: %s"
                    % _format_amqp_consume_error(e)
                )
              break

            if _db_backpressure_pause:
              # stop_consuming from on_message — wait then resume.
              continue
            # Dead channel/connection or unexpected stop_consuming → reconnect.
            if _amqp_reconnect_requested:
              _amqp_reconnect_requested = False
              _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
              reconnect_sleep_s = _apply_amqp_reconnect_backoff()
            else:
              _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
            break
        except (KeyboardInterrupt, SystemExit):
          # Allow clean shutdown on explicit termination signals.
          log_print("Shutting down listend daemon on user request")
          break
        except Exception as e:
          kind = _log_amqp_outer_loop_error(e)
          _maybe_reset_amqp_reconnect_backoff_after_stable_consume()
          if kind in ("quorum_consume_setup", "peer_reset"):
            reconnect_sleep_s = _apply_amqp_reconnect_backoff()
          elif should_use_amqp_exponential_reconnect_backoff(e):
            reconnect_sleep_s = _apply_amqp_reconnect_backoff()
          else:
            reconnect_sleep_s = AMQP_RECONNECT_BACKOFF_INITIAL_SECONDS
        finally:
          clear_amqp_connection()
          _close_amqp_channel_and_connection_gracefully(channel, connection)
          _amqp_reconnect_requested = False
          _consume_attach_monotonic = None

        # Sleep before reconnect. 541 consume-setup uses exponential backoff
        # so we do not hammer Ra checkout under load.
        time.sleep(reconnect_sleep_s)
  finally:
    _idle_monitor_stop_event.set()
    _recent_host_worker_stop_event.set()
    try:
      stop_listend_archive_pool()
    except Exception:
      pass
    try:
      from hpcperfstats.dbload.lib.listend_db_ingest import stop_listend_db_ingest_pool

      stop_listend_db_ingest_pool()
    except Exception:
      pass
    clear_amqp_connection()
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
