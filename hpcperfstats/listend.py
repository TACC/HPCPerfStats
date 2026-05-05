"""RabbitMQ listener daemon. Consumes messages from the configured queue and appends payloads to per-host files under the archive directory. Single-instance via file lock.

"""
import os
import queue
import re
import signal
import sys
import time
from collections import deque
from threading import Event, Lock, Thread
from fcntl import LOCK_EX, LOCK_NB, flock

import pika
import redis
from pika.exceptions import StreamLostError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.file_locking import file_read_lock_wait, file_write_lock
from hpcperfstats.print_utils import log_print
from hpcperfstats.shutdown_utils import send_sigchld_to_parent

DEBUG = cfg.get_debug()

MESSAGE_WINDOW_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60      # seconds
RECENT_HOST_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week

_message_timestamps = deque()
_unlink_timestamps = deque()
_timestamps_lock = Lock()
_last_message_time = None
_last_idle_report_time = None
_channel_ref = None
_idle_thread_started = False
_idle_monitor_stop_event = Event()
_recent_host_worker_thread_started = False
_recent_host_worker_stop_event = Event()
_recent_host_queue = queue.Queue(maxsize=100000)
_recent_host_redis_client = None

LIVE_JOB_TTL_SECONDS = 15 * 60  # 15 minutes
LIVE_JOB_INDEX_KEY = "live_job:index"

# Per-core jiffies lines: ``cpu <core_id> user nice system idle iowait irq softirq ...``
# (tacc_stats /proc snapshot style; see HPCPerfStatsdDataSample).
_PER_CPU_JIFFIES_RE = re.compile(r"^cpu (\d+) (.*)$")
# Reasonable upper bound so ``cpu <user_jiffies> ...`` aggregate lines are not mistaken for cores.
_MAX_PER_CPU_CORE_ID = 4095

_live_job_worker_thread_started = False
_live_job_worker_stop_event = Event()
_live_job_queue = queue.Queue(maxsize=100000)
_live_job_redis_client = None
_live_job_worker_redis_none_logged = False


def _get_first_timestamp_seconds(file_path, use_lock=True):
  """Return first unix timestamp seconds found in stats file content.

  Expects a line beginning with digits in the format: "<t> <jid> <host> ...".
  """
  try:
    if use_lock:
      with file_read_lock_wait(file_path):
        with open(file_path, "r") as fd:
          for line in fd:
            if not line:
              continue
            if line[0].isdigit():
              t = line.split(maxsplit=1)[0]
              # The file may store floats (epoch with fractional seconds).
              return int(float(t))
    else:
      with open(file_path, "r") as fd:
        for line in fd:
          if not line:
            continue
          if line[0].isdigit():
            t = line.split(maxsplit=1)[0]
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
        if name.endswith(".lock"):
          continue
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
  # Called from on_message() while holding write lock for current_path.
  first_ts_sec = _get_first_timestamp_seconds(current_path, use_lock=False)
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


def _get_recent_host_redis_client():
  """Get or create the Redis client used for recent-host timestamps."""
  global _recent_host_redis_client
  if _recent_host_redis_client is not None:
    return _recent_host_redis_client
  try:
    _recent_host_redis_client = redis.from_url(
        cfg.get_redis_location(), decode_responses=True)
  except Exception:
    _recent_host_redis_client = None
  return _recent_host_redis_client


def _set_recent_host_timestamp(redis_client, host):
  """Set `recent_host:<fqdn>` to current epoch seconds."""
  if not host or "." not in host:
    return
  redis_client.setex(
      "recent_host:%s" % host,
      RECENT_HOST_TTL_SECONDS,
      str(int(time.time())),
  )


def _enqueue_recent_host_update(host):
  """Queue a best-effort Redis host timestamp update."""
  if not host or "." not in host:
    return
  try:
    _recent_host_queue.put_nowait(host)
  except queue.Full:
    if DEBUG:
      log_print("Recent-host Redis queue is full; dropping update for %s" % host)


def _recent_host_worker():
  """Background worker that writes recent-host timestamps to Redis."""
  while not _recent_host_worker_stop_event.is_set():
    try:
      host = _recent_host_queue.get(timeout=1.0)
    except queue.Empty:
      continue

    try:
      redis_client = _get_recent_host_redis_client()
      if redis_client is not None:
        _set_recent_host_timestamp(redis_client, host)
    except Exception as e:
      if DEBUG:
        log_print("Failed to update recent-host Redis key for %s: %s" % (host, e))
    finally:
      _recent_host_queue.task_done()


def _first_timestamp_seconds(message):
  """First unix seconds from the first timestamp line, else now."""
  for line in message.splitlines():
    stripped = line.strip()
    if not stripped or stripped[0] not in "0123456789":
      continue
    tok = stripped.split(maxsplit=1)[0]
    try:
      return int(float(tok))
    except ValueError:
      continue
  return int(time.time())


def _parse_first_timestamp_line_jid_host(message):
  """Parse jid and host from the first '<t> <jid> <host>' line."""
  for line in message.splitlines():
    stripped = line.strip()
    if not stripped or stripped[0] not in "0123456789":
      continue
    parts = stripped.split()
    if len(parts) >= 3:
      return parts[1], parts[2]
    # e.g. ``$`` rotation metadata ``1 <host>`` — not a stats timestamp line.
    continue
  return None, None


def _extract_named_percent(message, name):
  """Parse ``name=12.34`` or ``name: 12.34`` (first match)."""
  pat = re.compile(
      r"\b" + re.escape(name) + r"\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)",
      re.IGNORECASE,
  )
  m = pat.search(message)
  if not m:
    return None
  try:
    return float(m.group(1))
  except ValueError:
    return None


def _extract_mem_percent_from_mem_lines(message):
  """Derive memory use percent from ``mem <node> <MemTotal> <MemFree> <MemUsed> ...`` rows.

  Sums MemTotal/MemUsed across NUMA nodes when multiple ``mem`` lines exist (prod sample).
  """
  total_kb = 0.0
  used_kb = 0.0
  for line in message.splitlines():
    if not line.startswith("mem ") or line.startswith("mem_used"):
      continue
    parts = line.split()
    if len(parts) < 5:
      continue
    try:
      if not parts[1].isdigit():
        continue
      node_total = float(parts[2])
      node_used = float(parts[4])
    except (ValueError, IndexError):
      continue
    if node_total <= 0:
      continue
    total_kb += node_total
    used_kb += node_used
  if total_kb <= 0:
    return None
  return 100.0 * used_kb / total_kb


def _extract_cpu_percent_from_cpu_lines(message):
  """Derive CPU use percent from /proc-style jiffies lines (``!cpu`` schema in daemon payloads).

  Prefer many ``cpu <id> user nice system idle iowait irq softirq ...`` per-core lines
  (HPCPerfStatsdDataSample). Falls back to a single aggregate ``cpu user nice system idle ...``
  line when the second field is not a small core id.
  """
  busy_sum = 0.0
  total_sum = 0.0
  for line in message.splitlines():
    line = line.strip()
    m = _PER_CPU_JIFFIES_RE.match(line)
    if not m:
      continue
    core_id = int(m.group(1))
    if core_id > _MAX_PER_CPU_CORE_ID:
      continue
    toks = m.group(2).split()
    if len(toks) < 7:
      continue
    try:
      nums = [float(x) for x in toks]
    except ValueError:
      continue
    total_j = sum(nums)
    if total_j <= 0:
      continue
    idle_j = nums[3]
    busy_j = total_j - idle_j
    busy_sum += busy_j
    total_sum += total_j
  if total_sum > 0:
    return 100.0 * busy_sum / total_sum
  for line in message.splitlines():
    line = line.strip()
    parts = line.split()
    if len(parts) < 8 or parts[0] != "cpu":
      continue
    m = _PER_CPU_JIFFIES_RE.match(line)
    if m and int(m.group(1)) <= _MAX_PER_CPU_CORE_ID:
      continue
    try:
      nums = [float(parts[i]) for i in range(1, len(parts))]
    except ValueError:
      continue
    if len(nums) < 5:
      continue
    total_j = sum(nums)
    if total_j <= 0:
      continue
    idle_j = nums[3]
    busy_j = total_j - idle_j
    return 100.0 * busy_j / total_j
  return None


def parse_live_job_metrics(message, fallback_host=None):
  """Extract jid/host/cpu_util/mem_util from a daemon stats payload, or None."""
  jid, host = _parse_first_timestamp_line_jid_host(message)
  if not jid or jid == "-":
    return None
  if not host and fallback_host:
    host = fallback_host
  if not host:
    return None
  cpu_util = _extract_named_percent(message, "cpu_util")
  mem_util = _extract_named_percent(message, "mem_util")
  if mem_util is None:
    mem_util = _extract_mem_percent_from_mem_lines(message)
  if cpu_util is None:
    cpu_util = _extract_cpu_percent_from_cpu_lines(message)
  if cpu_util is None or mem_util is None:
    return None
  updated_ts = _first_timestamp_seconds(message)
  return {
      "jid": str(jid),
      "host": str(host),
      "cpu_util": cpu_util,
      "mem_util": mem_util,
      "updated_ts": updated_ts,
  }


def _get_live_job_redis_client():
  """Get or create Redis client for live-job hashes (decode_responses=True)."""
  global _live_job_redis_client
  if _live_job_redis_client is not None:
    return _live_job_redis_client
  try:
    _live_job_redis_client = redis.from_url(
        cfg.get_redis_location(), decode_responses=True)
  except Exception:
    _live_job_redis_client = None
  return _live_job_redis_client


def _enqueue_live_job_update(payload):
  if not payload:
    return
  try:
    _live_job_queue.put_nowait(payload)
  except queue.Full:
    if DEBUG:
      log_print("Live-job Redis queue is full; dropping update")


def _set_live_job_metrics(redis_client, payload):
  """Write one live snapshot hash and index membership."""
  jid = payload["jid"]
  host = payload["host"]
  rkey = "live_job:%s:%s" % (jid, host)
  # region agent log
  _wall_ts = int(time.time())
  _sample_ts = int(payload.get("updated_ts", 0))
  try:
    import json as _json
    with open(
        "/home/beniyam12/HPCPerfStats/.cursor/debug-6046cd.log", "a"
    ) as _df:
      _df.write(_json.dumps({
          "sessionId": "6046cd",
          "hypothesisId": "H7",
          "location": "listend.py:_set_live_job_metrics",
          "message": "sample_ts vs wall_ts written to Redis",
          "data": {
              "jid": jid,
              "host": host,
              "payload_sample_ts": _sample_ts,
              "redis_updated_ts": _wall_ts,
              "delta_sample_behind_wall_sec": _wall_ts - _sample_ts,
          },
          "timestamp": int(time.time() * 1000),
      }) + "\n")
  except Exception:
    pass
  # endregion agent log
  mapping = {
      "jid": jid,
      "host": host,
      "cpu_util": str(payload["cpu_util"]),
      "mem_util": str(payload["mem_util"]),
      "updated_ts": str(_wall_ts),
  }
  pipe = redis_client.pipeline()
  pipe.hset(rkey, mapping=mapping)
  pipe.expire(rkey, LIVE_JOB_TTL_SECONDS)
  pipe.sadd(LIVE_JOB_INDEX_KEY, rkey)
  pipe.execute()


def _live_job_worker():
  """Background writer for live job CPU/mem snapshots."""
  global _live_job_worker_redis_none_logged
  while not _live_job_worker_stop_event.is_set():
    try:
      payload = _live_job_queue.get(timeout=1.0)
    except queue.Empty:
      continue
    try:
      client = _get_live_job_redis_client()
      if client is None:
        if not _live_job_worker_redis_none_logged:
          _live_job_worker_redis_none_logged = True
          try:
            import json as _json
            with open(
                "/home/beniyam12/HPCPerfStats/.cursor/debug-6046cd.log",
                "a",
                encoding="utf-8",
            ) as _df:
              _df.write(
                  _json.dumps({
                      "sessionId": "6046cd",
                      "hypothesisId": "H7b",
                      "location": "listend.py:_live_job_worker",
                      "message": "redis client is None; live_job writes skipped",
                      "timestamp": int(time.time() * 1000),
                  })
                  + "\n"
              )
          except Exception:
            pass
      else:
        _set_live_job_metrics(client, payload)
    except Exception as e:
      if DEBUG:
        log_print("Live-job Redis update failed: %s" % e)
    finally:
      _live_job_queue.task_done()


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
      with file_write_lock(current_path):
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
    else:
      with file_write_lock(current_path):
        with open(current_path, "a") as fd:
          fd.write(message)
    _enqueue_recent_host_update(host)

    live_payload = parse_live_job_metrics(message, fallback_host=host)
    if live_payload:
      _enqueue_live_job_update(live_payload)

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
  global _recent_host_worker_thread_started
  global _live_job_worker_thread_started
  # Use a mutable container so the SIGTERM handler can update state without
  # relying on `nonlocal` (which is only valid for enclosing function scopes).
  sigterm_received = {"value": False}
  connection = None

  def _sigterm_handler(signum, frame):
    sigterm_received["value"] = True
    _idle_monitor_stop_event.set()
    _live_job_worker_stop_event.set()
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
      if not _live_job_worker_thread_started:
        live_job_thread = Thread(target=_live_job_worker, daemon=True)
        live_job_thread.start()
        _live_job_worker_thread_started = True

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
    _recent_host_worker_stop_event.set()
    _live_job_worker_stop_event.set()
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
