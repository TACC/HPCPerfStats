"""
Host-affine sliding pool: listend archive payloads → Timescale dual-write.

Ack remains on archive write only when consuming. When live DB queues hit the
high watermark, listend stops RabbitMQ consume (no archive) until usage falls
below the low watermark. Incomplete samples are never partially inserted —
timestamp-second presence would poison duplicate-scan repair.

Attributes:
  _COUNTER_NAMES: Attribute.
  _GLOBAL_POOL: Attribute.
  _MIN_QUEUED_PAYLOAD_BYTES: Attribute.
  _PAUSE_WATERMARK: Attribute.
  _QUEUE_GET_TIMEOUT_S: Attribute.
  _RESUME_WATERMARK: Attribute.
  _SHUTDOWN_JOIN_TIMEOUT_S: Attribute.
  _listend_flush_error_is_poison: Classify flush errors that drop the batch.
  _is_listend_statement_timeout: Detect PostgreSQL statement timeout cancels.
  _apply_listend_db_ingest_statement_timeout: Apply listend INI statement_timeout.
  _write_proc_chunk_with_timeout_bisect: Peak-merge write with timeout bisect.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
import zlib
from typing import Any, Dict, Optional, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

# Module constants (not INI) — connection / idle hygiene.
_QUEUE_GET_TIMEOUT_S = 30.0
_MIN_QUEUED_PAYLOAD_BYTES = 256
_SHUTDOWN_JOIN_TIMEOUT_S = 15.0
# Consume pause hysteresis (fractions of total queue byte budget).
_PAUSE_WATERMARK = 0.95
_RESUME_WATERMARK = 0.50

# Shared counter names (multiprocessing.Value keys via dict of Values).
_COUNTER_NAMES = (
    "queue_drops",
    "pause_enters",
    "schema_miss",
    "db_ok",
    "db_err",
    "conn_recycle",
    "batch_flush",
    "shm_fallback",
    "shm_reclaim",
)

# POSIX SharedMemory name prefix for listend live-DB enqueue.
_LISTEND_SHM_PREFIX = "hps-ld-"


def _listend_flush_error_is_poison(exc: BaseException) -> bool:
  """
  True when a flush error is classified poison and the batch must be dropped.

  Transient Postgres/network errors keep ``pending_host`` / ``pending_proc``
  so the next flush can retry. Schema/data/integrity errors are poison.

  Args:
    exc (BaseException): Exception raised from ``_flush_orm_batch``.

  Returns:
    bool: True when the pending batch should be discarded.

  Examples:
    >>> _listend_flush_error_is_poison(ValueError("x"))
    False
    >>> _listend_flush_error_is_poison(type("DataError", (Exception,), {})("x"))
    True
  """
  name = type(exc).__name__
  return name in ("DataError", "ProgrammingError", "IntegrityError")


def _is_listend_statement_timeout(exc: BaseException) -> bool:
  """
  True when ``exc`` looks like a PostgreSQL statement timeout cancel.

  Args:
    exc (BaseException): Exception from a flush/peak-merge DB call.

  Returns:
    bool: True when the message indicates statement timeout.

  Examples:
    >>> _is_listend_statement_timeout(
    ...     Exception("canceling statement due to statement timeout")
    ... )
    True
    >>> _is_listend_statement_timeout(Exception("connection reset"))
    False
  """
  text = str(exc).lower()
  return "statement timeout" in text or "canceling statement" in text


def _apply_listend_db_ingest_statement_timeout() -> None:
  """
  Set this worker's Postgres ``statement_timeout`` from listend INI.

  Uses ``listend_db_ingest_statement_timeout_ms`` (default 10 min). ``0``
  leaves the portal ``db_statement_timeout_ms`` default unchanged.

  Returns:
    None

  Examples:
    >>> callable(_apply_listend_db_ingest_statement_timeout)
    True
  """
  try:
    ms = int(cfg.get_listend_db_ingest_statement_timeout_ms())
  except Exception:
    ms = 600000
  if ms <= 0:
    return
  try:
    from django.db import connection

    with connection.cursor() as cursor:
      cursor.execute("SET statement_timeout = %d" % ms)
  except Exception:
    return


def _peak_merge_proc_chunk_with_existing(chunk: list) -> list:
  """
  Raise peak fields on ``chunk`` from matching ``proc_data`` DB rows.

  Delegates to shared ``peak_merge_proc_objs_with_existing`` (jid+host
  ``proc__in`` slices).

  Args:
    chunk (list): ``proc_data`` instances for one bulk_create.

  Returns:
    list: Same chunk list (objs mutated when DB peers exist).

  Examples:
    >>> _peak_merge_proc_chunk_with_existing([])
    []
  """
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      peak_merge_proc_objs_with_existing,
  )

  return peak_merge_proc_objs_with_existing(chunk)


def _write_proc_chunk_with_timeout_bisect(
    chunk: list,
    *,
    update_fields: tuple,
) -> None:
  """
  Peak-merge and ``bulk_create`` one proc chunk; bisect on statement timeout.

  Args:
    chunk (list): Deduped ``proc_data`` instances for one write.
    update_fields (tuple): Fields passed to ``bulk_create`` update_conflicts.

  Returns:
    None

  Raises:
    OperationalError: Re-raised when timeout persists at chunk size 1 or the
      error is not a statement timeout.
    Exception: Propagated from connection recycle / peak-merge helpers during
      bisect recovery.

  Examples:
    >>> _write_proc_chunk_with_timeout_bisect([], update_fields=())  # doctest: +SKIP
  """
  from django.db import close_old_connections, connections
  from django.db.utils import OperationalError

  from hpcperfstats.site.lib.machine.models import proc_data

  if not chunk:
    return
  try:
    merged = _peak_merge_proc_chunk_with_existing(chunk)
    proc_data.objects.bulk_create(
        merged,
        update_conflicts=True,
        unique_fields=["jid", "host", "proc"],
        update_fields=update_fields,
    )
  except OperationalError as exc:
    if _is_listend_statement_timeout(exc) and len(chunk) > 1:
      try:
        connections.close_all()
      except Exception:
        pass
      close_old_connections()
      _apply_listend_db_ingest_statement_timeout()
      mid = len(chunk) // 2
      _write_proc_chunk_with_timeout_bisect(
          chunk[:mid], update_fields=update_fields
      )
      _write_proc_chunk_with_timeout_bisect(
          chunk[mid:], update_fields=update_fields
      )
      return
    raise


def _flush_orm_batch(host_objs: list, proc_objs: list) -> None:
  """
  Write pending ORM instances; clear caller lists on success.
  
  Args:
    host_objs (list): Sequence for host objs.
    proc_objs (list): Sequence for proc objs.
  
  Returns:
    None
  
  Examples:
    >>> _flush_orm_batch([], [])  # doctest: +SKIP
  """
  from django.db import close_old_connections, connections
  from django.db.utils import OperationalError

  from hpcperfstats.site.lib.machine.models import host_data
  from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

  # Do not close_old_connections() at every flush start — that fights
  # persistent connections and inflates idle conn_recycle.
  batch_size = cfg.get_sync_bulk_create_batch_size()
  update_fields = ("device",) + HOST_PROC_KEYS
  proc_objs = _dedupe_proc_objs_keep_last(proc_objs)

  def _write_once() -> None:
    """
    Internal helper to write the once.
    
    Returns:
      None
    
    Examples:
      >>> _write_once()  # doctest: +SKIP
    """
    for i in range(0, len(proc_objs), batch_size):
      chunk = proc_objs[i : i + batch_size]
      if not chunk:
        continue
      _write_proc_chunk_with_timeout_bisect(
          chunk, update_fields=update_fields
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
    _apply_listend_db_ingest_statement_timeout()
    _write_once()


def compute_listend_db_queue_budgets(
  *,
  pool_processes: int | None = None,
  queue_max_gb: float | None = None,
  min_payload_bytes: int = _MIN_QUEUED_PAYLOAD_BYTES,
) -> dict:
  """
  Derive per-worker byte budget and Queue maxsize from total GiB budget.
  
  Args:
    pool_processes (int | None): One of ``int``, ``None``.
    queue_max_gb (float | None): One of ``float``, ``None``.
    min_payload_bytes (int): Integer value for min payload bytes.
  
  Returns:
    dict: dict produced by this call.
  
  Examples:
    >>> compute_listend_db_queue_budgets(None, None, 0)  # doctest: +SKIP
  """
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
  """
  Stable hash(host) % N (not salted ``hash()``).
  
  Args:
    host (str): String for host.
    pool_processes (int): Integer value for pool processes.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> host_affine_worker_index("x", 0)  # doctest: +SKIP
  """
  n = max(1, int(pool_processes))
  raw = (host or "").encode("utf-8", errors="replace")
  return int(zlib.adler32(raw) & 0xFFFFFFFF) % n


def parse_host_from_monitor_payload(message: str) -> str:
  """
  Return FQDN host token (same contract as listend archive write).
  
  Args:
    message (str): String for message.
  
  Returns:
    str: str produced by this call.
  
  Raises:
    ValueError: Raised when ``parse_host_from_monitor_payload`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> parse_host_from_monitor_payload("x")  # doctest: +SKIP
  """
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
  """
  Payload has schema bang.
  
  Args:
    message (str): String for message.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> payload_has_schema_bang("x")  # doctest: +SKIP
  """
  for line in message.splitlines():
    s = line.lstrip()
    if s.startswith("!"):
      return True
  return False


def sample_measurement_types(message: str) -> list[str]:
  """
  Alpha-leading typed measurement names after a digit timestamp header.
  
  Args:
    message (str): String for message.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> sample_measurement_types("x")  # doctest: +SKIP
  """
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
  """
  Schema covers measurement types.
  
  Args:
    schema (dict): Mapping for schema.
    types (list[str]): Sequence for types.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> schema_covers_measurement_types({}, [])  # doctest: +SKIP
  """
  for typ in types:
    if typ in ("proc", "host_proc"):
      continue
    if typ not in schema:
      return False
  return True


def parse_schema_from_bang_lines(message: str) -> Tuple[dict, dict]:
  """
  Return ``(schema, schema_fast)`` from ``!`` lines (full replace shape).
  
  Args:
    message (str): String for message.
  
  Returns:
    Tuple[dict, dict]: Tuple[dict, dict] produced by this call.
  
  Examples:
    >>> parse_schema_from_bang_lines("x")  # doctest: +SKIP
  """
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
  """
  Cold-start: read ``!`` lines from host ``current`` under read lock.
  
  Args:
    host (str): String for host.
  
  Returns:
    Tuple[dict, dict]: Tuple[dict, dict] produced by this call.
  
  Examples:
    >>> seed_schema_from_current_file("x")  # doctest: +SKIP
  """
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
  """
  Internal helper to handle payload byte size.
  
  Args:
    message (str): String for message.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> _payload_byte_size("x")  # doctest: +SKIP
  """
  return len(message.encode("utf-8", errors="replace"))


def _unlink_shm_by_name(name: str) -> bool:
  """
  Best-effort unlink of a named POSIX SharedMemory segment.

  Args:
    name (str): SharedMemory name (e.g. ``hps-ld-123-0``).

  Returns:
    bool: True when unlink succeeded.

  Examples:
    >>> _unlink_shm_by_name("hps-ld-missing")
    False
  """
  if not name:
    return False
  try:
    from multiprocessing.shared_memory import SharedMemory

    shm = SharedMemory(name=name)
    try:
      shm.close()
    finally:
      try:
        shm.unlink()
      except FileNotFoundError:
        return False
    return True
  except FileNotFoundError:
    return False
  except Exception:
    try:
      path = os.path.join("/dev/shm", name.lstrip("/"))
      os.unlink(path)
      return True
    except Exception:
      return False


def _reclaim_orphan_listend_shm() -> int:
  """
  Unlink leftover ``hps-ld-*`` segments under ``/dev/shm``.

  Called on pool ``start()`` so a prior crash/OOM cannot starve tmpfs.

  Returns:
    int: Number of names successfully unlinked.

  Examples:
    >>> _reclaim_orphan_listend_shm() >= 0
    True
  """
  shm_dir = "/dev/shm"
  if not os.path.isdir(shm_dir):
    return 0
  reclaimed = 0
  try:
    names = os.listdir(shm_dir)
  except OSError:
    return 0
  for name in names:
    if not name.startswith(_LISTEND_SHM_PREFIX):
      continue
    if _unlink_shm_by_name(name):
      reclaimed += 1
  return reclaimed


def _read_archive_payload_range(
  archive_path: str,
  offset: int,
  length: int,
) -> str:
  """
  Read ``length`` bytes at ``offset`` from a durable archive file.

  Args:
    archive_path (str): Absolute path to the host ``current`` (or sibling).
    offset (int): Byte offset of the payload.
    length (int): UTF-8 byte length to read.

  Returns:
    str: Decoded UTF-8 payload (replace errors).

  Raises:
    OSError: When the file cannot be opened or read.
    ValueError: When ``length`` is negative.

  Examples:
    >>> _read_archive_payload_range("/tmp/x", 0, 0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.file_locking import file_read_lock_wait

  if int(length) < 0:
    raise ValueError("negative archive payload length")
  with file_read_lock_wait(archive_path):
    with open(archive_path, "rb") as fd:
      fd.seek(int(offset))
      raw = fd.read(int(length))
  return raw.decode("utf-8", errors="replace")


def _inc_counter(counters: dict, name: str, amount: int = 1) -> None:
  """
  Internal helper to handle inc counter.
  
  Args:
    counters (dict): Mapping for counters.
    name (str): String for name.
    amount (int): Integer value for amount.
  
  Returns:
    None
  
  Examples:
    >>> _inc_counter({}, "x", 0)  # doctest: +SKIP
  """
  val = counters.get(name)
  if val is None:
    return
  with val.get_lock():
    val.value = int(val.value) + int(amount)


def _release_listend_db_worker_memory() -> None:
  """
  Drop heap after flush / idle recycle (not every sample).
  
  Returns:
    None
  
  Examples:
    >>> _release_listend_db_worker_memory()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
      release_spawn_pool_worker_memory,
  )

  release_spawn_pool_worker_memory()


def _conn_max_age_s() -> float:
  """
  Internal helper to handle conn max age s.
  
  Returns:
    float: float produced by this call.
  
  Examples:
    >>> _conn_max_age_s()  # doctest: +SKIP
  """
  try:
    return max(30.0, float(cfg.get_db_conn_max_age()))
  except Exception:
    return 90.0


def _proc_field_or_none(row: Any, key: Any) -> Any:
  """
  Internal helper to handle proc field or none.
  
  Args:
    row (Any): Value to inspect (typically a numeric scalar).
    key (Any): Key passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _proc_field_or_none(None, None)  # doctest: +SKIP
  """
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


def _proc_data_row_kwargs(row: Any) -> Any:
  """
  Internal helper to handle proc data row kwargs.
  
  Args:
    row (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _proc_data_row_kwargs(None)  # doctest: +SKIP
  """
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


def _dedupe_proc_objs_keep_last(proc_objs: list) -> list:
  """
  Collapse duplicate ``(jid, host, proc)`` rows for one bulk_create upsert.

  Postgres rejects ``ON CONFLICT DO UPDATE`` when the same unique key appears
  twice in a single statement. Later samples last-write non-peak fields;
  ``vm_stk`` / ``vm_exe`` / ``vm_lib`` take GREATEST across the batch.

  Args:
    proc_objs (list): Sequence of ``proc_data`` instances (or namespaces).

  Returns:
    list: One object per unique key.

  Examples:
    >>> _dedupe_proc_objs_keep_last([])
    []
  """
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      apply_proc_peak_attrs_from_earlier,
  )

  if len(proc_objs) <= 1:
    return list(proc_objs)
  by_key: dict = {}
  for obj in proc_objs:
    key = (
        getattr(obj, "jid", None),
        getattr(obj, "host", None),
        getattr(obj, "proc", None),
    )
    if key in by_key:
      apply_proc_peak_attrs_from_earlier(by_key[key], obj)
    by_key[key] = obj
  return list(by_key.values())


def _process_sample_to_orm(
  message: str,
  *,
  host: str,
  schema: dict,
  schema_fast: dict,
  carry: Any,
) -> Tuple[list, list]:
  """
  Parse one complete sample → host_data / proc_data instances. Empty on skip.
  
  Args:
    message (str): String for message.
    host (str): String for host.
    schema (dict): Mapping for schema.
    schema_fast (dict): Mapping for schema fast.
    carry (Any): Carry passed to this helper.
  
  Returns:
    Tuple[list, list]: Tuple[list, list] produced by this call.
  
  Examples:
    >>> _process_sample_to_orm("x", "x", {}, {}, None)  # doctest: +SKIP
  """
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
  """
  Internal helper to handle worker main.
  
  Args:
    worker_idx (int): Integer value for worker idx.
    work_queue (Any): Work queue passed to this helper.
    stop_event (Any): Stop event passed to this helper.
    byte_count (Any): Byte count passed to this helper.
    byte_lock (Any): Byte lock passed to this helper.
    counters (dict): Mapping for counters.
    batch_samples (int): Integer value for batch samples.
    per_worker_budget (int): Integer value for per worker budget.
  
  Returns:
    None
  
  Examples:
    >>> _worker_main(0, None, None, None, None, {}, 0, 0)  # doctest: +SKIP
  """
  del per_worker_budget  # tracked on put; kept for future diagnostics
  # Cap BLAS/OpenMP before any numpy/pandas import (30 workers × default
  # OpenBLAS threads exhausts pthread resources → EAGAIN).
  from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env

  configure_blas_thread_env()

  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
  from hpcperfstats.dbload.lib.process_title import set_daemon_process_title
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  set_daemon_process_title(name="listend.py", role="worker", pool_kind="listend-db-pool")
  ensure_django()
  _apply_listend_db_ingest_statement_timeout()

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
    """
    Internal helper to handle recycle conn.
    
    Args:
      reason (str): String for reason.
    
    Returns:
      None
    
    Examples:
      >>> _recycle_conn("x")  # doctest: +SKIP
    """
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
    _apply_listend_db_ingest_statement_timeout()
    _inc_counter(counters, "conn_recycle")

  def _flush(*, force_memory_release: bool = True) -> None:
    """
    Internal helper to handle flush.
    
    Args:
      force_memory_release (bool): Whether to enable force memory release.
    
    Returns:
      None
    
    Examples:
      >>> _flush(True)  # doctest: +SKIP
    """
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
      if _listend_flush_error_is_poison(exc):
        pending_host = []
        pending_proc = []
        sample_count = 0
      return
    pending_host = []
    pending_proc = []
    sample_count = 0
    if force_memory_release:
      _release_listend_db_worker_memory()
    if (time.monotonic() - conn_opened_at) >= _conn_max_age_s():
      _recycle_conn(reason="age")

  def _ensure_schema_seed(host: str) -> None:
    """
    Internal helper to ensure the schema seed.
    
    Args:
      host (str): String for host.
    
    Returns:
      None
    
    Examples:
      >>> _ensure_schema_seed("x")  # doctest: +SKIP
    """
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
      # Recycle only on max age — not every idle Empty (avoids conn_recycle ≫ db_ok).
      if (time.monotonic() - conn_opened_at) >= _conn_max_age_s():
        _recycle_conn(reason="age")
      continue
    if item is None:
      break
    host = ""
    message = ""
    size = 0
    shm_name: str | None = None
    try:
      kind = item[0] if isinstance(item, tuple) and item else None
      if kind == "shm":
        _, host, shm_name, size = item
        from multiprocessing.shared_memory import SharedMemory

        shm = SharedMemory(name=shm_name)
        try:
          raw = bytes(shm.buf[: int(size)])
          message = raw.decode("utf-8", errors="replace")
        finally:
          try:
            shm.close()
          finally:
            try:
              shm.unlink()
            except FileNotFoundError:
              pass
        shm_name = None
      elif kind == "path":
        _, host, archive_path, offset, length = item
        size = int(length)
        message = _read_archive_payload_range(
            str(archive_path), int(offset), int(length),
        )
      else:
        # Legacy pickle-body tuple from older callers/tests.
        host, message, size = item
    except Exception as unpack_exc:
      _inc_counter(counters, "db_err")
      log_print(
          "ERROR: listend db ingest dequeue failed worker=%d: %s"
          % (worker_idx, unpack_exc),
          flush=True,
      )
      if shm_name:
        _unlink_shm_by_name(shm_name)
      with byte_lock:
        byte_count.value = max(0, int(byte_count.value) - int(size or 0))
      continue

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
  """
  Host-affine continuous worker pool for listend live DB ingest.
  
  Attributes:
    _byte_counts: Attribute.
    _byte_locks: Attribute.
    _counters: Attribute.
    _ctx: Attribute.
    _pause_seconds_window: Accumulated pause seconds in the current idle
      monitor window (closed intervals only).
    _pause_started_mono: Monotonic start of an open pause interval, or None.
    _queues: Attribute.
    _started: Attribute.
    _stop: Attribute.
    _window_baseline: Attribute.
    _workers: Attribute.
    batch_samples: Attribute.
    budget_bytes: Attribute.
    enabled: Attribute.
    per_worker_budget_bytes: Attribute.
    pool_processes: Attribute.
    queue_maxsize: Attribute.
  """

  def __init__(
    self,
    *,
    pool_processes: int | None = None,
    queue_max_gb: float | None = None,
    batch_samples: int | None = None,
    enabled: bool | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      pool_processes (int | None): One of ``int``, ``None``.
      queue_max_gb (float | None): One of ``float``, ``None``.
      batch_samples (int | None): One of ``int``, ``None``.
      enabled (bool | None): One of ``bool``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> ListendDbIngestPool(None, None, None, None)  # doctest: +SKIP
    """
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
    self.budget_bytes = int(budgets["budget_bytes"])
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
    self._queues: list = []
    self._byte_counts: list = []
    self._byte_locks: list = []
    self._workers: list = []
    self._started = False
    self._pause_started_mono: float | None = None
    self._pause_seconds_window = 0.0
    self._shm_seq = 0
    self._shm_seq_lock = threading.Lock()
    self._outstanding_shm: set[str] = set()
    self._outstanding_shm_lock = threading.Lock()
    # Disabled pools must not allocate spawn Event/Value/Queue objects.
    # POSIX semaphores fail with FileNotFoundError when /dev/shm is missing
    # (the [listend:main] "Failed to start listend db ingest pool: [Errno 2]"
    # log) even though start() would have been a no-op.
    if not self.enabled:
      self._ctx = None
      self._stop = None
      self._counters = {}
      self._window_baseline = self.snapshot_counters()
      return
    self._ctx = mp.get_context("spawn")
    self._stop = self._ctx.Event()
    self._counters = {
        name: self._ctx.Value("Q", 0) for name in _COUNTER_NAMES
    }
    self._window_baseline = self.snapshot_counters()

  def start(self) -> None:
    """
    Start background work for this object.
    
    Returns:
      None
    
    Examples:
      >>> ListendDbIngestPool().start()  # doctest: +SKIP
    """
    if not self.enabled or self._started:
      return
    reclaimed = _reclaim_orphan_listend_shm()
    if reclaimed:
      _inc_counter(self._counters, "shm_reclaim", reclaimed)
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
        "per_worker_budget_bytes=%d batch_samples=%d shm_reclaim=%d"
        % (
            self.pool_processes,
            self.queue_maxsize,
            self.per_worker_budget_bytes,
            self.batch_samples,
            reclaimed,
        ),
        flush=True,
    )

  def stop(self, *, join_timeout: float = _SHUTDOWN_JOIN_TIMEOUT_S) -> None:
    """
    Stop background work for this object.
    
    Args:
      join_timeout (float): Floating-point value for join timeout.
    
    Returns:
      None
    
    Examples:
      >>> ListendDbIngestPool().stop(0)  # doctest: +SKIP
    """
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
    with self._outstanding_shm_lock:
      outstanding = list(self._outstanding_shm)
      self._outstanding_shm.clear()
    for name in outstanding:
      _unlink_shm_by_name(name)
    self._started = False

  def queued_bytes(self) -> int:
    """
    Return total queued payload bytes across all workers.
    
    Returns:
      int: Sum of per-worker byte counters.
    
    Examples:
      >>> ListendDbIngestPool(enabled=False).queued_bytes()
      0
    """
    total = 0
    for byte_count in self._byte_counts:
      try:
        total += int(byte_count.value)
      except Exception:
        pass
    return total

  def worker_has_headroom(self, worker_idx: int, size: int) -> bool:
    """
    Return True when worker ``worker_idx`` can accept ``size`` more bytes.
    
    Args:
      worker_idx (int): Host-affine worker index.
      size (int): Payload size in bytes.
    
    Returns:
      bool: True when under that worker's byte budget.
    
    Examples:
      >>> p = ListendDbIngestPool(enabled=False, pool_processes=1)
      >>> p.worker_has_headroom(0, 1)
      False
    """
    if not self._started or not self._byte_counts:
      return False
    idx = int(worker_idx) % max(1, self.pool_processes)
    if idx < 0 or idx >= len(self._byte_counts):
      return False
    size_i = max(0, int(size))
    byte_count = self._byte_counts[idx]
    try:
      return int(byte_count.value) + size_i <= self.per_worker_budget_bytes
    except Exception:
      return False

  def can_enqueue(self, host: str, message: str) -> bool:
    """
    Return True when the host-affine worker can accept ``message`` now.
    
    Args:
      host (str): Monitor hostname token.
      message (str): Raw monitor payload.
    
    Returns:
      bool: True when enqueue would succeed under the byte budget.
    
    Examples:
      >>> ListendDbIngestPool(enabled=False).can_enqueue("h", "x")
      False
    """
    if not self.enabled or not self._started or self._stop.is_set():
      return False
    if not host or message is None:
      return False
    idx = host_affine_worker_index(host, self.pool_processes)
    size = _payload_byte_size(message)
    return self.worker_has_headroom(idx, size)

  def should_pause_consume(self) -> bool:
    """
    Return True when RabbitMQ consume should stop for DB backpressure.
    
    Triggers when aggregate queued bytes reach the high watermark fraction of
    the total budget, or when any worker cannot accept another minimum-floor
    payload (same condition as a would-be queue drop).
    
    Returns:
      bool: True when listend should stop consuming.
    
    Examples:
      >>> ListendDbIngestPool(enabled=False).should_pause_consume()
      False
    """
    if not self.enabled or not self._started or self._stop.is_set():
      return False
    budget = max(1, int(self.budget_bytes))
    if self.queued_bytes() >= int(_PAUSE_WATERMARK * budget):
      return True
    floor = max(1, int(_MIN_QUEUED_PAYLOAD_BYTES))
    for i in range(len(self._byte_counts)):
      if not self.worker_has_headroom(i, floor):
        return True
    return False

  def should_resume_consume(self) -> bool:
    """
    Return True when RabbitMQ consume may restart after a DB pause.
    
    Requires aggregate usage at or below the low watermark and every worker
    having headroom for a minimum-floor payload.
    
    Returns:
      bool: True when listend may resume consuming.
    
    Examples:
      >>> ListendDbIngestPool(enabled=False).should_resume_consume()
      True
    """
    if not self.enabled or not self._started:
      return True
    if self._stop.is_set():
      return True
    budget = max(1, int(self.budget_bytes))
    if self.queued_bytes() > int(_RESUME_WATERMARK * budget):
      return False
    floor = max(1, int(_MIN_QUEUED_PAYLOAD_BYTES))
    for i in range(len(self._byte_counts)):
      if not self.worker_has_headroom(i, floor):
        return False
    return True

  def note_pause_enter(self) -> None:
    """
    Record a consume-pause enter (once per False→True stop_consuming).

    Increments ``pause_enters`` and opens a monotonic pause interval when one
    is not already open (so reconnect while paused does not double-start).

    Returns:
      None

    Examples:
      >>> ListendDbIngestPool(enabled=False).note_pause_enter()
    """
    if not self._started:
      return
    _inc_counter(self._counters, "pause_enters")
    if self._pause_started_mono is None:
      self._pause_started_mono = time.monotonic()

  def note_pause_exit(self) -> None:
    """
    Close an open consume-pause interval and add elapsed time to the window.

    Call only on a real resume (True→False). Do not call on connection-loss
    abort while still paused — the open interval continues across reconnect.

    Returns:
      None

    Examples:
      >>> ListendDbIngestPool(enabled=False).note_pause_exit()
    """
    if not self._started:
      return
    started = self._pause_started_mono
    if started is None:
      return
    self._pause_seconds_window += max(0.0, time.monotonic() - started)
    self._pause_started_mono = None

  def pause_seconds_snapshot(self) -> tuple[int, int]:
    """
    Return window pause seconds and whether consume is currently paused.

    Open intervals contribute elapsed time through ``time.monotonic()`` so
    a full-window stall reports near the window length even without exit.

    Returns:
      tuple[int, int]: ``(pause_s, paused)`` where ``paused`` is ``0`` or
      ``1``.

    Examples:
      >>> ListendDbIngestPool(enabled=False).pause_seconds_snapshot()
      (0, 0)
    """
    pause_s = float(self._pause_seconds_window)
    paused = 0
    started = self._pause_started_mono
    if started is not None:
      pause_s += max(0.0, time.monotonic() - started)
      paused = 1
    return int(pause_s), paused

  def submit(
    self,
    host: str,
    message: str,
    *,
    archive_path: str | None = None,
    offset: int | None = None,
    length: int | None = None,
  ) -> bool:
    """
    Nonblocking enqueue via POSIX SharedMemory (path+range fallback).

    Prefer ``("shm", host, name, nbytes)`` so ``mp.Queue`` does not pickle the
    full monitor body. On SharedMemory create failure with archive range
    available, enqueue ``("path", host, path, offset, length)`` and increment
    ``shm_fallback``. Under ``drop``, False returns and ``queue_drops`` are
    expected when queues are full.

    Args:
      host (str): Monitor hostname token.
      message (str): Raw monitor payload string.
      archive_path (str | None): Durable archive path for path fallback.
      offset (int | None): Byte offset of this payload in ``archive_path``.
      length (int | None): UTF-8 byte length of this payload.

    Returns:
      bool: True when enqueued; False on drop / disabled / not started.

    Examples:
      >>> ListendDbIngestPool(enabled=False).submit("h", "x")
      False
    """
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

    payload = message.encode("utf-8", errors="replace")
    nbytes = len(payload)
    shm_name: str | None = None
    try:
      from multiprocessing.shared_memory import SharedMemory

      with self._shm_seq_lock:
        self._shm_seq += 1
        seq = self._shm_seq
      shm_name = "%s%d-%d" % (_LISTEND_SHM_PREFIX, os.getpid(), seq)
      shm = SharedMemory(create=True, size=max(1, nbytes), name=shm_name)
      try:
        if nbytes:
          shm.buf[:nbytes] = payload
      finally:
        shm.close()
      with self._outstanding_shm_lock:
        self._outstanding_shm.add(shm_name)
      try:
        with byte_lock:
          if int(byte_count.value) + size > self.per_worker_budget_bytes:
            raise queue.Full
          q.put_nowait(("shm", host, shm_name, nbytes))
          byte_count.value = int(byte_count.value) + size
      except Exception:
        with self._outstanding_shm_lock:
          self._outstanding_shm.discard(shm_name)
        _unlink_shm_by_name(shm_name)
        _inc_counter(self._counters, "queue_drops")
        return False
      with self._outstanding_shm_lock:
        self._outstanding_shm.discard(shm_name)
      return True
    except Exception:
      if (
          archive_path is not None
          and offset is not None
          and length is not None
      ):
        try:
          with byte_lock:
            if int(byte_count.value) + size > self.per_worker_budget_bytes:
              _inc_counter(self._counters, "queue_drops")
              return False
            q.put_nowait(
                ("path", host, archive_path, int(offset), int(length))
            )
            byte_count.value = int(byte_count.value) + size
          _inc_counter(self._counters, "shm_fallback")
          return True
        except Exception:
          _inc_counter(self._counters, "queue_drops")
          return False
      _inc_counter(self._counters, "queue_drops")
      return False

  def snapshot_counters(self) -> dict:
    """
    Snapshot counters.
    
    Returns:
      dict: dict produced by this call.
    
    Examples:
      >>> ListendDbIngestPool().snapshot_counters()  # doctest: +SKIP
    """
    out = {}
    for name in _COUNTER_NAMES:
      try:
        out[name] = int(self._counters[name].value)
      except Exception:
        out[name] = 0
    depth = 0
    for q in self._queues:
      try:
        depth += int(q.qsize())
      except Exception:
        pass
    out["db_queue_depth"] = depth
    out["db_queued_bytes"] = self.queued_bytes()
    return out

  def window_counters_and_reset(self) -> dict:
    """
    Return counters since last window baseline, then reset the baseline.

    Also snapshots ``pause_s`` / ``paused`` for the idle monitor, then clears
    the window pause accumulator. If still paused, restarts the open monotonic
    start so the next window does not double-count already-reported seconds.

    Returns:
      dict: Window deltas plus live ``db_queue_depth``, ``db_queued_bytes``,
      ``pause_s``, and ``paused``.

    Examples:
      >>> ListendDbIngestPool().window_counters_and_reset()  # doctest: +SKIP
    """
    now = self.snapshot_counters()
    base = self._window_baseline or {}
    delta = {}
    for key, val in now.items():
      if key in ("db_queue_depth", "db_queued_bytes"):
        delta[key] = val
      else:
        delta[key] = max(0, int(val) - int(base.get(key, 0)))
    pause_s, paused = self.pause_seconds_snapshot()
    delta["pause_s"] = pause_s
    delta["paused"] = paused
    self._pause_seconds_window = 0.0
    if self._pause_started_mono is not None:
      self._pause_started_mono = time.monotonic()
    self._window_baseline = now
    return delta

  def format_idle_monitor_suffix(self) -> str:
    """
    Format the idle-monitor DB-ingest suffix for the 10-minute status line.

    Includes window ``pause_enters``, ``pause_s``, and ``paused`` so operators
    see backpressure duration without per-flap pause/resume INFO.

    Returns:
      str: Space-separated ``key=value`` fields for the idle monitor line.

    Examples:
      >>> ListendDbIngestPool().format_idle_monitor_suffix()  # doctest: +SKIP
    """
    d = self.window_counters_and_reset()
    return (
        "db_ingest queue_drops=%d pause_enters=%d pause_s=%d paused=%d "
        "schema_miss=%d db_ok=%d db_err=%d conn_recycle=%d "
        "db_queue_depth=%d db_queued_bytes=%d batch_flush=%d "
        "shm_fallback=%d shm_reclaim=%d"
        % (
            d.get("queue_drops", 0),
            d.get("pause_enters", 0),
            d.get("pause_s", 0),
            d.get("paused", 0),
            d.get("schema_miss", 0),
            d.get("db_ok", 0),
            d.get("db_err", 0),
            d.get("conn_recycle", 0),
            d.get("db_queue_depth", 0),
            d.get("db_queued_bytes", 0),
            d.get("batch_flush", 0),
            d.get("shm_fallback", 0),
            d.get("shm_reclaim", 0),
        )
    )


# Process-global pool for listend main (set by start_listend_db_ingest_pool).
_GLOBAL_POOL: Optional[ListendDbIngestPool] = None


def get_listend_db_ingest_pool() -> Optional[ListendDbIngestPool]:
  """
  Return the listend db ingest pool.
  
  Returns:
    Optional[ListendDbIngestPool]: Optional[ListendDbIngestPool] — the result,
    or None when unavailable.
  
  Examples:
    >>> get_listend_db_ingest_pool()  # doctest: +SKIP
  """
  return _GLOBAL_POOL


def start_listend_db_ingest_pool(
  **kwargs: Any,
) -> Optional[ListendDbIngestPool]:
  """
  Construct and start the process-global live DB ingest pool.

  When live ingest is off (``listend_db_ingest_enabled=no`` or
  ``enabled=False``), return ``None`` without creating multiprocessing
  shared objects. Spawn ``Event``/``Value`` allocation is what raises
  ``FileNotFoundError`` when ``/dev/shm`` or ``sys.executable`` is missing.

  Args:
    **kwargs (Any): Forwarded to ``ListendDbIngestPool``:
      ``pool_processes``, ``queue_max_gb``, ``batch_samples``, ``enabled``.

  Returns:
    Optional[ListendDbIngestPool]: The started pool, an already-created
      global pool, or ``None`` when live ingest is disabled.

  Examples:
    >>> start_listend_db_ingest_pool(enabled=False)
    None
  """
  global _GLOBAL_POOL
  if _GLOBAL_POOL is not None:
    return _GLOBAL_POOL
  enabled = kwargs.get("enabled")
  if enabled is None:
    enabled = bool(cfg.get_listend_db_ingest_enabled())
  if not enabled:
    log_print(
        "listend db ingest disabled; skipping live DB pool",
        flush=True,
    )
    return None
  pool = ListendDbIngestPool(**kwargs)
  if pool.enabled:
    pool.start()
  _GLOBAL_POOL = pool
  return pool


def stop_listend_db_ingest_pool() -> None:
  """
  Stop the listend db ingest pool.
  
  Returns:
    None
  
  Examples:
    >>> stop_listend_db_ingest_pool()  # doctest: +SKIP
  """
  global _GLOBAL_POOL
  pool = _GLOBAL_POOL
  _GLOBAL_POOL = None
  if pool is not None:
    try:
      pool.stop()
    except Exception as exc:
      log_print("ERROR: listend db ingest pool stop failed: %s" % exc, flush=True)


def submit_listend_db_ingest(
  host: str,
  message: str,
  *,
  archive_path: str | None = None,
  offset: int | None = None,
  length: int | None = None,
) -> bool:
  """
  Best-effort enqueue; never raises into the ack path.

  Args:
    host (str): Monitor hostname token.
    message (str): Raw monitor payload.
    archive_path (str | None): Durable archive path for shm create fallback.
    offset (int | None): Byte offset of this payload in ``archive_path``.
    length (int | None): UTF-8 byte length of this payload.

  Returns:
    bool: True when enqueued; False when pool missing or drop.

  Examples:
    >>> submit_listend_db_ingest("x", "x")
    False
  """
  pool = _GLOBAL_POOL
  if pool is None:
    return False
  try:
    return bool(
        pool.submit(
            host,
            message,
            archive_path=archive_path,
            offset=offset,
            length=length,
        )
    )
  except Exception as exc:
    try:
      log_print(
          "ERROR: listend db ingest submit failed host=%s: %s" % (host, exc),
          flush=True,
      )
    except Exception:
      pass
    return False
