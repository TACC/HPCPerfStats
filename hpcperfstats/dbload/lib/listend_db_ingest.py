"""Host-affine sliding pool: listend archive payloads → Timescale dual-write.

Ack remains on archive write only. Queue-full / byte-budget overflow drops the
DB path (file stays durable for sync_timedb). Incomplete samples are never
partially inserted — timestamp-second presence would poison duplicate-scan repair.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
import zlib
from typing import Any, Dict, Optional, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

# Module constants (not INI) — connection / idle hygiene.
_QUEUE_GET_TIMEOUT_S = 30.0
_MIN_QUEUED_PAYLOAD_BYTES = 256
_SHUTDOWN_JOIN_TIMEOUT_S = 15.0

# Shared counter names (multiprocessing.Value keys via dict of Values).
_COUNTER_NAMES = (
    "queue_drops",
    "schema_miss",
    "db_ok",
    "db_err",
    "conn_recycle",
    "batch_flush",
)


def compute_listend_db_queue_budgets(
    *,
    pool_processes: int | None = None,
    queue_max_gb: float | None = None,
    min_payload_bytes: int = _MIN_QUEUED_PAYLOAD_BYTES,
) -> dict:
  """Derive per-worker byte budget and Queue maxsize from total GiB budget."""
  n = max(1, int(pool_processes if pool_processes is not None else cfg.get_listend_db_ingest_pool_processes()))
  max_gb = float(
      queue_max_gb if queue_max_gb is not None else cfg.get_listend_db_ingest_queue_max_gb()
  )
  budget_bytes = int(max(0.001, max_gb) * (1024 ** 3))
  per_worker_budget = max(min_payload_bytes, budget_bytes // n)
  floor = max(1, int(min_payload_bytes))
  maxsize = max(1, per_worker_budget // floor)
  return {
      "pool_processes": n,
      "budget_bytes": budget_bytes,
      "per_worker_budget_bytes": per_worker_budget,
      "queue_maxsize": maxsize,
  }


def host_affine_worker_index(host: str, pool_processes: int) -> int:
  """Stable hash(host) % N (not salted ``hash()``)."""
  n = max(1, int(pool_processes))
  raw = (host or "").encode("utf-8", errors="replace")
  return int(zlib.adler32(raw) & 0xFFFFFFFF) % n


def parse_host_from_monitor_payload(message: str) -> str:
  """Return FQDN host token (same contract as listend archive write)."""
  if not message:
    raise ValueError("Empty message body")
  if message[0] == "$":
    parts = message.split("\n")
    if len(parts) < 2:
      raise ValueError("Malformed '$' message: missing host line")
    host_parts = parts[1].split()
    if len(host_parts) < 2:
      raise ValueError("Malformed '$' message: host line missing field")
    return host_parts[1]
  msg_parts = message.split()
  if len(msg_parts) < 3:
    raise ValueError("Malformed message: not enough fields to get host")
  return msg_parts[2]


def payload_has_schema_bang(message: str) -> bool:
  for line in message.splitlines():
    s = line.lstrip()
    if s.startswith("!"):
      return True
  return False


def sample_measurement_types(message: str) -> list[str]:
  """Alpha-leading typed measurement names after a digit timestamp header."""
  types: list[str] = []
  saw_ts = False
  for line in message.splitlines():
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      saw_ts = True
      continue
    if saw_ts and s[0].isalpha():
      types.append(s.split(maxsplit=1)[0])
  return types


def schema_covers_measurement_types(schema: dict, types: list[str]) -> bool:
  for typ in types:
    if typ in ("proc", "host_proc"):
      continue
    if typ not in schema:
      return False
  return True


def parse_schema_from_bang_lines(message: str) -> Tuple[dict, dict]:
  """Return ``(schema, schema_fast)`` from ``!`` lines (full replace shape)."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import _fast_schema_keys

  schema: dict = {}
  schema_fast: dict = {}
  for line in message.splitlines():
    s = line.lstrip()
    if not s or s[0] != "!":
      continue
    try:
      label, events = s.split(maxsplit=1)
    except ValueError:
      continue
    typ = label[1:]
    event_list = events.split()
    schema[typ] = event_list
    schema_fast[typ] = _fast_schema_keys(event_list)
  return schema, schema_fast


def seed_schema_from_current_file(host: str) -> Tuple[dict, dict]:
  """Cold-start: read ``!`` lines from host ``current`` under read lock."""
  from hpcperfstats.dbload.lib.file_locking import file_read_lock_wait

  archive_dir = cfg.get_archive_dir_path()
  if not archive_dir or not host:
    return {}, {}
  current_path = os.path.join(archive_dir, host, "current")
  if not os.path.isfile(current_path):
    return {}, {}
  try:
    with file_read_lock_wait(current_path):
      with open(current_path, "r", encoding="utf-8", errors="replace") as fd:
        # Schema dumps are near the top; bound the seed read.
        chunk = fd.read(1024 * 1024)
  except OSError:
    return {}, {}
  return parse_schema_from_bang_lines(chunk)


def _payload_byte_size(message: str) -> int:
  return len(message.encode("utf-8", errors="replace"))


def _inc_counter(counters: dict, name: str, amount: int = 1) -> None:
  val = counters.get(name)
  if val is None:
    return
  with val.get_lock():
    val.value = int(val.value) + int(amount)


def _release_listend_db_worker_memory() -> None:
  """Drop heap after flush / idle recycle (not every sample)."""
  from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
      release_spawn_pool_worker_memory,
  )

  release_spawn_pool_worker_memory()


def _conn_max_age_s() -> float:
  try:
    return max(30.0, float(cfg.get_db_conn_max_age()))
  except Exception:
    return 90.0


def _proc_field_or_none(row, key):
  try:
    val = getattr(row, key)
  except AttributeError:
    return None
  if val is None:
    return None
  try:
    if val != val:  # NaN
      return None
  except Exception:
    pass
  return val


def _proc_data_row_kwargs(row):
  from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

  kwargs = {
      "jid": row.jid,
      "host": row.host,
      "proc": row.proc,
      "device": _proc_field_or_none(row, "device"),
  }
  for key in HOST_PROC_KEYS:
    kwargs[key] = _proc_field_or_none(row, key)
  return kwargs


def _flush_orm_batch(host_objs: list, proc_objs: list) -> None:
  """Write pending ORM instances; clear caller lists on success."""
  from django.db import close_old_connections, connections
  from django.db.utils import OperationalError

  from hpcperfstats.site.lib.machine.models import host_data, proc_data
  from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

  close_old_connections()
  batch_size = cfg.get_sync_bulk_create_batch_size()
  update_fields = ("device",) + HOST_PROC_KEYS

  def _write_once():
    for i in range(0, len(proc_objs), batch_size):
      chunk = proc_objs[i : i + batch_size]
      if not chunk:
        continue
      proc_data.objects.bulk_create(
          chunk,
          update_conflicts=True,
          unique_fields=["jid", "host", "proc"],
          update_fields=update_fields,
      )
    for i in range(0, len(host_objs), batch_size):
      chunk = host_objs[i : i + batch_size]
      if not chunk:
        continue
      host_data.objects.bulk_create(chunk, ignore_conflicts=True)

  try:
    _write_once()
  except OperationalError:
    try:
      connections.close_all()
    except Exception:
      pass
    close_old_connections()
    _write_once()


def _process_sample_to_orm(
    message: str,
    *,
    host: str,
    schema: dict,
    schema_fast: dict,
    carry,
) -> Tuple[list, list]:
  """Parse one complete sample → host_data / proc_data instances. Empty on skip."""
  from hpcperfstats.dbload.lib.io_helpers import host_data_instance_from_stats_row
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      DeltaCarryState,
      IncrementalStatsParser,
      build_stats_dataframes,
      compute_deltas_and_arc_chunk,
  )

  types = sample_measurement_types(message)
  if not types:
    return [], []
  if not schema_covers_measurement_types(schema, types):
    return [], []

  parser = IncrementalStatsParser(0)
  parser.schema = dict(schema)
  parser.schema_fast = dict(schema_fast)
  try:
    parser.feed_lines(message.splitlines())
    stats_list, proc_list = parser.finish()
  except Exception:
    return [], []

  # All-or-nothing: non-proc typed lines must produce host stats rows.
  non_proc = [t for t in types if t not in ("proc", "host_proc")]
  if non_proc and not stats_list:
    return [], []
  if not stats_list and not proc_list:
    return [], []

  stats_df, proc_df = build_stats_dataframes(stats_list, proc_list)
  del stats_list, proc_list
  host_objs: list = []
  proc_objs: list = []
  try:
    if not stats_df.empty:
      if carry is None:
        carry = DeltaCarryState()
      finalized = compute_deltas_and_arc_chunk(stats_df, carry=carry)
      del stats_df
      if not finalized.empty:
        for row in finalized.itertuples(index=False):
          host_objs.append(host_data_instance_from_stats_row(row))
      del finalized
    if not proc_df.empty:
      for row in proc_df.itertuples(index=False):
        from hpcperfstats.site.lib.machine.models import proc_data

        proc_objs.append(proc_data(**_proc_data_row_kwargs(row)))
  finally:
    try:
      del stats_df
    except Exception:
      pass
    try:
      del proc_df
    except Exception:
      pass
  return host_objs, proc_objs


def _worker_main(
    worker_idx: int,
    work_queue: Any,
    stop_event: Any,
    byte_count: Any,
    byte_lock: Any,
    counters: dict,
    batch_samples: int,
    per_worker_budget: int,
) -> None:
  del per_worker_budget  # tracked on put; kept for future diagnostics
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
  from hpcperfstats.dbload.lib.process_title import set_daemon_process_title
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  set_daemon_process_title(name="listend.py", role="worker", pool_kind="listend-db-pool")
  ensure_django()

  from django.db import close_old_connections, connections

  schema_by_host: Dict[str, dict] = {}
  schema_fast_by_host: Dict[str, dict] = {}
  carry_by_host: Dict[str, DeltaCarryState] = {}
  pending_host: list = []
  pending_proc: list = []
  sample_count = 0
  conn_opened_at = time.monotonic()
  seeded_hosts: set = set()

  def _recycle_conn(*, reason: str = "idle") -> None:
    del reason
    try:
      close_old_connections()
    except Exception:
      pass
    try:
      connections.close_all()
    except Exception:
      pass
    nonlocal conn_opened_at
    conn_opened_at = time.monotonic()
    _inc_counter(counters, "conn_recycle")

  def _flush(*, force_memory_release: bool = True) -> None:
    nonlocal sample_count, pending_host, pending_proc
    if not pending_host and not pending_proc:
      sample_count = 0
      return
    try:
      _flush_orm_batch(pending_host, pending_proc)
      _inc_counter(counters, "db_ok")
      _inc_counter(counters, "batch_flush")
    except Exception as exc:
      _inc_counter(counters, "db_err")
      log_print(
          "ERROR: listend db ingest flush failed worker=%d: %s"
          % (worker_idx, exc),
          flush=True,
      )
    pending_host = []
    pending_proc = []
    sample_count = 0
    if force_memory_release:
      _release_listend_db_worker_memory()
    if (time.monotonic() - conn_opened_at) >= _conn_max_age_s():
      _recycle_conn(reason="age")

  def _ensure_schema_seed(host: str) -> None:
    if host in seeded_hosts:
      return
    seeded_hosts.add(host)
    if host in schema_by_host and schema_by_host[host]:
      return
    schema, schema_fast = seed_schema_from_current_file(host)
    if schema:
      schema_by_host[host] = schema
      schema_fast_by_host[host] = schema_fast

  while not stop_event.is_set():
    try:
      item = work_queue.get(timeout=_QUEUE_GET_TIMEOUT_S)
    except queue.Empty:
      _flush(force_memory_release=True)
      _recycle_conn(reason="idle")
      continue
    if item is None:
      break
    host, message, size = item
    with byte_lock:
      byte_count.value = max(0, int(byte_count.value) - int(size))

    try:
      if message and message[0] == "$":
        schema, schema_fast = parse_schema_from_bang_lines(message)
        if schema:
          schema_by_host[host] = schema
          schema_fast_by_host[host] = schema_fast
        seeded_hosts.add(host)
        continue

      _ensure_schema_seed(host)
      has_bang = payload_has_schema_bang(message)
      if has_bang:
        schema, schema_fast = parse_schema_from_bang_lines(message)
        if schema:
          schema_by_host[host] = schema
          schema_fast_by_host[host] = schema_fast
      schema = schema_by_host.get(host) or {}
      if not schema and not has_bang:
        _inc_counter(counters, "schema_miss")
        continue
      if has_bang and not schema:
        _inc_counter(counters, "schema_miss")
        continue

      schema_fast = schema_fast_by_host.get(host) or {}
      carry = carry_by_host.setdefault(host, DeltaCarryState())
      host_objs, proc_objs = _process_sample_to_orm(
          message,
          host=host,
          schema=schema,
          schema_fast=schema_fast,
          carry=carry,
      )
      message = None  # drop payload ref
      if not host_objs and not proc_objs:
        # Covered schema but unusable / incomplete → treat as schema_miss class skip.
        _inc_counter(counters, "schema_miss")
        continue
      pending_host.extend(host_objs)
      pending_proc.extend(proc_objs)
      del host_objs, proc_objs
      sample_count += 1
      if sample_count >= batch_samples:
        _flush(force_memory_release=True)
    except Exception as exc:
      _inc_counter(counters, "db_err")
      log_print(
          "ERROR: listend db ingest sample failed worker=%d host=%s: %s"
          % (worker_idx, host, exc),
          flush=True,
      )
      pending_host = []
      pending_proc = []
      sample_count = 0
      _release_listend_db_worker_memory()

  _flush(force_memory_release=True)
  try:
    connections.close_all()
  except Exception:
    pass


class ListendDbIngestPool:
  """Host-affine continuous worker pool for listend live DB ingest."""

  def __init__(
      self,
      *,
      pool_processes: int | None = None,
      queue_max_gb: float | None = None,
      batch_samples: int | None = None,
      enabled: bool | None = None,
  ):
    budgets = compute_listend_db_queue_budgets(
        pool_processes=pool_processes,
        queue_max_gb=queue_max_gb,
    )
    self.enabled = (
        bool(cfg.get_listend_db_ingest_enabled())
        if enabled is None
        else bool(enabled)
    )
    self.pool_processes = int(budgets["pool_processes"])
    self.per_worker_budget_bytes = int(budgets["per_worker_budget_bytes"])
    self.queue_maxsize = int(budgets["queue_maxsize"])
    self.batch_samples = max(
        1,
        int(
            batch_samples
            if batch_samples is not None
            else cfg.get_listend_db_ingest_batch_samples()
        ),
    )
    self._ctx = mp.get_context("spawn")
    self._stop = self._ctx.Event()
    self._queues: list = []
    self._byte_counts: list = []
    self._byte_locks: list = []
    self._workers: list = []
    self._counters = {
        name: self._ctx.Value("Q", 0) for name in _COUNTER_NAMES
    }
    self._started = False
    self._window_baseline = self.snapshot_counters()

  def start(self) -> None:
    if not self.enabled or self._started:
      return
    for i in range(self.pool_processes):
      q = self._ctx.Queue(maxsize=self.queue_maxsize)
      byte_count = self._ctx.Value("Q", 0)
      byte_lock = self._ctx.Lock()
      proc = self._ctx.Process(
          target=_worker_main,
          args=(
              i,
              q,
              self._stop,
              byte_count,
              byte_lock,
              self._counters,
              self.batch_samples,
              self.per_worker_budget_bytes,
          ),
          name="listend-db-pool-%d" % i,
          daemon=True,
      )
      self._queues.append(q)
      self._byte_counts.append(byte_count)
      self._byte_locks.append(byte_lock)
      self._workers.append(proc)
      proc.start()
    self._started = True
    self._window_baseline = self.snapshot_counters()
    log_print(
        "listend db ingest pool started workers=%d queue_maxsize=%d "
        "per_worker_budget_bytes=%d batch_samples=%d"
        % (
            self.pool_processes,
            self.queue_maxsize,
            self.per_worker_budget_bytes,
            self.batch_samples,
        ),
        flush=True,
    )

  def stop(self, *, join_timeout: float = _SHUTDOWN_JOIN_TIMEOUT_S) -> None:
    if not self._started:
      return
    self._stop.set()
    for q in self._queues:
      try:
        q.put_nowait(None)
      except Exception:
        pass
    deadline = time.monotonic() + max(0.1, float(join_timeout))
    for proc in self._workers:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        break
      try:
        proc.join(timeout=remaining)
      except Exception:
        pass
    for proc in self._workers:
      if proc.is_alive():
        try:
          proc.terminate()
        except Exception:
          pass
    self._started = False

  def submit(self, host: str, message: str) -> bool:
    """Nonblocking enqueue. Return False on drop / disabled / not started."""
    if not self.enabled or not self._started or self._stop.is_set():
      return False
    if not host or message is None:
      return False
    idx = host_affine_worker_index(host, self.pool_processes)
    size = _payload_byte_size(message)
    q = self._queues[idx]
    byte_count = self._byte_counts[idx]
    byte_lock = self._byte_locks[idx]
    with byte_lock:
      if int(byte_count.value) + size > self.per_worker_budget_bytes:
        _inc_counter(self._counters, "queue_drops")
        return False
      try:
        q.put_nowait((host, message, size))
      except Exception:
        _inc_counter(self._counters, "queue_drops")
        return False
      byte_count.value = int(byte_count.value) + size
    return True

  def snapshot_counters(self) -> dict:
    out = {name: int(self._counters[name].value) for name in _COUNTER_NAMES}
    depth = 0
    for q in self._queues:
      try:
        depth += int(q.qsize())
      except Exception:
        pass
    out["db_queue_depth"] = depth
    return out

  def window_counters_and_reset(self) -> dict:
    """Return counters since last window baseline, then reset the baseline."""
    now = self.snapshot_counters()
    base = self._window_baseline or {}
    delta = {}
    for key, val in now.items():
      if key == "db_queue_depth":
        delta[key] = val
      else:
        delta[key] = max(0, int(val) - int(base.get(key, 0)))
    self._window_baseline = now
    return delta

  def format_idle_monitor_suffix(self) -> str:
    d = self.window_counters_and_reset()
    return (
        "db_ingest queue_drops=%d schema_miss=%d db_ok=%d db_err=%d "
        "conn_recycle=%d db_queue_depth=%d batch_flush=%d"
        % (
            d.get("queue_drops", 0),
            d.get("schema_miss", 0),
            d.get("db_ok", 0),
            d.get("db_err", 0),
            d.get("conn_recycle", 0),
            d.get("db_queue_depth", 0),
            d.get("batch_flush", 0),
        )
    )


# Process-global pool for listend main (set by start_listend_db_ingest_pool).
_GLOBAL_POOL: Optional[ListendDbIngestPool] = None


def get_listend_db_ingest_pool() -> Optional[ListendDbIngestPool]:
  return _GLOBAL_POOL


def start_listend_db_ingest_pool(**kwargs) -> Optional[ListendDbIngestPool]:
  global _GLOBAL_POOL
  if _GLOBAL_POOL is not None:
    return _GLOBAL_POOL
  pool = ListendDbIngestPool(**kwargs)
  if pool.enabled:
    pool.start()
  _GLOBAL_POOL = pool
  return pool


def stop_listend_db_ingest_pool() -> None:
  global _GLOBAL_POOL
  pool = _GLOBAL_POOL
  _GLOBAL_POOL = None
  if pool is not None:
    try:
      pool.stop()
    except Exception as exc:
      log_print("ERROR: listend db ingest pool stop failed: %s" % exc, flush=True)


def submit_listend_db_ingest(host: str, message: str) -> bool:
  """Best-effort enqueue; never raises into the ack path."""
  pool = _GLOBAL_POOL
  if pool is None:
    return False
  try:
    return bool(pool.submit(host, message))
  except Exception as exc:
    try:
      log_print(
          "ERROR: listend db ingest submit failed host=%s: %s" % (host, exc),
          flush=True,
      )
    except Exception:
      pass
    return False
