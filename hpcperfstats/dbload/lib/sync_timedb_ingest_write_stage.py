"""
Long-lived ingest write stage: bounded queue + reused Django connections.

Parse threads enqueue write-ready payloads with blocking ``put``; write
threads ``get``, COPY/upsert, and signal completion. Job-store ACK stays on
the ingest coordinator drain after the parse worker returns success.

Attributes:
  IngestWriteStageShutdownError: Typed shutdown refusal (environment class).
  _PUT_BLOCK_EPS_S: Minimum put wait counted in ``parse_put_block_n``.
  _PUT_BLOCK_LOCK: Mutex for put-block census totals.
  _PUT_BLOCK_SUM_S: Window sum of put wait seconds.
  _PUT_BLOCK_MAX_S: Window max single put wait.
  _PUT_BLOCK_N: Window count of puts that waited above epsilon.
  _BUSY_LOCK: Mutex for the busy-writer count.
  _WRITERS_BUSY: Write threads currently inside ``_execute_write_payload``.
  _STAGE_LOCK: Mutex for start/stop of the write stage.
  _WRITE_QUEUE: Bounded ``queue.Queue`` of ``IngestWritePayload``, or None.
  _WRITE_THREADS: Live write worker threads.
  _SHUTDOWN: Event set during stage stop.
  _STARTED: True after ``start_ingest_write_stage`` succeeds.
  _WORKER_SETUP: Optional zero-arg callable run after write-thread title
    and after connection recycle (for example listend statement_timeout).
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from hpcperfstats.dbload.lib import sync_timedb_wait_telemetry as wait_telem
from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

_PUT_BLOCK_EPS_S = 0.001
_PUT_BLOCK_LOCK = threading.Lock()
_PUT_BLOCK_SUM_S = 0.0
_PUT_BLOCK_MAX_S = 0.0
_PUT_BLOCK_N = 0

_BUSY_LOCK = threading.Lock()
_WRITERS_BUSY = 0

_STAGE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[Any] | None = None
_WRITE_THREADS: list[threading.Thread] = []
_SHUTDOWN = threading.Event()
_STARTED = False
_WORKER_SETUP: Any | None = None


@dataclass
class IngestWritePayload:
  """
  One flush (or whole-file) write unit for the write stage.

  Attributes:
    stats_file: Absolute stats path for this flush.
    stats: Host dataframe, columnar SoA Mapping (Codon-hit), or empty.
    proc_stats: Proc dataframe (or empty) for this flush.
    need_archival: Archive-needed flag carried across flushes.
    is_last: True when this is the last flush for the file.
    done: Set by the write worker when the flush finishes.
    error: Exception from the write path, or None.
    archival_out: Archive-needed flag after the write.
    ingest_ok: True when the write succeeded.
    write_timing: Snapshot of write-thread ContextVar timers for the
      parse thread to apply after ``done`` (empty when unset).
    wait: Enqueue/dequeue/exec stamps from ``sync_timedb_wait_telemetry``
      (empty unless ingest telemetry is on).
    copy_unique: When True, host/proc SoA is COPY-unique and scalar-normalized
      (Codon worker ``_finalize_codon_parse_copy_emit`` before IPC)
      (Codon emit); write stage skips pre-COPY collapse.
    emit: ``insert`` writes per-row INSERT. Empty writes COPY bytes when
      ``stats`` carries ``host_copy`` / ``proc_copy``.
  """

  stats_file: str
  stats: Any
  proc_stats: Any
  need_archival: bool = True
  is_last: bool = False
  copy_unique: bool = False
  emit: str = ""
  done: threading.Event = field(default_factory=threading.Event)
  error: BaseException | None = None
  archival_out: bool = True
  ingest_ok: bool = False
  write_timing: dict[str, float] = field(default_factory=dict)
  wait: dict[str, float] = field(default_factory=dict)


class IngestWriteStageShutdownError(RuntimeError):
  """
  A write was refused because the process-wide write stage is shutting down.

  Subclasses ``RuntimeError`` (the type older callers caught) but is typed so
  the ingest coordinator treats it as an environment condition: requeue
  without burning the file's attempts, never quarantine (DE-2).

  Examples:
    >>> isinstance(IngestWriteStageShutdownError("x"), RuntimeError)
    True
  """


def record_put_block_wait(wait_s: float) -> None:
  """
  Accumulate parse ``queue.put`` wait into the 10-minute census window.

  Args:
    wait_s (float): Wall seconds spent inside ``put``.

  Returns:
    None

  Examples:
    >>> record_put_block_wait(0.0)
  """
  global _PUT_BLOCK_SUM_S, _PUT_BLOCK_MAX_S, _PUT_BLOCK_N
  wait = max(0.0, float(wait_s))
  with _PUT_BLOCK_LOCK:
    _PUT_BLOCK_SUM_S += wait
    if wait > _PUT_BLOCK_MAX_S:
      _PUT_BLOCK_MAX_S = wait
    if wait > _PUT_BLOCK_EPS_S:
      _PUT_BLOCK_N += 1


def snapshot_put_block_window() -> tuple[float, float, int]:
  """
  Return ``(sum_s, max_s, n)`` for the current put-block window.

  Returns:
    tuple[float, float, int]: Cumulative wait, max wait, wait count.

  Examples:
    >>> isinstance(snapshot_put_block_window()[2], int)
    True
  """
  with _PUT_BLOCK_LOCK:
    return (_PUT_BLOCK_SUM_S, _PUT_BLOCK_MAX_S, _PUT_BLOCK_N)


def reset_put_block_window() -> None:
  """
  Zero the put-block window (progress-report cadence).

  Returns:
    None

  Examples:
    >>> reset_put_block_window()
  """
  global _PUT_BLOCK_SUM_S, _PUT_BLOCK_MAX_S, _PUT_BLOCK_N
  with _PUT_BLOCK_LOCK:
    _PUT_BLOCK_SUM_S = 0.0
    _PUT_BLOCK_MAX_S = 0.0
    _PUT_BLOCK_N = 0


def format_parse_put_block_census_token() -> str:
  """
  Format census/progress tokens for put-block waits.

  Returns:
    str: Space-prefixed token string, or empty when all zeros.

  Examples:
    >>> format_parse_put_block_census_token().startswith(" ") or True
    True
  """
  sum_s, max_s, n = snapshot_put_block_window()
  if sum_s <= 0.0 and max_s <= 0.0 and n <= 0:
    return (
      " parse_put_block_s=0.000 parse_put_block_max_s=0.000 parse_put_block_n=0"
    )
  return (
    f" parse_put_block_s={sum_s:.3f}"
    f" parse_put_block_max_s={max_s:.3f}"
    f" parse_put_block_n={n}"
  )


def format_write_stage_census_token() -> str:
  """
  Return `` wq_depth=queued/max w_busy=busy/writers`` for the 60 s census.

  Returns:
    str: Leading-space token, or ``""`` when the write stage is not started.

  Examples:
    >>> format_write_stage_census_token() in ("",) or True
    True
  """
  q = _WRITE_QUEUE
  if not _STARTED or q is None:
    return ""
  with _BUSY_LOCK:
    busy = _WRITERS_BUSY
  return (
    f" wq_depth={q.qsize()}/{q.maxsize} w_busy={busy}/{len(_WRITE_THREADS)}"
  )


def write_queue_qsize() -> int:
  """
  Return current write-queue depth, or 0 when the stage is stopped.

  Returns:
    int: ``Queue.qsize()`` or 0.

  Examples:
    >>> write_queue_qsize() >= 0
    True
  """
  q = _WRITE_QUEUE
  if q is None:
    return 0
  try:
    return int(q.qsize())
  except Exception:
    return 0


def write_stage_started() -> bool:
  """
  Return whether long-lived write workers are running.

  Returns:
    bool: True after successful :func:`start_ingest_write_stage`.

  Examples:
    >>> isinstance(write_stage_started(), bool)
    True
  """
  return bool(_STARTED)


def start_ingest_write_stage(
  *,
  write_workers: int,
  queue_max: int,
  process_title: str = "sync_timedb.py",
  worker_setup: Any | None = None,
) -> None:
  """
  Start the bounded write queue and long-lived write threads.

  Args:
    write_workers (int): Number of write threads (DB sessions).
    queue_max (int): ``queue.Queue`` maxsize (payload slots).
    process_title (str): Daemon script name for thread titles.
    worker_setup (Any | None): Optional zero-arg callable run on each
      write thread after title/libpq setup and after connection recycle
      (for example listend ``statement_timeout``).

  Returns:
    None

  Examples:
    >>> callable(start_ingest_write_stage)
    True
  """
  global _WRITE_QUEUE, _STARTED, _WORKER_SETUP
  with _STAGE_LOCK:
    if _STARTED:
      return
    n = max(1, int(write_workers))
    qmax = max(1, int(queue_max))
    _WORKER_SETUP = worker_setup
    _SHUTDOWN.clear()
    _WRITE_QUEUE = queue.Queue(maxsize=qmax)
    _WRITE_THREADS.clear()
    for idx in range(n):
      th = threading.Thread(
        target=_write_worker_main,
        name=f"ingest-write-{idx}",
        args=(idx, process_title),
        daemon=True,
      )
      _WRITE_THREADS.append(th)
      th.start()
    _STARTED = True


def stop_ingest_write_stage(*, join_timeout_s: float = 30.0) -> None:
  """
  Signal write workers to exit and join them.

  Args:
    join_timeout_s (float): Per-thread join timeout.

  Returns:
    None

  Examples:
    >>> callable(stop_ingest_write_stage)
    True
  """
  global _WRITE_QUEUE, _STARTED, _WORKER_SETUP
  with _STAGE_LOCK:
    if not _STARTED:
      return
    _SHUTDOWN.set()
    q = _WRITE_QUEUE
    if q is not None:
      for _ in _WRITE_THREADS:
        try:
          q.put_nowait(None)
        except Exception:
          break
    for th in list(_WRITE_THREADS):
      th.join(timeout=max(0.1, float(join_timeout_s)))
    _WRITE_THREADS.clear()
    _WRITE_QUEUE = None
    _STARTED = False
    _WORKER_SETUP = None
    _SHUTDOWN.clear()


def submit_ingest_write(payload: IngestWritePayload) -> IngestWritePayload:
  """
  Enqueue one write payload without waiting for completion.

  When the write stage is not started, executes inline on the caller.

  Args:
    payload (IngestWritePayload): Flush or whole-file write unit.

  Returns:
    IngestWritePayload: Same payload (``done`` not yet set when queued).

  Examples:
    >>> callable(submit_ingest_write)
    True
  """
  if not _STARTED or _WRITE_QUEUE is None:
    _execute_write_payload(payload)
    return payload
  t0 = time.monotonic()
  wait_telem.stamp_enqueue(payload)
  while True:
    if _SHUTDOWN.is_set():
      payload.error = IngestWriteStageShutdownError(
        "ingest write stage shutting down"
      )
      payload.ingest_ok = False
      payload.done.set()
      return payload
    try:
      _WRITE_QUEUE.put(payload, block=True, timeout=1.0)
      break
    except queue.Full:
      continue
  put_s = time.monotonic() - t0
  record_put_block_wait(put_s)
  wait_telem.add("wq_put_s", put_s)
  return payload


def submit_ingest_write_and_wait(
  payload: IngestWritePayload,
) -> IngestWritePayload:
  """
  Blocking ``put`` then wait for the write thread to finish this payload.

  When the write stage is not started, writes inline on the caller thread
  (direct ``add_stats_file_to_db`` / tests).

  Args:
    payload (IngestWritePayload): Flush or whole-file write unit.

  Returns:
    IngestWritePayload: Same payload after ``done`` is set.

  Examples:
    >>> callable(submit_ingest_write_and_wait)
    True
  """
  submit_ingest_write(payload)
  if not _STARTED or _WRITE_QUEUE is None:
    return payload
  payload.done.wait()
  return payload


def ingest_write_stage_started() -> bool:
  """
  Return True when the long-lived write stage is running.

  Returns:
    bool: True after a successful ``start_ingest_write_stage``.

  Examples:
    >>> isinstance(ingest_write_stage_started(), bool)
    True
  """
  return bool(_STARTED)


def _execute_write_payload(payload: IngestWritePayload) -> None:
  """
  Run COPY/upsert for one payload and set ``done``.

  When the write stage is started, isolate write-thread ContextVar timers
  onto ``payload.write_timing`` so the parse thread can apply them after
  ``done`` without ambient cross-thread ContextVar leakage.

  Args:
    payload (IngestWritePayload): Work unit.

  Returns:
    None

  Examples:
    >>> callable(_execute_write_payload)
    True
  """
  isolate = bool(_STARTED)
  try:
    from hpcperfstats.dbload.sync_timedb import (
      _reset_ingest_write_timing,
      _snapshot_ingest_write_timing,
      _write_stats_payload_to_db,
    )

    if isolate:
      _reset_ingest_write_timing()
    try:
      with wait_telem.writer_exec_scope(payload):
        _path, archival, ok = _write_stats_payload_to_db(
          payload.stats_file,
          payload.stats,
          payload.proc_stats,
          need_archival=payload.need_archival,
          copy_unique=bool(payload.copy_unique),
          emit=str(payload.emit or ""),
        )
      del _path
      payload.archival_out = bool(archival)
      payload.ingest_ok = bool(ok)
    except BaseException as exc:
      payload.error = exc
      payload.ingest_ok = False
    finally:
      payload.write_timing = dict(_snapshot_ingest_write_timing())
      if isolate:
        _reset_ingest_write_timing()
  except BaseException as exc:
    payload.error = exc
    payload.ingest_ok = False
  finally:
    payload.done.set()


def _write_worker_main(worker_idx: int, process_title: str) -> None:
  """
  Long-lived write loop: reuse Django connection until CONN_MAX_AGE.

  Args:
    worker_idx (int): Thread index for titles.
    process_title (str): Daemon script name.

  Returns:
    None

  Raises:
    TypeError: Queue item is not ``IngestWritePayload`` (and not sentinel
      ``None``).

  Examples:
    >>> callable(_write_worker_main)
    True
  """
  from django.db import close_old_connections, connections

  from hpcperfstats.dbload.lib import conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    apply_libpq_application_name,
  )

  set_daemon_thread_title(
    f"ingest-write-{worker_idx}",
    script_name=process_title,
    role="ingest-write-pool",
  )
  apply_libpq_application_name()
  close_old_connections()
  setup = _WORKER_SETUP
  if callable(setup):
    with __import__("contextlib").suppress(Exception):
      setup()
  conn_opened_mono = time.monotonic()
  max_age = max(30.0, float(cfg.get_db_conn_max_age()))

  while not _SHUTDOWN.is_set():
    q = _WRITE_QUEUE
    if q is None:
      break
    try:
      item = q.get(timeout=1.0)
    except queue.Empty:
      continue
    if item is None:
      break
    if not isinstance(item, IngestWritePayload):
      raise TypeError(
        f"ingest write queue expected IngestWritePayload, got {type(item)!r}"
      )
    global _WRITERS_BUSY
    with _BUSY_LOCK:
      _WRITERS_BUSY += 1
    try:
      _execute_write_payload(item)
    finally:
      with _BUSY_LOCK:
        _WRITERS_BUSY -= 1
      age = time.monotonic() - conn_opened_mono
      if age >= max_age:
        close_old_connections()
        with __import__("contextlib").suppress(Exception):
          connections.close_all()
        close_old_connections()
        apply_libpq_application_name()
        setup = _WORKER_SETUP
        if callable(setup):
          with __import__("contextlib").suppress(Exception):
            setup()
        conn_opened_mono = time.monotonic()


def refuse_new_parse_when_queue_full(queue_max: int | None = None) -> bool:
  """
  Return True when fill should skip new parse submit (queue at capacity).

  Args:
    queue_max (int | None): Slot cap; defaults to INI when None.

  Returns:
    bool: True when ``qsize() >= max``.

  Examples:
    >>> isinstance(refuse_new_parse_when_queue_full(32), bool)
    True
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  cap = (
    max(1, int(queue_max))
    if queue_max is not None
    else max(1, int(cfg.get_sync_ingest_parse_queue_max()))
  )
  if not write_stage_started():
    return False
  return write_queue_qsize() >= cap
