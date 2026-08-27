"""
Greenfield Redis ``job:v1`` queue orchestrator for ``sync_timedb``.

Replaces ``run_sync_timedb_supervisor_loop`` as the sole coordinator inside
the same CLI entry. Holds an exclusive ``archive_dir`` flock, starts ingest
and populate pools, then submits streaming boot discover onto the background
executor. **MainThread** only does pool/thread maintenance (populate reap,
pause-protocol ingest recycle via ``AtomicPoolRef``, fail-closed coordinator
death, shutdown drain barrier). Fill/drain/classify run on titled subsystem
threads: ``ingest-coordinator``, ``append-coordinator``,
``day-close-coordinator``, ``discover-bg``, ``reconstruct-coordinator``.
Append drain enqueues day_close via cheap tar-deduped enqueue (no archive
find on the coordinator). Never starts ``ArchiveJanitor`` or the retired
supervisor loop.

Attributes:
  CENSUS_LOG_INTERVAL_S: Minimum seconds between structured census log lines.
  DAY_CLOSE_THREAD_NAME_PREFIX: Thread name prefix for day_close workers.
  INGEST_WATCHDOG_GRACE_S: Slack added to the per-file ingest budget before a
    still-unready worker is treated as dead.
  INGEST_FILL_SKIP_BUDGET: Max unsubmittable ingest claims processed per fill
    tick before returning to the main loop.
  APPEND_FILL_SKIP_BUDGET: Max impossible append claims (missing path /
    unresolved daily tar) ACK-dropped per fill tick before yielding.
  SHUTDOWN_DRAIN_TIMEOUT_S: Bounded wall clock for the cooperative drain.
  _IDLE_RECONSTRUCT_MIN_INTERVAL_S: Min seconds between idle reconstruct passes.
  _SHUTDOWN_REQUESTED: Event set by ``SIGTERM``/``SIGINT`` handlers.
  _TRANSIENT_DAY_CLOSE_OUTCOMES: day_close results that requeue without
    burning a retry attempt.
  _discover_bg_executor: Single-worker executor for idle GNU find (P1-10).
  _discover_bg_future: In-flight background discover future, or ``None``.
  _discover_bg_lock: Serializes submit/shutdown of background discover.
  _last_idle_reconstruct_mono: Monotonic timestamp of last idle reconstruct.
  PROGRESS_REPORT_INTERVAL_S: Alias of progress module 600s emit interval.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date, datetime
from multiprocessing.pool import AsyncResult
from typing import Any, Callable, Iterable

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import shutdown_utils as _shutdown_utils
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as it
from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
from hpcperfstats.dbload.lib import sync_timedb_progress_report as progress
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
  create_sync_timedb_spawn_pool,
)
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.process_title import (
  apply_pool_worker_process_title,
  set_daemon_thread_title,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_dir_lock import (
  exclusive_archive_dir_flock,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  calendar_date_from_daily_tar_path,
  daily_tar_path_for_stats_path,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
  get_archive_members_redis_client,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  ensure_persistence_contract,
)
from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
  PopulatePoolController,
  set_populate_pool_controller,
)
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
  iter_find_stats_stdout_chunks,
)

DAY_CLOSE_THREAD_NAME_PREFIX = "day-close"
CENSUS_LOG_INTERVAL_S = 60.0
PROGRESS_REPORT_INTERVAL_S = progress.PROGRESS_REPORT_INTERVAL_S
SHUTDOWN_DRAIN_TIMEOUT_S = 120.0
# Per-tick cap on fill continues (missing path, reband, fingerprint mismatch)
# so MainThread cannot busy-spin a huge NFS-hole / reband storm.
INGEST_FILL_SKIP_BUDGET = 8
# Same bound for append fill: missing/unresolved identities are ACK-dropped
# (not requeued) so a deep LIST of gone paths cannot livelock MainThread.
APPEND_FILL_SKIP_BUDGET = 8
_TRANSIENT_DAY_CLOSE_OUTCOMES = frozenset({
    "deferred_age",
    "yielded",
    "skipped",
    "incomplete_raw",
})
# Slack over the per-file budget: a worker that is merely slow (contended DB,
# large file) must not be abandoned before the ingest path itself gives up.
INGEST_WATCHDOG_GRACE_S = it.STALL_ABORT_GRACE_S
_IDLE_RECONSTRUCT_MIN_INTERVAL_S = 30.0
_last_idle_reconstruct_mono = 0.0
_SHUTDOWN_REQUESTED = threading.Event()
_discover_bg_lock = threading.Lock()
_discover_bg_executor: ThreadPoolExecutor | None = None
_discover_bg_future: Future | None = None


class AtomicPoolRef:
  """
  Thread-safe holder for the current ingest ``multiprocessing.Pool``.

  MainThread publishes replacements after the pause protocol; coordinators
  only ``get()`` before ``apply_async``.

  Attributes:
    _lock: Guards ``_pool``.
    _pool: Current pool object.
  """

  def __init__(self, pool: Any) -> None:
    """
    Wrap an initial pool object.

    Args:
      pool (Any): Initial spawn pool (may be ``None`` in tests).

    Returns:
      None

    Examples:
      >>> AtomicPoolRef(None).get() is None
      True
    """
    self._lock = threading.Lock()
    self._pool = pool

  def get(self) -> Any:
    """
    Return the current pool reference.

    Returns:
      Any: Pool last published via ``set``.

    Examples:
      >>> AtomicPoolRef("p").get()
      'p'
    """
    with self._lock:
      return self._pool

  def set(self, pool: Any) -> None:
    """
    Publish a replacement pool for coordinators.

    Args:
      pool (Any): New pool object.

    Returns:
      None

    Examples:
      >>> r = AtomicPoolRef(None); r.set("n"); r.get()
      'n'
    """
    with self._lock:
      self._pool = pool


class IngestRecycleGate:
  """
  Pause protocol between ingest-coordinator and MainThread pool recycle.

  Attributes:
    recycle_requested: Set by ingest-coordinator when a recycle is needed.
    paused: Set when ingest-coordinator has stopped ``apply_async``.
  """

  def __init__(self) -> None:
    """
    Create unset recycle/paused events (coordinator not paused).

    Returns:
      None

    Examples:
      >>> g = IngestRecycleGate(); g.recycle_requested.is_set()
      False
    """
    self.recycle_requested = threading.Event()
    self.paused = threading.Event()


class SubsystemShutdownBarrier:
  """
  MainThread ``draining`` plus per-coordinator ``drained`` events.

  Attributes:
    draining: Set when cooperative shutdown begins.
    drained: Map of coordinator role → Event set when that role finished.
  """

  def __init__(self, names: Iterable[str]) -> None:
    """
    Allocate one ``drained`` Event per coordinator name.

    Args:
      names (Iterable[str]): Coordinator role names.

    Returns:
      None

    Examples:
      >>> SubsystemShutdownBarrier(["ingest"]).draining.is_set()
      False
    """
    self.draining = threading.Event()
    self.drained: dict[str, threading.Event] = {
        str(name): threading.Event() for name in names
    }

  def mark_drained(self, name: str) -> None:
    """
    Signal that one coordinator finished its drain.

    Args:
      name (str): Coordinator role name.

    Returns:
      None

    Examples:
      >>> b = SubsystemShutdownBarrier(["x"]); b.mark_drained("x")
      >>> b.drained["x"].is_set()
      True
    """
    ev = self.drained.get(str(name))
    if ev is not None:
      ev.set()

  def all_drained(self) -> bool:
    """
    True when every registered coordinator has marked drained.

    Returns:
      bool: All ``drained`` events set.

    Examples:
      >>> SubsystemShutdownBarrier([]).all_drained()
      True
    """
    return all(ev.is_set() for ev in self.drained.values())


def _discover_bg_is_busy() -> bool:
  """
  True when a background discover future is still running.

  Returns:
    bool: True when ``_discover_bg_future`` exists and is not done.

  Examples:
    >>> isinstance(_discover_bg_is_busy(), bool)
    True
  """
  with _discover_bg_lock:
    fut = _discover_bg_future
  return fut is not None and not fut.done()


def fail_closed_on_coordinator_death(
  *,
  role: str,
  log_fn: Callable[..., None] | None = None,
  exit_fn: Callable[[int], None] | None = None,
) -> None:
  """
  Exit the process when a subsystem thread dies unexpectedly.

  Silent restart with empty local inflight maps is forbidden (Principal C2).

  Args:
    role (str): Dead coordinator role name for logs.
    log_fn (Callable[..., None] | None): Optional logger.
    exit_fn (Callable[[int], None] | None): Process exit hook (tests inject).

  Returns:
    None

  Examples:
    >>> fail_closed_on_coordinator_death(  # doctest: +SKIP
    ...   role="ingest-coordinator", exit_fn=lambda c: None
    ... )
  """
  _log(
      "queue_orchestrator fail-closed coordinator death role=%s"
      % role,
      log_fn=log_fn,
  )
  request_shutdown()
  (exit_fn or os._exit)(1)


def shutdown_requested() -> bool:
  """
  Return True once a cooperative shutdown signal has been observed.

  Returns:
    bool: True when the orchestrator loop should stop taking new work.

  Examples:
    >>> isinstance(shutdown_requested(), bool)
    True
  """
  return _SHUTDOWN_REQUESTED.is_set()


def request_shutdown() -> None:
  """
  Mark a cooperative shutdown request (signal-safe, flag only).

  The handler does no logging, no Redis I/O, and no lock acquisition, so a
  signal delivered inside a Redis call or while holding the archive flock
  cannot deadlock or re-enter non-reentrant code.

  Returns:
    None

  Examples:
    >>> request_shutdown()
    >>> shutdown_requested()
    True
    >>> reset_shutdown_for_tests()
  """
  _SHUTDOWN_REQUESTED.set()
  _shutdown_utils.shutdown_requested[0] = True


def reset_shutdown_for_tests() -> None:
  """
  Clear the cooperative shutdown flag (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_shutdown_for_tests()
  """
  _SHUTDOWN_REQUESTED.clear()
  _shutdown_utils.shutdown_requested[0] = False


def install_cooperative_shutdown_handlers(
  *,
  log_fn: Callable[..., None] | None = None,
) -> bool:
  """
  Install flag-only ``SIGTERM``/``SIGINT`` handlers for the main thread.

  Args:
    log_fn (Callable[..., None] | None): Optional logger for the failure path.

  Returns:
    bool: True when both handlers were installed.

  Examples:
    >>> isinstance(install_cooperative_shutdown_handlers(), bool)
    True
  """

  def _handler(_signum: int, _frame: Any) -> None:
    """
    Flag-only signal handler: request cooperative shutdown.

    Args:
      _signum (int): Delivered signal number.
      _frame (Any): Interpreter frame (unused).

    Returns:
      None

    Examples:
      >>> callable(_handler)
      True
    """
    request_shutdown()

  installed = True
  for signum in (signal.SIGTERM, signal.SIGINT):
    try:
      signal.signal(signum, _handler)
    except (ValueError, OSError, RuntimeError) as exc:
      installed = False
      _log(
          "queue_orchestrator signal handler unavailable sig=%s err=%s"
          % (signum, type(exc).__name__),
          log_fn=log_fn,
      )
  return installed


def _log(msg: str, *, log_fn: Callable[..., None] | None = None) -> None:
  """
  Emit an orchestrator log line via ``log_fn`` or ``log_print``.

  Falls back to ``log_print`` (role-prefixed atomic write) when ``log_fn`` is
  None — never bare ``print``.

  Args:
    msg (str): Message body.
    log_fn (Callable[..., None] | None): Optional logger (``flush`` kwargs OK).

  Returns:
    None

  Examples:
    >>> _log("x", log_fn=lambda *a, **k: None)
  """
  if log_fn is not None:
    log_fn(msg, flush=True)
  else:
    log_print(msg, flush=True)


def _day_token_from_date(cal: Any) -> str | None:
  """
  Render an ISO day token from a ``date`` (or None).

  Args:
    cal (Any): ``date`` instance or None.

  Returns:
    str | None: ``YYYY-MM-DD`` or None.

  Examples:
    >>> _day_token_from_date(None) is None
    True
  """
  if cal is None:
    return None
  try:
    return cal.isoformat()
  except Exception:
    return None


def _status_band_ratios(client: Any) -> dict[str, dict[str, int]]:
  """
  Build status footer band ratios (current/queued) including ingest hot/catchup.

  Args:
    client (Any): Redis client.

  Returns:
    dict[str, dict[str, int]]: Name → ``inflight`` / ``queued``.

  Examples:
    >>> _status_band_ratios(type("C", (), {})())  # doctest: +SKIP
    {}
  """
  out: dict[str, dict[str, int]] = {}
  try:
    census = jq.queue_census(client)
  except Exception:
    census = {}
  try:
    hot_i, catch_i = jq.count_inflight_by_band(client)
  except Exception:
    hot_i, catch_i = 0, 0
  hot_q = catch_q = 0
  try:
    zkey = jq.job_queue_key(jq.JOB_KIND_INGEST)
    hot_lo, hot_hi = jq.ingest_score_range("hot")
    catch_lo, catch_hi = jq.ingest_score_range("catchup")
    hot_q = int(client.zcount(zkey, jq._score_arg(hot_lo), jq._score_arg(hot_hi)) or 0)
    catch_q = int(
        client.zcount(zkey, jq._score_arg(catch_lo), jq._score_arg(catch_hi)) or 0,
    )
  except Exception:
    pass
  out["ingest_hot"] = {"inflight": int(hot_i), "queued": int(hot_q)}
  out["ingest_catchup"] = {"inflight": int(catch_i), "queued": int(catch_q)}
  for kind in (jq.JOB_KIND_APPEND, jq.JOB_KIND_DISCOVER, jq.JOB_KIND_DAY_CLOSE):
    entry = census.get(kind) or {}
    out[kind] = {
        "inflight": int(entry.get("inflight", 0) or 0),
        "queued": int(entry.get("queued", 0) or 0),
    }
  return out


def _emit_progress_report_if_due(
  client: Any,
  *,
  busy_flags: dict[str, bool],
  busy_lock: Any,
  log_fn: Callable[..., None] | None = None,
  force: bool = False,
) -> bool:
  """
  Emit 10-minute progress+status lines when the interval has elapsed.

  Args:
    client (Any): Redis client.
    busy_flags (dict[str, bool]): Local coordinator busy map.
    busy_lock (Any): Lock guarding ``busy_flags``.
    log_fn (Callable[..., None] | None): Optional logger.
    force (bool): Bypass interval (tests / shutdown).

  Returns:
    bool: True when a report was emitted.

  Examples:
    >>> _emit_progress_report_if_due(None, busy_flags={}, busy_lock=threading.Lock())
    False
  """
  state = progress.get_progress_state()
  with busy_lock:
    busy_kinds = [k for k, v in busy_flags.items() if v]
  if _discover_bg_is_busy() and "discover" not in busy_kinds:
    busy_kinds = list(busy_kinds) + ["discover"]
  try:
    census = jq.queue_census(client)
  except Exception:
    census = {}
  band = _status_band_ratios(client)
  depth_now = {
      kind: int((census.get(kind) or {}).get("queued", 0) or 0)
      for kind in jq.JOB_KINDS_ALL
  }
  depth_now["ingest_hot"] = int((band.get("ingest_hot") or {}).get("queued", 0) or 0)
  depth_now["ingest_catchup"] = int(
      (band.get("ingest_catchup") or {}).get("queued", 0) or 0,
  )
  inflight_map = {
      kind: int((census.get(kind) or {}).get("inflight", 0) or 0)
      for kind in jq.JOB_KINDS_ALL
  }
  oldest_day, oldest_age_s = progress.resolve_oldest_queued_day(client)
  if force:
    lines = state.emit_lines(
        band_ratios=band,
        busy_kinds=busy_kinds,
        census_inflight=inflight_map,
        queue_depth_now=depth_now,
        oldest_day=oldest_day,
        oldest_age_s=oldest_age_s,
    )
    for line in lines:
      _log(line, log_fn=log_fn)
    state.reset_window(queue_depth_start=depth_now)
    return True
  return state.maybe_emit_and_reset(
      interval_s=PROGRESS_REPORT_INTERVAL_S,
      band_ratios=band,
      busy_kinds=busy_kinds,
      census_inflight=inflight_map,
      queue_depth_now=depth_now,
      oldest_day=oldest_day,
      oldest_age_s=oldest_age_s,
      log_fn=lambda msg, flush=False: _log(msg, log_fn=log_fn),
  )


def _path_from_ingest_identity(identity: str) -> str:
  """
  Extract the filesystem path from an ingest ``path|size|mtime_ns`` identity.

  Args:
    identity (str): Ingest ZSET member identity.

  Returns:
    str: Path portion (may be empty when identity is malformed).

  Examples:
    >>> _path_from_ingest_identity("/a/b|10|20")
    '/a/b'
  """
  text = str(identity or "")
  if "|" not in text:
    return text
  return text.rsplit("|", 2)[0]


def _hot_days() -> int:
  """
  Return the configured hot-band window length.

  Returns:
    int: ``sync_ingest_hot_days`` (minimum 1).

  Examples:
    >>> _hot_days() >= 1
    True
  """
  return max(1, int(cfg.get_sync_ingest_hot_days()))


def assert_redis_queue_safety(client: Any) -> None:
  """
  Fail closed when Redis would silently evict durable ``job:v1`` keys.

  Args:
    client (Any): Redis client with ``config_get`` / ``ttl``.

  Returns:
    None

  Raises:
    RuntimeError: When :func:`check_redis_queue_safety` reports problems.

  Examples:
    >>> class _C:
    ...   def config_get(self, name):
    ...     return {"maxmemory-policy": "noeviction"}
    ...   def ttl(self, key):
    ...     return -1
    >>> assert_redis_queue_safety(_C())
  """
  problems = jq.check_redis_queue_safety(client)
  if problems:
    raise RuntimeError("Redis job:v1 unsafe: %s" % "; ".join(problems))


def catchup_dispatch_cap(
  *,
  hot_queued: int,
  catchup_queued: int,
  hot_cap: int,
  catchup_cap: int,
  pool: int,
) -> int:
  """
  Return how many catchup slots may be filled this tick.

  Catchup may overflow into unused pool slots only when the hot range is
  empty; otherwise hot keeps its reserved ``hot_cap``.

  Args:
    hot_queued (int): Members currently in the hot score range.
    catchup_queued (int): Members currently in the catchup score range.
    hot_cap (int): Reserved hot slots.
    catchup_cap (int): Reserved catchup slots.
    pool (int): Ingest pool size.

  Returns:
    int: Catchup fill ceiling for this tick.

  Examples:
    >>> catchup_dispatch_cap(
    ...   hot_queued=4, catchup_queued=10, hot_cap=10, catchup_cap=6, pool=16,
    ... )
    6
    >>> catchup_dispatch_cap(
    ...   hot_queued=0, catchup_queued=10, hot_cap=10, catchup_cap=6, pool=16,
    ... )
    16
  """
  del catchup_queued, hot_cap
  if int(hot_queued) <= 0:
    return max(int(catchup_cap), int(pool))
  return int(catchup_cap)


def discover_job_identity(
  archive_dir: str,
  mtime_days: int | None = None,
) -> str:
  """
  Encode a discover LIST identity for one scan window.

  Args:
    archive_dir (str): Find root.
    mtime_days (int | None): Incremental ``-mtime`` window, or ``None`` for
      a full scan.

  Returns:
    str: Stable discover identity.

  Examples:
    >>> discover_job_identity("/a", 1).startswith("rescan|")
    True
  """
  window = "all" if mtime_days is None else str(int(mtime_days))
  return "rescan|%s|mtime=%s" % (os.path.normpath(archive_dir), window)


def _iter_claim_jobs(claim: Any) -> list[Any]:
  """
  Normalize a single claim or a grouped list of claims.

  Args:
    claim (Any): One :class:`ClaimedJob` or a list of them.

  Returns:
    list[Any]: Claims to ack, requeue, or renew.

  Examples:
    >>> _iter_claim_jobs(None)
    []
  """
  if claim is None:
    return []
  if isinstance(claim, list):
    return [item for item in claim if item is not None]
  return [claim]


def _calendar_day_for_ingest_path(
  path: str,
  tgz_archive_dir: str,
) -> date | None:
  """
  Resolve the calendar day of a claimed ingest path.

  Args:
    path (str): Raw stats path (or ingest identity).
    tgz_archive_dir (str): Daily archive directory.

  Returns:
    date | None: Calendar day, or ``None`` when unresolved.

  Examples:
    >>> _calendar_day_for_ingest_path("/nope", "/d") is None
    True
  """
  tar = daily_tar_path_for_stats_path(
      _path_from_ingest_identity(path) or path, tgz_archive_dir,
  )
  if not tar:
    return None
  return calendar_date_from_daily_tar_path(tar)


def _reband_claimed_ingest_if_needed(
  client: Any,
  claim: Any,
  tgz_archive_dir: str,
  today: date | None = None,
) -> bool:
  """
  Requeue a claimed ingest job when its live band no longer matches.

  Args:
    client (Any): Redis client.
    claim (Any): :class:`ClaimedJob` from a hot or catchup claim.
    tgz_archive_dir (str): Daily archive directory.
    today (date | None): Local today override for tests.

  Returns:
    bool: True when the job was requeued onto a different band.

  Examples:
    >>> _reband_claimed_ingest_if_needed(None, None, "/d")
    False
  """
  if claim is None or client is None:
    return False
  today_d = today or date.today()
  day = _calendar_day_for_ingest_path(claim.identity, tgz_archive_dir)
  if day is None:
    return False
  band = jr.select_ingest_band(day, today=today_d, hot_days=_hot_days())
  claimed_band = (
      jq.decode_ingest_band(claim.score) if claim.score is not None else band
  )
  if band == claimed_band:
    return False
  score = jq.encode_ingest_score(
      band=band,
      day=day,
      today=today_d,
      identity=claim.identity,
  )
  jq.requeue_job(
      client,
      kind=jq.JOB_KIND_INGEST,
      identity=claim.identity,
      owner_token=claim.owner_token,
      score=score,
  )
  return True


def _day_close_min_age_hours() -> float:
  """
  Return the configured day_close min-age hours after day end.

  Returns:
    float: ``sync_day_close_min_age_hours`` (minimum 0).

  Examples:
    >>> _day_close_min_age_hours() >= 0.0
    True
  """
  return max(0.0, float(cfg.get_sync_day_close_min_age_hours()))


def _boot_stream_discover(
  client: Any,
  archive_dir: str,
  *,
  tgz_archive_dir: str,
  log_fn: Callable[..., None] | None = None,
  mtime_days: int | None = None,
  startdate: Any = None,
  enddate: Any = None,
) -> jd.StreamingDiscoverStats:
  """
  Stream GNU find stdout into ingest/append/day_close jobs as paths arrive.

  Does **not** call capture-all ``run_find_stats``. Empty Redis before or after
  this call does **not** mean caught up. ``mtime_days=None`` is a full scan
  (boot); a positive window is the periodic reconstruct rescan. Append
  skip-complete uses :func:`jr.discover_append_is_complete` (warm Redis /
  open-tar only; never ``populate_and_wait``).

  Args:
    client (Any): Redis client for ``job:v1``.
    archive_dir (str): Archive data directory (find root).
    tgz_archive_dir (str): Daily archive directory for append classify.
    log_fn (Callable[..., None] | None): Optional logger.
    mtime_days (int | None): Optional GNU find ``-mtime`` window.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    StreamingDiscoverStats: Boot discover counters.

  Examples:
    >>> class _C:
    ...   def zadd(self, *a, **k):
    ...     return 0
    ...   def rpush(self, *a, **k):
    ...     return 0
    >>> isinstance(
    ...   _boot_stream_discover(
    ...     _C(), "/nope", tgz_archive_dir="/d", log_fn=lambda *a, **k: None,
    ...   ),
    ...   jd.StreamingDiscoverStats,
    ... )
    True
  """
  today = date.today()
  chunks = iter_find_stats_stdout_chunks(
      archive_dir, mtime_days=mtime_days,
  )
  stats = jd.stream_enqueue_ingest_from_find_stdout_chunks(
      client,
      chunks,
      tgz_archive_dir=tgz_archive_dir,
      today=today,
      hot_days=_hot_days(),
      archive_data_dir=archive_dir,
      calendar_day_fn=lambda rec: jd.calendar_day_from_find_record(
          rec, tgz_archive_dir,
      ),
      startdate=startdate,
      enddate=enddate,
      append_is_complete_fn=jr.discover_append_is_complete,
  )
  day_n = _enqueue_day_closes_for_daily_dir(
      client,
      tgz_archive_dir=tgz_archive_dir,
  )
  _log(
      "queue_orchestrator boot discover seen=%d ingest=%d append=%d "
      "day_close=%d skipped=%d daily_scan_day_close=%d"
      % (
          stats.seen,
          stats.enqueued_ingest,
          stats.enqueued_append,
          stats.enqueued_day_close,
          stats.skipped_complete,
          day_n,
      ),
      log_fn=log_fn,
  )
  return stats


def _discover_executor() -> ThreadPoolExecutor:
  """
  Return the process-wide single-worker executor for idle discover.

  Returns:
    ThreadPoolExecutor: Shared executor with ``max_workers=1``.

  Examples:
    >>> callable(_discover_executor)
    True
  """
  global _discover_bg_executor
  with _discover_bg_lock:
    if _discover_bg_executor is None:
      _discover_bg_executor = ThreadPoolExecutor(
          max_workers=1,
          thread_name_prefix="discover-bg",
      )
    return _discover_bg_executor


def _run_background_discover(
  client: Any,
  archive_dir: str,
  *,
  tgz_archive_dir: str,
  log_fn: Callable[..., None] | None,
  mtime_days: int | None,
  startdate: Any,
  enddate: Any,
) -> None:
  """
  Claim one discover job and stream GNU find off the orchestrator MainThread.

  Args:
    client (Any): Redis client (pool-backed; command-safe across threads).
    archive_dir (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    log_fn (Callable[..., None] | None): Optional logger.
    mtime_days (int | None): Incremental find window.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    None

  Examples:
    >>> callable(_run_background_discover)
    True
  """
  try:
    claim = jq.claim_list_job(
        client,
        kind=jq.JOB_KIND_DISCOVER,
        owner_token=jq.make_lease_owner_token(),
    )
  except Exception:
    return
  if claim is None:
    return
  try:
    _boot_stream_discover(
        client,
        archive_dir,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
        mtime_days=mtime_days,
        startdate=startdate,
        enddate=enddate,
    )
  finally:
    try:
      jq.ack_job(
          client,
          kind=jq.JOB_KIND_DISCOVER,
          identity=claim.identity,
          owner_token=claim.owner_token,
      )
    except Exception:
      pass


def _submit_background_discover(
  client: Any,
  archive_dir: str,
  *,
  tgz_archive_dir: str,
  log_fn: Callable[..., None] | None = None,
  mtime_days: int | None = None,
  startdate: Any = None,
  enddate: Any = None,
) -> None:
  """
  Submit idle discover to the background executor when none is already running.

  Args:
    client (Any): Redis client.
    archive_dir (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    log_fn (Callable[..., None] | None): Optional logger.
    mtime_days (int | None): Incremental find window.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    None

  Examples:
    >>> callable(_submit_background_discover)
    True
  """
  global _discover_bg_future, _discover_bg_executor
  # Create executor + submit under one Lock hold. Do NOT call the locked
  # discover-executor helper from here — that helper also takes
  # ``_discover_bg_lock`` (non-reentrant), which deadlocks MainThread forever
  # (hpcperfstats03 2026-08-26: py-spy idle under submit on nested Lock).
  with _discover_bg_lock:
    if _discover_bg_future is not None and not _discover_bg_future.done():
      return
    if _discover_bg_executor is None:
      _discover_bg_executor = ThreadPoolExecutor(
          max_workers=1,
          thread_name_prefix="discover-bg",
      )
    _discover_bg_future = _discover_bg_executor.submit(
        _run_background_discover,
        client,
        archive_dir,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
        mtime_days=mtime_days,
        startdate=startdate,
        enddate=enddate,
    )


def _shutdown_background_discover() -> None:
  """
  Cancel in-flight idle discover and drop the background executor.

  Returns:
    None

  Examples:
    >>> callable(_shutdown_background_discover)
    True
  """
  global _discover_bg_executor, _discover_bg_future
  with _discover_bg_lock:
    fut = _discover_bg_future
    executor = _discover_bg_executor
    _discover_bg_future = None
    _discover_bg_executor = None
  if fut is not None:
    try:
      fut.cancel()
    except Exception:
      pass
  if executor is not None:
    try:
      executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
      pass


def _enqueue_day_closes_for_daily_dir(
  client: Any,
  *,
  tgz_archive_dir: str,
) -> int:
  """
  Scan daily ``.tar`` siblings and enqueue incomplete day_close jobs.

  Args:
    client (Any): Redis client with ``rpush``.
    tgz_archive_dir (str): Daily archive directory.

  Returns:
    int: Number of newly enqueued day_close identities.

  Examples:
    >>> class _C:
    ...   def rpush(self, *a, **k):
    ...     return 1
    >>> _enqueue_day_closes_for_daily_dir(_C(), tgz_archive_dir="/nope")
    0
  """
  root = str(tgz_archive_dir or "").strip()
  if not root or not os.path.isdir(root):
    return 0
  enqueued = 0
  for name in os.listdir(root):
    if not name.endswith(".tar") or name.endswith(".tar.zst"):
      continue
    if len(name) < 14:
      continue
    day_token = name[:10]
    try:
      cal = date.fromisoformat(day_token)
    except ValueError:
      continue
    tar_path = os.path.normpath(os.path.join(root, name))
    if jr.enqueue_cheap_day_close_if_needed(
        client,
        tar_path,
        calendar_day=cal,
    ):
      enqueued += 1
      progress.record(day_token, "incomplete_seen", 1)
      progress.record(day_token, "reconstruct_enq", 1)
  return enqueued


def _idle_reconstruct_pass(
  client: Any,
  archive_dir: str,
  *,
  tgz_archive_dir: str,
  log_fn: Callable[..., None] | None = None,
  force: bool = False,
  mtime_days: int | None = None,
  startdate: Any = None,
  enddate: Any = None,
) -> int:
  """
  Interval reconstruct: rediscover via ``JOB_KIND_DISCOVER`` + day_close scan.

  Empty Redis does **not** mean caught up — this pass re-classifies from disk.
  Throttled to at most once per ``_IDLE_RECONSTRUCT_MIN_INTERVAL_S`` unless
  ``force`` (``run_once`` exit path). ``force=True`` claims discover on this
  thread so tests and run_once can observe a complete pass; ``force=False``
  enqueues discover and runs GNU find on the background executor (P1-10).

  Args:
    client (Any): Redis client.
    archive_dir (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    log_fn (Callable[..., None] | None): Optional logger.
    force (bool): Bypass throttle when True.
    mtime_days (int | None): Incremental find window; ``None`` is a full scan.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    int: Approximate work units enqueued (discover runs + day_close pushes).

  Examples:
    >>> class _C:
    ...   def rpush(self, *a, **k):
    ...     return 1
    ...   def lpop(self, *a, **k):
    ...     return None
    >>> _idle_reconstruct_pass(_C(), "/nope", tgz_archive_dir="/d", force=True)
    0
  """
  global _last_idle_reconstruct_mono
  now = time.monotonic()
  if (
      not force
      and (now - _last_idle_reconstruct_mono) < _IDLE_RECONSTRUCT_MIN_INTERVAL_S
  ):
    return 0
  # H8: do not enqueue/submit discover while discover-bg is already running.
  # Skip without burning the throttle so the next tick can retry promptly.
  if not force and _discover_bg_is_busy():
    return 0
  _last_idle_reconstruct_mono = now
  identity = discover_job_identity(archive_dir, mtime_days)
  try:
    jq.enqueue_list_job(
        client,
        kind=jq.JOB_KIND_DISCOVER,
        identity=identity,
        dedupe=True,
    )
  except Exception:
    pass
  work = 0
  if not force:
    _submit_background_discover(
        client,
        archive_dir,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
        mtime_days=mtime_days,
        startdate=startdate,
        enddate=enddate,
    )
  else:
    while True:
      try:
        claim = jq.claim_list_job(
            client,
            kind=jq.JOB_KIND_DISCOVER,
            owner_token=jq.make_lease_owner_token(),
        )
      except Exception:
        break
      if claim is None:
        break
      work += 1
      stats = _boot_stream_discover(
          client,
          archive_dir,
          tgz_archive_dir=tgz_archive_dir,
          log_fn=log_fn,
          mtime_days=mtime_days,
          startdate=startdate,
          enddate=enddate,
      )
      work += int(stats.enqueued_ingest) + int(stats.enqueued_append)
      work += int(stats.enqueued_day_close)
      try:
        jq.ack_job(
            client,
            kind=jq.JOB_KIND_DISCOVER,
            identity=claim.identity,
            owner_token=claim.owner_token,
        )
      except Exception:
        pass
  work += _enqueue_day_closes_for_daily_dir(
      client,
      tgz_archive_dir=tgz_archive_dir,
  )
  return work


def _ingest_worker(lock: Any, path: str) -> Any:
  """
  Spawn-pool ingest entry: parse + write one closed raw stats path.

  Args:
    lock (Any): Manager write lock (or shard list handled by callee).
    path (str): Absolute closed raw stats path.

  Returns:
    Any: Packed ingest worker result from ``add_stats_file_to_db``.

  Examples:
    >>> _ingest_worker(None, "/x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.sync_timedb import (
      IngestPerFileTimeoutError,
      _apply_ingest_session_statement_timeout,
      _ingest_outcome_meta,
      _log_ingest_per_file_timeout,
      _merge_ingest_write_timing_into_meta,
      _pack_ingest_worker_result,
      _record_ingest_marks_from_worker_result,
      add_stats_file_to_db,
  )

  _apply_ingest_session_statement_timeout()
  try:
    result = add_stats_file_to_db(lock, path)
  except TimeoutError as exc:
    # Multiprocessing may surface IngestPerFileTimeoutError as TimeoutError.
    elapsed = float(getattr(exc, "elapsed_s", 0.0) or 0.0)
    stage = str(getattr(exc, "stage", "ingest") or "ingest")
    if isinstance(exc, IngestPerFileTimeoutError):
      _log_ingest_per_file_timeout(exc)
    result = _pack_ingest_worker_result(
        path,
        False,
        False,
        elapsed,
        _merge_ingest_write_timing_into_meta(
            _ingest_outcome_meta(
                outcome="timeout",
                fail_reason=stage,
                archive_skip="timeout",
            ),
        ),
    )
  _record_ingest_marks_from_worker_result(result)
  return result


def _append_worker(archive_info: Any) -> Any:
  """
  Spawn-pool append entry: append closed raw paths to a daily ``.tar``.

  Args:
    archive_info (Any): ``(tar_path, [stats_paths…])`` tuple for
      ``archive_stats_files``.

  Returns:
    Any: Archive worker result.

  Examples:
    >>> _append_worker(("/d/2026-01-01.tar", []))  # doctest: +SKIP
  """
  from hpcperfstats.dbload.sync_timedb import archive_stats_files

  return archive_stats_files(archive_info)


def _local_today() -> date:
  """
  Return local-calendar today for ingest band scoring.

  Args:
    None

  Returns:
    date: ``datetime.now`` in the configured local timezone, else
    ``date.today()``.

  Examples:
    >>> isinstance(_local_today(), date)
    True
  """
  tz = cfg.get_local_timezone()
  if tz is None:
    return date.today()
  return datetime.now(tz).date()


def _handoff_retryable_paths_to_ingest(
  client: Any,
  tar_path: str,
  paths: Iterable[str],
  *,
  tgz_archive_dir: str,
  archive_data_dir: str | None = None,
  reason: str = "",
  log_fn: Callable[..., None] | None = None,
  today: date | None = None,
  ingest_is_complete_fn: Callable[..., bool] | None = None,
  append_is_complete_fn: Callable[..., bool] | None = None,
) -> int:
  """
  Enqueue ingest/append jobs for day-close retryable raw paths.

  Uses reconstruct classify + enqueue so calendar day comes from the daily
  tar (never substituted with today) and complete predicates are skipped.

  Args:
    client (Any): Redis client, or ``None`` to no-op.
    tar_path (str): Daily ``.tar`` path that produced the handoff.
    paths (Iterable[str]): Retryable raw stats paths.
    tgz_archive_dir (str): Daily archive directory.
    archive_data_dir (str | None): Archive root for ingest-complete marks.
    reason (str): Handoff reason token from day-raw-removal.
    log_fn (Callable[..., None] | None): Optional logger.
    today (date | None): Override local today for tests.
    ingest_is_complete_fn (Callable[..., bool] | None): Injectable ingest
      complete predicate.
    append_is_complete_fn (Callable[..., bool] | None): Injectable append
      complete predicate.

  Returns:
    int: Count of paths that enqueued at least one job kind.

  Examples:
    >>> class _C:
    ...   def zscore(self, key, member):
    ...     return None
    ...   def zcard(self, key):
    ...     return 0
    ...   def zadd(self, key, mapping):
    ...     return 1
    ...   def hset(self, *a, **k):
    ...     return 1
    >>> _handoff_retryable_paths_to_ingest(
    ...   _C(), "/d/2020-01-01.tar", [], tgz_archive_dir="/d",
    ... )
    0
  """
  if client is None:
    return 0
  calendar_day = calendar_date_from_daily_tar_path(tar_path)
  today_local = today or _local_today()
  hot_days = int(cfg.get_sync_ingest_hot_days())
  quiet = log_fn or (lambda *a, **k: None)
  enqueued_n = 0
  for raw in paths:
    text = str(raw or "").strip()
    if not text or not os.path.isfile(text):
      continue
    try:
      st_info = os.stat(text)
      plan = jr.classify_closed_raw_path(
          text,
          tgz_archive_dir=tgz_archive_dir,
          size=int(st_info.st_size),
          mtime_ns=int(getattr(st_info, "st_mtime_ns", 0) or 0),
          calendar_day=calendar_day,
          tar_path=tar_path,
          archive_data_dir=archive_data_dir,
          ingest_is_complete_fn=ingest_is_complete_fn,
          append_is_complete_fn=append_is_complete_fn,
      )
      result = jr.enqueue_reconstruct_jobs_for_closed_path(
          client,
          plan,
          today=today_local,
          hot_days=hot_days,
      )
    except Exception as exc:
      quiet(
          "queue_orchestrator day_close handoff fail path=%s err=%s"
          % (text, type(exc).__name__),
          flush=True,
      )
      continue
    if result.get("ingest") or result.get("append"):
      enqueued_n += 1
  if enqueued_n:
    day_tok = _day_token_from_date(calendar_date_from_daily_tar_path(tar_path))
    progress.record(day_tok, "ingest_handoff", enqueued_n)
    reason_text = str(reason or "")
    if reason_text == "gate_skip":
      from hpcperfstats.dbload.sync_timedb import DEBUG as _ST_DEBUG
      if _ST_DEBUG:
        quiet(
            "DEBUG: queue_orchestrator day_close handoff_to_ingest n=%s tar=%s "
            "reason=%s"
            % (enqueued_n, tar_path, reason),
            flush=True,
        )
    else:
      quiet(
          "queue_orchestrator day_close handoff_to_ingest n=%s tar=%s reason=%s"
          % (enqueued_n, tar_path, reason),
          flush=True,
      )
  return enqueued_n


def _run_day_close_job(
  identity: str,
  *,
  tgz_archive_dir: str,
  archive_data_dir: str | None = None,
  log_fn: Callable[..., None] | None = None,
  redis_client: Any | None = None,
) -> str:
  """
  Day-close thread body: age gate, seal, raw removal, tar-drop when ready.

  Returns ``complete`` only when filesystem-complete plus min-age hold (or
  this job dropped the mutable tar after remaining raw was gone). Remaining
  closed raw returns ``incomplete_raw`` so drain requeues without ACK.
  Re-enqueues when the age gate is not yet satisfied. Does not start
  ``ArchiveJanitor``. Wires ``on_handoff_to_ingest`` so retryable raw is
  requeued onto ``job:v1`` ingest/append.

  Args:
    identity (str): Day token (``YYYY-MM-DD``) or daily ``.tar`` path.
    tgz_archive_dir (str): Daily archive directory.
    archive_data_dir (str | None): Archive root for day_raw_removal manifests.
    log_fn (Callable[..., None] | None): Optional logger.
    redis_client (Any | None): Queue Redis client for ingest handoff.

  Returns:
    str: Outcome token (``complete``, ``deferred_age``, ``incomplete_raw``,
    ``skipped``, ``verify_failed``, ``yielded``).

  Examples:
    >>> _run_day_close_job(
    ...   "2099-01-01",
    ...   tgz_archive_dir="/tmp",
    ...   log_fn=lambda *a, **k: None,
    ... ) in (
    ...   "complete", "deferred_age", "incomplete_raw",
    ...   "skipped", "verify_failed", "yielded",
    ... )
    True
  """
  text = str(identity or "").strip()
  if not text:
    return "skipped"
  day_token = progress.day_token_from_day_close_identity(text)
  if not day_token:
    return "skipped"
  if text.endswith(".tar"):
    tar_path = os.path.normpath(text)
  else:
    tar_path = os.path.normpath(
        os.path.join(tgz_archive_dir, "%s.tar" % day_token)
    )
  try:
    set_daemon_thread_title(
        "day-close",
        script_name="sync_timedb.py",
        role="day-close",
    )
  except Exception:
    pass
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      day_close_yield_requested,
  )

  yielded, _reason = day_close_yield_requested(
      tar_path, tgz_archive_dir=tgz_archive_dir, phase="day_close",
  )
  if yielded:
    return "yielded"
  try:
    cal = date.fromisoformat(day_token)
  except ValueError:
    return "skipped"
  min_age_h = _day_close_min_age_hours()
  if jr.day_close_is_complete(
      tar_path,
      calendar_day=cal,
      min_age_hours=min_age_h,
  ):
    return "complete"
  if not jr.day_close_min_age_elapsed(cal, min_age_hours=min_age_h):
    return "deferred_age"
  remaining_raw = False
  tar_dropped = False
  try:
    from django.db import close_old_connections

    from hpcperfstats.dbload.lib.conf_parser import (
        get_archive_keep_uncompressed_tar,
        get_archive_zstd_level,
        get_archive_zstd_threads,
        get_host_name_ext,
        get_local_timezone,
    )
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        dedupe_tar_keep_largest_file_per_member,
        seal_dirty_daily_archives,
    )
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        DayRawRemovalCoordinator,
    )
    from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
        stats_file_head_ingested_in_db,
    )

    close_old_connections()
    root = str(archive_data_dir or "").strip() or os.path.dirname(
        os.path.normpath(tgz_archive_dir)
    )
    quiet = log_fn or (lambda *a, **k: None)
    coord = DayRawRemovalCoordinator(
        archive_data_dir=root,
        host_name_ext=get_host_name_ext() or "",
        tgz_archive_dir=tgz_archive_dir,
        log_fn=quiet,
        get_quarantine_skip_paths=lambda: set(),
        ingest_ready_fn=stats_file_head_ingested_in_db,
        on_handoff_to_ingest=lambda tar_norm, paths, reason: (
            _handoff_retryable_paths_to_ingest(
                redis_client,
                tar_norm,
                paths,
                tgz_archive_dir=tgz_archive_dir,
                archive_data_dir=root,
                reason=reason,
                log_fn=quiet,
            )
        ),
    )
    # DC-01: pre-seal verify → dedupe → seal → post-seal verify → delete → tar-drop.
    if os.path.isfile(tar_path):
      try:
        coord.run_pre_seal_verify_sync(tar_path)
      except Exception as exc:
        _log(
            "queue_orchestrator day_close pre_seal_verify fail day=%s err=%s"
            % (day_token, type(exc).__name__),
            log_fn=log_fn,
        )
        return "verify_failed"
      try:
        dedupe_tar_keep_largest_file_per_member(
            tar_path,
            log_fn=quiet,
            tgz_archive_dir=tgz_archive_dir,
        )
        progress.record(day_token, "dedupe", 1)
      except Exception as exc:
        _log(
            "queue_orchestrator day_close dedupe fail day=%s err=%s"
            % (day_token, type(exc).__name__),
            log_fn=log_fn,
        )

    seal_dirty_daily_archives(
        tgz_archive_dir,
        local_tz=get_local_timezone(),
        zstd_threads=get_archive_zstd_threads(),
        compress_level=get_archive_zstd_level(),
        keep_uncompressed_tar=get_archive_keep_uncompressed_tar(),
        idle_seconds=0,
        seal_immediately_if_dirty=True,
        only_daily_tar_paths={tar_path},
        only_when_no_remaining_raw=True,
        log_fn=quiet,
    )
    remaining_raw = bool(coord.has_closed_raw_on_disk(tar_path))

    if os.path.isfile(tar_path) or os.path.isfile(tar_path + ".zst"):
      try:
        coord.run_post_seal_verify_sync(tar_path)
      except Exception as exc:
        _log(
            "queue_orchestrator day_close post_seal_verify fail day=%s err=%s"
            % (day_token, type(exc).__name__),
            log_fn=log_fn,
        )

    deleted = int(coord.apply_batch_delete(tar_path) or 0)
    if deleted > 0:
      remaining_raw = bool(coord.has_closed_raw_on_disk(tar_path))
      progress.record(day_token, "raw_delete", 1)
    zst_path = tar_path + ".zst"
    if (
        os.path.isfile(zst_path)
        and os.path.isfile(tar_path)
        and not remaining_raw
    ):
      try:
        os.remove(tar_path)
        tar_dropped = True
        progress.record(day_token, "tar_delete", 1)
        _log(
            "queue_orchestrator day_close tar_drop day=%s" % day_token,
            log_fn=log_fn,
        )
      except OSError as exc:
        _log(
            "queue_orchestrator day_close tar_drop fail day=%s err=%s"
            % (day_token, type(exc).__name__),
            log_fn=log_fn,
        )
  except Exception as exc:
    _log(
        "queue_orchestrator day_close error day=%s err=%s"
        % (day_token, type(exc).__name__),
        log_fn=log_fn,
    )
    return "skipped"
  finally:
    try:
      from django.db import close_old_connections as _close

      _close()
    except Exception:
      pass
  if remaining_raw:
    return "incomplete_raw"
  if jr.day_close_is_complete(
      tar_path,
      calendar_day=cal,
      min_age_hours=min_age_h,
  ):
    return "complete"
  if tar_dropped:
    return "complete"
  return "incomplete_raw"


def _retry_or_dead_letter(
  client: Any,
  *,
  kind: str,
  claim: Any,
  archive_data_dir: str,
  reason: str,
  log_fn: Callable[..., None] | None = None,
) -> str:
  """
  Requeue a failed claim, or dead-letter it once attempts are exhausted.

  Shared by the ingest, append, and day_close drains so every failure path
  accounts for its attempt exactly once and no claim is dropped silently.

  Args:
    client (Any): Redis client.
    kind (str): Job kind.
    claim (Any): :class:`sync_timedb_job_queue.ClaimedJob` for the failure.
    archive_data_dir (str): Archive data root holding the dead-letter sidecar.
    reason (str): Short failure reason recorded on give-up.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    str: ``\"requeued\"`` or ``\"dead_letter\"``.

  Examples:
    >>> _retry_or_dead_letter(
    ...   None, kind="ingest", claim=None, archive_data_dir="/a",
    ...   reason="x",
    ... )
    'requeued'
  """
  if claim is None or client is None:
    _log(
        "queue_orchestrator retry_or_dead_letter missing claim kind=%s reason=%s"
        % (kind, reason),
        log_fn=log_fn,
    )
    return "dropped_no_claim"
  identity = claim.identity
  day_tok = None
  if kind == jq.JOB_KIND_DAY_CLOSE:
    day_tok = progress.day_token_from_day_close_identity(str(identity or ""))
  attempt = jq.bump_job_attempt(client, kind=kind, identity=identity)
  if attempt < jq.job_max_attempts():
    progress.record(day_tok, "attempt_bump", 1)
    jq.requeue_job(
        client,
        kind=kind,
        identity=identity,
        owner_token=claim.owner_token,
        score=getattr(claim, "score", None),
    )
    return "requeued"
  progress.get_progress_state().record_dead_letter(day_tok, kind, 1)
  jq.append_queue_dead_letter(
      archive_data_dir,
      kind=kind,
      identity=identity,
      attempt=attempt,
      reason=reason,
  )
  jq.ack_job(
      client, kind=kind, identity=identity, owner_token=claim.owner_token,
  )
  _log(
      "queue_orchestrator dead_letter kind=%s identity=%s attempt=%d reason=%s"
      % (kind, identity, attempt, reason),
      log_fn=log_fn,
  )
  return "dead_letter"


def _requeue_claimed_job_without_attempt_bump(
  client: Any,
  *,
  claim: Any,
  log_fn: Callable[..., None] | None = None,
) -> str:
  """
  Return a claim to its queue without incrementing the attempt counter.

  Used for cooperative day_close outcomes (``deferred_age``, ``yielded``,
  ``skipped``, ``incomplete_raw``) that are not failures.

  Args:
    client (Any): Redis client.
    claim (Any): :class:`sync_timedb_job_queue.ClaimedJob` to restore.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    str: ``\"requeued\"`` or ``\"dropped_no_claim\"``.

  Examples:
    >>> _requeue_claimed_job_without_attempt_bump(None, claim=None)
    'dropped_no_claim'
  """
  if claim is None or client is None:
    _log(
        "queue_orchestrator requeue_without_bump missing claim",
        log_fn=log_fn,
    )
    return "dropped_no_claim"
  jq.requeue_job(
      client,
      kind=claim.kind,
      identity=claim.identity,
      owner_token=claim.owner_token,
      score=getattr(claim, "score", None),
  )
  return "requeued"


def _count_ingest_band_inflight(claims: dict[str, Any]) -> tuple[int, int]:
  """
  Count locally tracked ingest claims per band.

  Args:
    claims (dict[str, Any]): Identity → claim map.

  Returns:
    tuple[int, int]: ``(hot, catchup)`` counts.

  Examples:
    >>> _count_ingest_band_inflight({})
    (0, 0)
  """
  hot = 0
  catchup = 0
  for claim in claims.values():
    score = getattr(claim, "score", None)
    if score is None or jq.decode_ingest_band(score) == "hot":
      hot += 1
    else:
      catchup += 1
  return (hot, catchup)


def _ingest_watchdog_budget_s(path: str) -> float:
  """
  Return the wall-clock budget after which an ingest worker is presumed dead.

  Args:
    path (str): Absolute closed raw stats path.

  Returns:
    float: Seconds from submission before the slot is reclaimed.

  Examples:
    >>> _ingest_watchdog_budget_s("/missing") >= INGEST_WATCHDOG_GRACE_S
    True
  """
  try:
    budget = float(it.resolve_ingest_per_file_timeout_s(path))
  except Exception:
    budget = 0.0
  if budget <= 0.0:
    budget = 0.0
  return budget + float(INGEST_WATCHDOG_GRACE_S)


def _abandon_timed_out_ingest(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  submitted: dict[str, float],
  archive_data_dir: str,
  now: float | None = None,
  log_fn: Callable[..., None] | None = None,
) -> list[str]:
  """
  Reclaim slots held by ingest workers that blew their per-file budget.

  A pool worker killed by the OOM killer never marks its ``AsyncResult``
  ready, so without this the slot, the Redis lease, and the reported ``busy``
  state persist for the life of the process. Abandoned jobs are requeued with
  ``attempt+1`` (or dead-lettered once attempts are exhausted) and the caller
  must recycle the pool, since a ``Pool`` with a dead worker cannot be trusted.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight ingest map (mutated).
    claims (dict[str, Any]): Claim map (mutated).
    submitted (dict[str, float]): Identity → submit monotonic time (mutated).
    archive_data_dir (str): Archive root for the dead-letter sidecar.
    now (float | None): Monotonic override for tests.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    list[str]: Identities abandoned during this tick.

  Examples:
    >>> _abandon_timed_out_ingest(
    ...   None, inflight={}, claims={}, submitted={}, archive_data_dir="/a",
    ... )
    []
  """
  stamp = time.monotonic() if now is None else float(now)
  abandoned: list[str] = []
  for identity, async_res in list(inflight.items()):
    started = submitted.get(identity)
    if started is None:
      # No submission stamp: adopt it now so the next tick can judge it.
      submitted[identity] = stamp
      continue
    try:
      if async_res.ready():
        continue
    except Exception:
      pass
    path = _path_from_ingest_identity(identity) or identity
    if stamp - started <= _ingest_watchdog_budget_s(path):
      continue
    claim = claims.pop(identity, None)
    inflight.pop(identity, None)
    submitted.pop(identity, None)
    abandoned.append(identity)
    _log(
        "queue_orchestrator ingest watchdog abandon identity=%s held_s=%d"
        % (identity, int(stamp - started)),
        log_fn=log_fn,
    )
    _retry_or_dead_letter(
        client,
        kind=jq.JOB_KIND_INGEST,
        claim=claim,
        archive_data_dir=archive_data_dir,
        reason="ingest_watchdog_timeout",
        log_fn=log_fn,
    )
  return abandoned


def _requeue_pool_collateral(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  submitted: dict[str, float],
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Requeue still-running ingest claims that a pool recycle is about to kill.

  Terminating the pool kills every worker, not just the hung one, so the
  survivors are put back at their original score without burning an attempt —
  they did not fail, the coordinator preempted them.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight ingest map (cleared).
    claims (dict[str, Any]): Claim map (cleared).
    submitted (dict[str, float]): Submit-time map (cleared).
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of claims requeued.

  Examples:
    >>> _requeue_pool_collateral(
    ...   None, inflight={}, claims={}, submitted={},
    ... )
    0
  """
  requeued = 0
  for identity in list(inflight):
    claim = claims.pop(identity, None)
    inflight.pop(identity, None)
    submitted.pop(identity, None)
    if claim is None:
      continue
    try:
      jq.requeue_job(
          client,
          kind=jq.JOB_KIND_INGEST,
          identity=identity,
          owner_token=claim.owner_token,
          score=claim.score,
      )
      requeued += 1
    except Exception as exc:
      _log(
          "queue_orchestrator requeue-on-recycle failed identity=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
  return requeued


def _recycle_ingest_pool(pool: Any, *, factory: Callable[[], Any]) -> Any:
  """
  Terminate a pool with a presumed-dead worker and build a replacement.

  Args:
    pool (Any): Existing spawn pool (may already be broken).
    factory (Callable[[], Any]): Builds the replacement pool.

  Returns:
    Any: The replacement pool.

  Examples:
    >>> _recycle_ingest_pool(None, factory=lambda: "fresh")
    'fresh'
  """
  try:
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
        terminate_pool_bounded,
    )
    if pool is not None:
      terminate_pool_bounded(
          pool,
          join_timeout_s=5.0,
          abandon_after_kill=True,
          context="queue_orchestrator_ingest_recycle",
      )
  except Exception:
    for method in ("terminate", "join"):
      call = getattr(pool, method, None)
      if call is None:
        continue
      try:
        call()
      except Exception:
        pass
  return factory()


def _fill_ingest_band(
  client: Any,
  *,
  band: str,
  cap: int,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  submitted: dict[str, float],
  ingest_pool: Any,
  manager_lock: Any,
  band_cap: int | None = None,
  tgz_archive_dir: str = "",
  archive_data_dir: str | None = None,
  ingest_is_complete_fn: Callable[..., bool] | None = None,
) -> int:
  """
  Claim ranged ingest jobs and submit until ``cap`` in-flight for ``band``.

  Each claim is atomic (ranged pop + lease + in-flight record), so a crash
  between claiming and submitting leaves the job recoverable by the reaper
  rather than lost.

  Args:
    client (Any): Redis client.
    band (str): ``hot`` or ``catchup``.
    cap (int): Max total concurrent ingest jobs.
    inflight (dict[str, AsyncResult]): Identity → async result map.
    claims (dict[str, Any]): Identity → claim map.
    submitted (dict[str, float]): Identity → submit monotonic time (mutated).
    ingest_pool (Any): Spawn ``multiprocessing.Pool``.
    manager_lock (Any): Write lock for ingest workers.
    band_cap (int | None): Optional reserved cap for this band alone.
    tgz_archive_dir (str): Daily archive directory used to reband claims.
    archive_data_dir (str | None): Archive root for ingest-complete marks.
    ingest_is_complete_fn (Callable[..., bool] | None): Injectable complete
      predicate (tests); defaults to
      :func:`sync_timedb_job_reconstruct.ingest_is_complete`.

  Returns:
    int: Newly submitted job count.

  Raises:
    Exception: When ingest-pool submit fails after a claim was taken
      (the claim is requeued first).

  Examples:
    >>> _fill_ingest_band(
    ...   type("C", (), {"evalsha": lambda *a: None, "eval": lambda *a: None,
    ...                  "script_load": lambda s: "x"})(),
    ...   band="hot",
    ...   cap=0,
    ...   inflight={},
    ...   claims={},
    ...   submitted={},
    ...   ingest_pool=None,
    ...   manager_lock=None,
    ... )
    0
  """
  submitted_n = 0
  skipped = 0
  complete_fn = ingest_is_complete_fn or jr.ingest_is_complete
  while len(inflight) < cap:
    if band_cap is not None:
      hot_n, catch_n = _count_ingest_band_inflight(claims)
      used = hot_n if band == "hot" else catch_n
      if used >= band_cap:
        break
    claim = jq.claim_ingest_job(
        client, band=band, owner_token=jq.make_lease_owner_token(),
    )
    if claim is None:
      break
    if _reband_claimed_ingest_if_needed(
        client, claim, tgz_archive_dir,
    ):
      skipped += 1
      if skipped >= INGEST_FILL_SKIP_BUDGET:
        break
      continue
    path = _path_from_ingest_identity(claim.identity)
    if not path or not os.path.isfile(path):
      complete = False
      if path:
        try:
          complete = bool(
              complete_fn(path, archive_data_dir=archive_data_dir),
          )
        except Exception:
          complete = False
      if complete:
        jq.ack_job(
            client,
            kind=jq.JOB_KIND_INGEST,
            identity=claim.identity,
            owner_token=claim.owner_token,
        )
      else:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_INGEST,
            identity=claim.identity,
            owner_token=claim.owner_token,
            score=claim.score,
        )
      skipped += 1
      if skipped >= INGEST_FILL_SKIP_BUDGET:
        break
      continue
    stored_fp = ""
    try:
      stored_fp = jq.read_job_fingerprint(
          client, kind=jq.JOB_KIND_INGEST, identity=claim.identity,
      )
    except Exception:
      stored_fp = ""
    if stored_fp and not jq.fingerprint_matches_path(path, stored_fp):
      try:
        st_now = os.stat(path)
        jq.write_job_fingerprint(
            client,
            kind=jq.JOB_KIND_INGEST,
            identity=claim.identity,
            fingerprint=jq.ingest_fingerprint(
                st_now.st_size, st_now.st_mtime_ns,
            ),
        )
      except OSError:
        pass
      jq.requeue_job(
          client,
          kind=jq.JOB_KIND_INGEST,
          identity=claim.identity,
          owner_token=claim.owner_token,
          score=claim.score,
      )
      skipped += 1
      if skipped >= INGEST_FILL_SKIP_BUDGET:
        break
      continue
    try:
      async_res = ingest_pool.apply_async(_ingest_worker, (manager_lock, path))
    except Exception:
      jq.requeue_job(
          client,
          kind=jq.JOB_KIND_INGEST,
          identity=claim.identity,
          owner_token=claim.owner_token,
          score=claim.score,
      )
      raise
    inflight[claim.identity] = async_res
    claims[claim.identity] = claim
    submitted[claim.identity] = time.monotonic()
    submitted_n += 1
  return submitted_n


def _drain_ingest_ready(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  tgz_archive_dir: str,
  archive_data_dir: str,
  submitted: dict[str, float] | None = None,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished ingest async results; enqueue append on success.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight ingest map (mutated).
    claims (dict[str, Any]): Claim map (mutated).
    tgz_archive_dir (str): Daily archive dir (unused; append uses path).
    archive_data_dir (str): Archive data root for the dead-letter sidecar.
    submitted (dict[str, float] | None): Submit-time map to clear (mutated).
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of completed ingest jobs drained.

  Examples:
    >>> _drain_ingest_ready(
    ...   type("C", (), {"eval": lambda *a: 1, "evalsha": lambda *a: 1,
    ...                  "script_load": lambda s: "x", "rpush": lambda *a: 1})(),
    ...   inflight={},
    ...   claims={},
    ...   tgz_archive_dir="/d",
    ...   archive_data_dir="/a",
    ... )
    0
  """
  done = 0
  for identity, async_res in list(inflight.items()):
    if not async_res.ready():
      continue
    claim = claims.pop(identity, None)
    inflight.pop(identity, None)
    if submitted is not None:
      submitted.pop(identity, None)
    done += 1
    path = _path_from_ingest_identity(identity)
    try:
      result = async_res.get(timeout=0)
    except TimeoutError as exc:
      # Bare TimeoutError / IngestPerFileTimeoutError escape → soft requeue.
      _log(
          "queue_orchestrator ingest timeout identity=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
      day_tok = _day_token_from_date(
          _calendar_day_for_ingest_path(path or identity, tgz_archive_dir),
      )
      progress.record(day_tok, "ingest", 1)
      progress.record(day_tok, "timeout", 1)
      _requeue_claimed_job_without_attempt_bump(
          client, claim=claim, log_fn=log_fn,
      )
      continue
    except Exception as exc:
      _log(
          "queue_orchestrator ingest fail identity=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
      day_tok = _day_token_from_date(
          _calendar_day_for_ingest_path(path or identity, tgz_archive_dir),
      )
      progress.record(day_tok, "ingest", 1)
      progress.record(day_tok, "fail", 1)
      _retry_or_dead_letter(
          client,
          kind=jq.JOB_KIND_INGEST,
          claim=claim,
          archive_data_dir=archive_data_dir,
          reason=type(exc).__name__,
          log_fn=log_fn,
      )
      continue
    ingest_ok = False
    need_archival = False
    outcome = ""
    if isinstance(result, (tuple, list)) and len(result) >= 3:
      need_archival = bool(result[1])
      ingest_ok = bool(result[2])
    if isinstance(result, (tuple, list)) and len(result) >= 5:
      meta = result[4]
      if isinstance(meta, dict):
        outcome = str(meta.get("outcome") or "")
    if not ingest_ok:
      day_tok = _day_token_from_date(
          _calendar_day_for_ingest_path(path or identity, tgz_archive_dir),
      )
      progress.record(day_tok, "ingest", 1)
      if outcome in ("timeout", "lookup_budget"):
        progress.record(day_tok, "timeout", 1)
        _requeue_claimed_job_without_attempt_bump(
            client, claim=claim, log_fn=log_fn,
        )
      else:
        progress.record(day_tok, "fail", 1)
        _retry_or_dead_letter(
            client,
            kind=jq.JOB_KIND_INGEST,
            claim=claim,
            archive_data_dir=archive_data_dir,
            reason=outcome or "ingest_incomplete",
            log_fn=log_fn,
        )
      continue
    try:
      from hpcperfstats.dbload.sync_timedb import (
          _record_ingest_marks_from_worker_result,
      )
      # Worker already logged mark INFO; coordinator persists quietly.
      _record_ingest_marks_from_worker_result(result, log_fn=None)
    except Exception as exc:
      _log(
          "queue_orchestrator ingest mark fail identity=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
      _retry_or_dead_letter(
          client,
          kind=jq.JOB_KIND_INGEST,
          claim=claim,
          archive_data_dir=archive_data_dir,
          reason="ingest_mark_failed",
          log_fn=log_fn,
      )
      continue
    day_tok = _day_token_from_date(
        _calendar_day_for_ingest_path(path or identity, tgz_archive_dir),
    )
    progress.record(day_tok, "ingest", 1)
    if outcome == "db_skip":
      progress.record(day_tok, "db_skip", 1)
    else:
      progress.record(day_tok, "ingested", 1)
    if need_archival and path:
      jq.enqueue_list_job(
          client, kind=jq.JOB_KIND_APPEND, identity=path, dedupe=True,
      )
    if claim is not None:
      jq.ack_job(
          client,
          kind=jq.JOB_KIND_INGEST,
          identity=identity,
          owner_token=claim.owner_token,
      )
  return done


def _fill_append_slots(
  client: Any,
  *,
  cap: int,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  archive_pool: Any,
  tgz_archive_dir: str,
) -> int:
  """
  Claim append LIST jobs and submit grouped ``archive_stats_files`` tasks.

  Missing paths and unresolved daily-tar identities are ``ack_job``-dropped
  (impossible work), bounded by ``APPEND_FILL_SKIP_BUDGET`` per tick. Inflight /
  other-tar collisions still ``requeue`` + ``break`` (correct queue semantics).

  Args:
    client (Any): Redis client.
    cap (int): Max concurrent append jobs.
    inflight (dict[str, AsyncResult]): Path → async result.
    claims (dict[str, Any]): Path → claim map.
    archive_pool (Any): Archive spawn pool.
    tgz_archive_dir (str): Daily archive directory.

  Returns:
    int: Newly submitted append jobs.

  Raises:
    Exception: When archive-pool submit fails after claims were taken
      (those claims are requeued first).

  Examples:
    >>> _fill_append_slots(
    ...   type("C", (), {"lpop": lambda *a: None, "evalsha": lambda *a: False,
    ...                  "script_load": lambda s: "x"})(),
    ...   cap=0,
    ...   inflight={},
    ...   claims={},
    ...   archive_pool=None,
    ...   tgz_archive_dir="/d",
    ... )
    0
  """
  submitted = 0
  skipped = 0
  batch_size = max(1, int(cfg.get_sync_timedb_tar_append_batch_size()))
  while len(inflight) < cap:
    batch_claims: list[Any] = []
    tar_path = ""
    while len(batch_claims) < batch_size:
      if skipped >= APPEND_FILL_SKIP_BUDGET:
        break
      claim = jq.claim_list_job(
          client,
          kind=jq.JOB_KIND_APPEND,
          owner_token=jq.make_lease_owner_token(),
      )
      if claim is None:
        break
      path = claim.identity
      if not path or not os.path.isfile(path):
        jq.ack_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
        skipped += 1
        continue
      this_tar = daily_tar_path_for_stats_path(path, tgz_archive_dir)
      if not this_tar:
        jq.ack_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
        skipped += 1
        continue
      if this_tar in inflight:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
        break
      if not tar_path:
        tar_path = this_tar
      elif this_tar != tar_path:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
        break
      batch_claims.append(claim)
    if skipped >= APPEND_FILL_SKIP_BUDGET and not batch_claims:
      break
    if not batch_claims or not tar_path:
      break
    paths = [job.identity for job in batch_claims]
    try:
      async_res = archive_pool.apply_async(
          _append_worker, ((tar_path, paths),)
      )
    except Exception:
      for job in batch_claims:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=job.identity,
            owner_token=job.owner_token,
        )
      raise
    inflight[tar_path] = async_res
    claims[tar_path] = batch_claims
    submitted += 1
  return submitted


def _drain_append_ready(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  claims: dict[str, Any],
  tgz_archive_dir: str,
  archive_data_dir: str,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished append async results; enqueue day_close when needed.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight append map (mutated).
    claims (dict[str, Any]): Claim map (mutated).
    tgz_archive_dir (str): Daily archive directory for day_close identity.
    archive_data_dir (str): Archive data root for the dead-letter sidecar.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of append jobs drained.

  Examples:
    >>> _drain_append_ready(
    ...   type("C", (), {"eval": lambda *a: 1, "evalsha": lambda *a: 1,
    ...                  "script_load": lambda s: "x", "rpush": lambda *a: 1})(),
    ...   inflight={},
    ...   claims={},
    ...   tgz_archive_dir="/d",
    ...   archive_data_dir="/a",
    ... )
    0
  """
  from hpcperfstats.dbload.sync_timedb import (
      ArchiveAppendOutcome,
      _archive_append_outcome_is_gate_skip,
      _archive_append_outcome_is_soft_requeue,
  )

  done = 0
  for key, async_res in list(inflight.items()):
    if not async_res.ready():
      continue
    claim = claims.pop(key, None)
    inflight.pop(key, None)
    done += 1
    jobs = _iter_claim_jobs(claim)
    failed = False
    result = None
    try:
      result = async_res.get(timeout=0)
    except Exception as exc:
      failed = True
      _log(
          "queue_orchestrator append fail path=%s err=%s"
          % (key, type(exc).__name__),
          log_fn=log_fn,
      )
      for job in jobs:
        _retry_or_dead_letter(
            client,
            kind=jq.JOB_KIND_APPEND,
            claim=job,
            archive_data_dir=archive_data_dir,
            reason=type(exc).__name__,
            log_fn=log_fn,
        )
    if failed:
      continue
    if _archive_append_outcome_is_soft_requeue(result):
      _log(
          "queue_orchestrator append soft_requeue path=%s"
          % key,
          log_fn=log_fn,
      )
      day_tok = _day_token_from_date(calendar_date_from_daily_tar_path(
          key if str(key).endswith(".tar") else daily_tar_path_for_stats_path(
              key, tgz_archive_dir,
          ),
      ))
      progress.record(day_tok, "soft_requeue", 1)
      progress.record(day_tok, "requeue_noprogress", 1)
      for job in jobs:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=job.identity,
            owner_token=job.owner_token,
        )
      continue
    if isinstance(result, ArchiveAppendOutcome) and not result.ok:
      if not _archive_append_outcome_is_gate_skip(result):
        day_tok = _day_token_from_date(calendar_date_from_daily_tar_path(
            key if str(key).endswith(".tar") else daily_tar_path_for_stats_path(
                key, tgz_archive_dir,
            ),
        ))
        progress.record(day_tok, "append_drop", 1)
        for job in jobs:
          _retry_or_dead_letter(
              client,
              kind=jq.JOB_KIND_APPEND,
              claim=job,
              archive_data_dir=archive_data_dir,
              reason="append_failed",
              log_fn=log_fn,
          )
        continue
    elif result is False:
      day_tok = _day_token_from_date(calendar_date_from_daily_tar_path(
          key if str(key).endswith(".tar") else daily_tar_path_for_stats_path(
              key, tgz_archive_dir,
          ),
      ))
      progress.record(day_tok, "append_drop", 1)
      for job in jobs:
        _retry_or_dead_letter(
            client,
            kind=jq.JOB_KIND_APPEND,
            claim=job,
            archive_data_dir=archive_data_dir,
            reason="append_failed",
            log_fn=log_fn,
        )
      continue
    tar = key if str(key).endswith(".tar") else daily_tar_path_for_stats_path(
        key, tgz_archive_dir,
    )
    day_tok = _day_token_from_date(calendar_date_from_daily_tar_path(tar))
    if _archive_append_outcome_is_gate_skip(result):
      skipped = tuple(getattr(result, "skipped_paths", ()) or ())
      progress.record(day_tok, "gate_skip", len(skipped) or 1)
      if skipped and tar:
        _handoff_retryable_paths_to_ingest(
            client,
            tar,
            skipped,
            tgz_archive_dir=tgz_archive_dir,
            archive_data_dir=archive_data_dir,
            reason="gate_skip",
            log_fn=log_fn,
        )
    else:
      progress.record(day_tok, "archive", 1)
    for job in jobs:
      jq.ack_job(
          client,
          kind=jq.JOB_KIND_APPEND,
          identity=job.identity,
          owner_token=job.owner_token,
      )
    if tar:
      # Append coordinator must never run remaining-raw / archive find.
      jr.enqueue_cheap_day_close_if_needed(client, tar)
  return done


def _fill_day_close_slots(
  client: Any,
  *,
  executor: ThreadPoolExecutor,
  inflight: dict[str, Future],
  leases: dict[str, str],
  tgz_archive_dir: str,
  archive_data_dir: str,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Pop day_close LIST jobs into the day_close thread pool.

  Args:
    client (Any): Redis client.
    executor (ThreadPoolExecutor): Day-close thread pool (max inflight 4).
    inflight (dict[str, Future]): Identity → future.
    leases (dict[str, Any]): Identity → claim map (mutated).
    tgz_archive_dir (str): Daily archive directory.
    archive_data_dir (str): Archive data root for day_raw_removal.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Newly submitted day_close jobs.

  Examples:
    >>> from concurrent.futures import ThreadPoolExecutor
    >>> stub = type("C", (), {"script_load": lambda self, s: "sha",
    ...                       "evalsha": lambda self, *a: False})()
    >>> with ThreadPoolExecutor(max_workers=1) as ex:
    ...   _fill_day_close_slots(
    ...     stub,
    ...     executor=ex,
    ...     inflight={},
    ...     leases={},
    ...     tgz_archive_dir="/d",
    ...     archive_data_dir="/a",
    ...   )
    0
  """
  cap = max(1, int(cfg.get_sync_day_close_max_inflight()))
  submitted = 0
  while len(inflight) < cap:
    claim = jq.claim_list_job(
        client,
        kind=jq.JOB_KIND_DAY_CLOSE,
        owner_token=jq.make_lease_owner_token(),
    )
    if claim is None:
      break
    try:
      fut = executor.submit(
          _run_day_close_job,
          claim.identity,
          tgz_archive_dir=tgz_archive_dir,
          archive_data_dir=archive_data_dir,
          log_fn=log_fn,
          redis_client=client,
      )
    except RuntimeError:
      jq.requeue_job(
          client,
          kind=jq.JOB_KIND_DAY_CLOSE,
          identity=claim.identity,
          owner_token=claim.owner_token,
      )
      break
    inflight[claim.identity] = fut
    leases[claim.identity] = claim
    progress.record(
        progress.day_token_from_day_close_identity(claim.identity),
        "dc_run",
        1,
    )
    submitted += 1
  return submitted


def _drain_day_close_ready(
  client: Any,
  *,
  inflight: dict[str, Future],
  leases: dict[str, Any],
  archive_data_dir: str = "",
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished day_close futures; ACK only filesystem+age complete.

  ``deferred_age``, ``incomplete_raw``, ``yielded``, and ``skipped`` requeue
  without an attempt bump. ``verify_failed`` and exceptions retry or
  dead-letter. Fake ``sealed`` after a no-op seal is never an ACK.

  Args:
    client (Any): Redis client.
    inflight (dict[str, Future]): In-flight day_close map (mutated).
    leases (dict[str, Any]): Claim map (mutated).
    archive_data_dir (str): Archive data root for the dead-letter sidecar.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of day_close jobs drained.

  Examples:
    >>> _drain_day_close_ready(
    ...   type("C", (), {"eval": lambda *a: 1, "evalsha": lambda *a: 1,
    ...                  "script_load": lambda s: "x",
    ...                  "rpush": lambda *a: 1})(),
    ...   inflight={},
    ...   leases={},
    ... )
    0
  """
  done = 0
  for identity, fut in list(inflight.items()):
    if not fut.done():
      continue
    claim = leases.pop(identity, None)
    inflight.pop(identity, None)
    done += 1
    outcome = "skipped"
    failure = ""
    try:
      outcome = str(fut.result())
    except Exception as exc:
      failure = "%s: %s" % (type(exc).__name__, exc)
      _log(
          "queue_orchestrator day_close fail id=%s err=%s"
          % (identity, failure),
          log_fn=log_fn,
      )
    if failure or outcome == "verify_failed":
      progress.record(
          progress.day_token_from_day_close_identity(identity),
          "verify_failed",
          1,
      )
      _retry_or_dead_letter(
          client,
          kind=jq.JOB_KIND_DAY_CLOSE,
          claim=claim,
          archive_data_dir=archive_data_dir,
          reason=failure or outcome,
          log_fn=log_fn,
      )
      continue
    day_tok = progress.day_token_from_day_close_identity(identity)
    if outcome != "complete":
      if outcome == "deferred_age":
        progress.record(day_tok, "deferred_age", 1)
      elif outcome == "yielded":
        progress.record(day_tok, "yielded", 1)
      elif (
          outcome == "incomplete_raw"
          or outcome not in _TRANSIENT_DAY_CLOSE_OUTCOMES
      ):
        progress.record(day_tok, "incomplete_raw", 1)
      _requeue_claimed_job_without_attempt_bump(
          client, claim=claim, log_fn=log_fn,
      )
      continue
    if claim is not None:
      progress.record(day_tok, "complete", 1)
      jq.ack_job(
          client,
          kind=jq.JOB_KIND_DAY_CLOSE,
          identity=identity,
          owner_token=claim.owner_token,
      )
  return done


def _renew_active_claims(
  client: Any,
  claim_maps: Iterable[tuple[str, dict[str, Any]]],
  *,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Compare-and-extend every lease this coordinator still owns.

  **OQ-1 forbidden in the production loop.** Leases use per-file EX (default
  86400s) with no heartbeat renew. This helper remains only for tests or an
  explicit operator abandon path — ``run_sync_timedb_queue_orchestrator``
  must not call it on each tick.

  Args:
    client (Any): Redis client.
    claim_maps (Iterable[tuple[str, dict[str, Any]]]): ``(kind, claims)`` pairs.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of leases that could not be renewed (lost ownership).

  Examples:
    >>> _renew_active_claims(None, [])
    0
  """
  lost = 0
  for kind, claims in claim_maps:
    for _map_key, claim in list(claims.items()):
      for job in _iter_claim_jobs(claim):
        try:
          ok = jq.renew_job_lease(
              client,
              kind=kind,
              identity=job.identity,
              owner_token=job.owner_token,
          )
        except Exception as exc:
          _log(
              "queue_orchestrator lease renew error kind=%s identity=%s err=%s"
              % (kind, job.identity, type(exc).__name__),
              log_fn=log_fn,
          )
          continue
        if not ok:
          lost += 1
          _log(
              "queue_orchestrator lease lost kind=%s identity=%s"
              % (kind, job.identity),
              log_fn=log_fn,
          )
  return lost


def _release_claims_on_shutdown(
  client: Any,
  claim_maps: Iterable[tuple[str, dict[str, Any]]],
  *,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Requeue every still-held claim when the bounded drain times out.

  Without this, work owned by a coordinator that is exiting would wait a full
  lease TTL plus reap grace before another coordinator could pick it up.

  Args:
    client (Any): Redis client.
    claim_maps (Iterable[tuple[str, dict[str, Any]]]): ``(kind, claims)`` pairs.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of claims returned to their queues.

  Examples:
    >>> _release_claims_on_shutdown(None, [])
    0
  """
  released = 0
  for kind, claims in claim_maps:
    for identity, claim in list(claims.items()):
      for job in _iter_claim_jobs(claim):
        try:
          if jq.requeue_job(
              client,
              kind=kind,
              identity=job.identity,
              owner_token=job.owner_token,
              score=getattr(job, "score", None),
          ):
            released += 1
        except Exception as exc:
          _log(
              "queue_orchestrator shutdown requeue error kind=%s identity=%s "
              "err=%s" % (kind, job.identity, type(exc).__name__),
              log_fn=log_fn,
          )
      claims.pop(identity, None)
  return released


def _protect_local_inflight_deadlines(
  client: Any,
  *,
  kind: str,
  identities: Iterable[str],
  extend_s: float = 600.0,
) -> int:
  """
  Push in-flight deadlines forward for local identities before a Redis reap.

  Kind-scoped coordinators must not let ``reap_expired_inflight`` steal
  identities still held in local claim maps. Extending the inflight HASH
  deadline (not a lease heartbeat renew loop) keeps those members above the
  reap cutoff for this tick.

  Args:
    client (Any): Redis client with ``hget`` / ``hset``.
    kind (str): Job kind whose inflight HASH is updated.
    identities (Iterable[str]): Local identities to protect.
    extend_s (float): Seconds added to ``time.time()`` for the new deadline.

  Returns:
    int: Number of HASH fields rewritten.

  Examples:
    >>> _protect_local_inflight_deadlines(None, kind="ingest", identities=())
    0
  """
  if client is None:
    return 0
  idents = [str(x) for x in identities if str(x)]
  if not idents:
    return 0
  key = jq.job_inflight_key(kind)
  deadline = "%.3f" % (time.time() + float(extend_s))
  protected = 0
  for ident in idents:
    try:
      raw = client.hget(key, ident)
    except Exception:
      continue
    if raw is None:
      continue
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    parts = text.split("|", 2)
    owner = parts[1] if len(parts) > 1 else ""
    score = parts[2] if len(parts) > 2 else ""
    try:
      client.hset(key, ident, "%s|%s|%s" % (deadline, owner, score))
      protected += 1
    except Exception:
      continue
  return protected


def _reap_stale_inflight(
  client: Any,
  *,
  kinds: Iterable[str] | None = None,
  skip_identities: Iterable[str] | None = None,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Recover in-flight jobs abandoned by a dead or hung owner.

  Kind-scoped: each subsystem coordinator reaps only its own kinds and
  protects identities still held in its local claim/inflight maps so a
  cross-thread reaper cannot steal live work.

  Args:
    client (Any): Redis client.
    kinds (Iterable[str] | None): Kinds to reap; default all job kinds
      (boot path only).
    skip_identities (Iterable[str] | None): Local identities to leave
      alone even if Redis reports them expired.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of identities returned to their queues.

  Examples:
    >>> _reap_stale_inflight(None)
    0
  """
  if client is None:
    return 0
  skip = frozenset(str(x) for x in (skip_identities or ()))
  total = 0
  for kind in (kinds if kinds is not None else jq.JOB_KINDS_ALL):
    if skip:
      _protect_local_inflight_deadlines(
          client, kind=str(kind), identities=skip,
      )
    try:
      recovered = jq.reap_expired_inflight(client, kind=kind)
    except Exception as exc:
      _log(
          "queue_orchestrator reap error kind=%s err=%s"
          % (kind, type(exc).__name__),
          log_fn=log_fn,
      )
      continue
    if skip and recovered:
      recovered = [ident for ident in recovered if str(ident) not in skip]
    if recovered:
      total += len(recovered)
      _log(
          "queue_orchestrator reaped kind=%s count=%d first=%s"
          % (kind, len(recovered), recovered[0]),
          log_fn=log_fn,
      )
  return total


def _queues_appear_idle(client: Any) -> bool:
  """
  True when every durable job structure reports zero queued and in-flight.

  Discover and in-flight counts are included: a coordinator that only checks
  ingest/append/day_close queue depth can declare idle while a discover job
  or another worker's claim is still outstanding.

  Empty queues still do **not** mean caught up (reconstruct law).

  Args:
    client (Any): Redis client with ``zcard`` / ``llen`` / ``hlen``.

  Returns:
    bool: True when all job structures report zero length.

  Examples:
    >>> class _C:
    ...   def zcard(self, k):
    ...     return 0
    ...   def llen(self, k):
    ...     return 0
    ...   def hlen(self, k):
    ...     return 0
    >>> _queues_appear_idle(_C())
    True
  """
  try:
    census = jq.queue_census(client)
  except Exception:
    return False
  if not census:
    return False
  for entry in census.values():
    if entry.get("queued") or entry.get("inflight"):
      return False
  return True


def _ingest_coordinator_loop(
  *,
  client: Any,
  directory: str,
  tgz_archive_dir: str,
  manager_lock: Any,
  pool_ref: AtomicPoolRef,
  recycle_gate: IngestRecycleGate,
  barrier: SubsystemShutdownBarrier,
  hot_cap: int,
  catchup_cap: int,
  ingest_pool_size: int,
  poll_s: float,
  ingest_inflight: dict[str, AsyncResult],
  ingest_leases: dict[str, Any],
  ingest_submitted: dict[str, float],
  busy_flags: dict[str, bool],
  busy_lock: threading.Lock,
  log_fn: Callable[..., None] | None,
) -> None:
  """
  Own ingest fill/drain/abandon and kind-scoped ingest reap.

  Requests pool recycle via ``IngestRecycleGate``; never terminates the
  pool on this thread. Marks ``busy_flags["ingest"]`` and ``drained`` on
  shutdown.

  Args:
    client (Any): Redis job client.
    directory (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    manager_lock (Any): Write lock passed to ingest workers.
    pool_ref (AtomicPoolRef): Current ingest pool published by MainThread.
    recycle_gate (IngestRecycleGate): Pause protocol with MainThread.
    barrier (SubsystemShutdownBarrier): Shared draining/drained Events.
    hot_cap (int): Reserved hot ingest slots.
    catchup_cap (int): Reserved catchup ingest slots.
    ingest_pool_size (int): Total ingest pool process count.
    poll_s (float): Idle poll seconds.
    ingest_inflight (dict[str, AsyncResult]): Local ingest AsyncResult map.
    ingest_leases (dict[str, Any]): Local ingest claim map.
    ingest_submitted (dict[str, float]): Submit monotonic times.
    busy_flags (dict[str, bool]): Shared busy flags (mutated under lock).
    busy_lock (threading.Lock): Guards ``busy_flags``.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    None

  Raises:
    RuntimeError: When Redis ``zcount`` fails (fail-closed mid-tick).
    Exception: Re-raises unexpected coordinator crashes after logging.

  Examples:
    >>> callable(_ingest_coordinator_loop)
    True
  """
  set_daemon_thread_title(
      "ingest-coordinator",
      script_name="sync_timedb.py",
      role="ingest-coordinator",
  )
  role = "ingest-coordinator"
  last_reap = time.monotonic()
  try:
    while True:
      draining = barrier.draining.is_set() or shutdown_requested()
      if draining:
        barrier.draining.set()
      # Pause protocol: stop apply_async until MainThread recycles; keep
      # draining ready results so slots free while the pool is replaced.
      if recycle_gate.recycle_requested.is_set():
        recycle_gate.paused.set()
        while recycle_gate.recycle_requested.is_set() and not (
            barrier.draining.is_set() or shutdown_requested()
        ):
          _drain_ingest_ready(
              client,
              inflight=ingest_inflight,
              claims=ingest_leases,
              submitted=ingest_submitted,
              tgz_archive_dir=tgz_archive_dir,
              archive_data_dir=directory,
              log_fn=log_fn,
          )
          time.sleep(min(0.05, max(0.01, poll_s)))
        recycle_gate.paused.clear()
      did = 0
      if not draining and not recycle_gate.recycle_requested.is_set():
        pool = pool_ref.get()
        while len(ingest_inflight) < hot_cap:
          n = _fill_ingest_band(
              client,
              band="hot",
              cap=hot_cap,
              inflight=ingest_inflight,
              claims=ingest_leases,
              submitted=ingest_submitted,
              ingest_pool=pool,
              manager_lock=manager_lock,
              band_cap=hot_cap,
              tgz_archive_dir=tgz_archive_dir,
              archive_data_dir=directory,
          )
          did += n
          if n == 0:
            break
        try:
          lo, hi = jq.ingest_score_range("hot")
          hot_queued = int(
              client.zcount(jq.job_queue_key(jq.JOB_KIND_INGEST), lo, hi) or 0
          )
        except Exception as exc:
          raise RuntimeError(
              "queue orchestrator redis zcount failed: %s" % type(exc).__name__
          ) from exc
        catchup_limit = catchup_dispatch_cap(
            hot_queued=hot_queued,
            catchup_queued=0,
            hot_cap=hot_cap,
            catchup_cap=catchup_cap,
            pool=ingest_pool_size,
        )
        while len(ingest_inflight) < ingest_pool_size:
          n = _fill_ingest_band(
              client,
              band="catchup",
              cap=ingest_pool_size,
              inflight=ingest_inflight,
              claims=ingest_leases,
              submitted=ingest_submitted,
              ingest_pool=pool_ref.get(),
              manager_lock=manager_lock,
              band_cap=catchup_limit,
              tgz_archive_dir=tgz_archive_dir,
              archive_data_dir=directory,
          )
          did += n
          if n == 0:
            break
        while len(ingest_inflight) < ingest_pool_size:
          n = _fill_ingest_band(
              client,
              band="hot",
              cap=ingest_pool_size,
              inflight=ingest_inflight,
              claims=ingest_leases,
              submitted=ingest_submitted,
              ingest_pool=pool_ref.get(),
              manager_lock=manager_lock,
              tgz_archive_dir=tgz_archive_dir,
              archive_data_dir=directory,
          )
          did += n
          if n == 0:
            break
      did += _drain_ingest_ready(
          client,
          inflight=ingest_inflight,
          claims=ingest_leases,
          submitted=ingest_submitted,
          tgz_archive_dir=tgz_archive_dir,
          archive_data_dir=directory,
          log_fn=log_fn,
      )
      abandoned = _abandon_timed_out_ingest(
          client,
          inflight=ingest_inflight,
          claims=ingest_leases,
          submitted=ingest_submitted,
          archive_data_dir=directory,
          log_fn=log_fn,
      )
      if abandoned:
        _requeue_pool_collateral(
            client,
            inflight=ingest_inflight,
            claims=ingest_leases,
            submitted=ingest_submitted,
            log_fn=log_fn,
        )
        recycle_gate.recycle_requested.set()
        recycle_gate.paused.set()
        _log(
            "queue_orchestrator ingest recycle requested abandoned=%d"
            % len(abandoned),
            log_fn=log_fn,
        )
      now = time.monotonic()
      if now - last_reap >= max(poll_s, 5.0):
        last_reap = now
        _reap_stale_inflight(
            client,
            kinds=(jq.JOB_KIND_INGEST,),
            skip_identities=tuple(ingest_inflight) + tuple(ingest_leases),
            log_fn=log_fn,
        )
      with busy_lock:
        busy_flags["ingest"] = bool(ingest_inflight)
      if draining and not ingest_inflight:
        barrier.mark_drained(role)
        return
      if draining:
        time.sleep(min(0.25, max(0.05, poll_s)))
      elif did == 0 and not ingest_inflight:
        time.sleep(max(0.05, poll_s))
      else:
        time.sleep(min(0.05, poll_s))
  except Exception as exc:
    _log(
        "queue_orchestrator ingest-coordinator crash err=%s"
        % type(exc).__name__,
        log_fn=log_fn,
    )
    raise
  finally:
    barrier.mark_drained(role)


def _append_coordinator_loop(
  *,
  client: Any,
  directory: str,
  tgz_archive_dir: str,
  archive_pool: Any,
  append_cap: int,
  poll_s: float,
  barrier: SubsystemShutdownBarrier,
  append_inflight: dict[str, AsyncResult],
  append_leases: dict[str, Any],
  busy_flags: dict[str, bool],
  busy_lock: threading.Lock,
  log_fn: Callable[..., None] | None,
) -> None:
  """
  Own append fill/drain and tar-deduped cheap day_close enqueue.

  Never runs remaining-raw / archive-wide find on this thread.

  Args:
    client (Any): Redis job client.
    directory (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    archive_pool (Any): Spawn pool for append workers.
    append_cap (int): Max concurrent append batches.
    poll_s (float): Idle poll seconds.
    barrier (SubsystemShutdownBarrier): Shared draining/drained Events.
    append_inflight (dict[str, AsyncResult]): Local append AsyncResult map.
    append_leases (dict[str, Any]): Local append claim map.
    busy_flags (dict[str, bool]): Shared busy flags (mutated under lock).
    busy_lock (threading.Lock): Guards ``busy_flags``.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    None

  Examples:
    >>> callable(_append_coordinator_loop)
    True
  """
  set_daemon_thread_title(
      "append-coordinator",
      script_name="sync_timedb.py",
      role="append-coordinator",
  )
  role = "append-coordinator"
  last_reap = time.monotonic()
  try:
    while True:
      draining = barrier.draining.is_set() or shutdown_requested()
      if draining:
        barrier.draining.set()
      did = 0
      if not draining:
        did += _fill_append_slots(
            client,
            cap=append_cap,
            inflight=append_inflight,
            claims=append_leases,
            archive_pool=archive_pool,
            tgz_archive_dir=tgz_archive_dir,
        )
      did += _drain_append_ready(
          client,
          inflight=append_inflight,
          claims=append_leases,
          tgz_archive_dir=tgz_archive_dir,
          archive_data_dir=directory,
          log_fn=log_fn,
      )
      now = time.monotonic()
      if now - last_reap >= max(poll_s, 5.0):
        last_reap = now
        _reap_stale_inflight(
            client,
            kinds=(jq.JOB_KIND_APPEND,),
            skip_identities=tuple(append_inflight) + tuple(append_leases),
            log_fn=log_fn,
        )
      with busy_lock:
        busy_flags["append"] = bool(append_inflight)
      if draining and not append_inflight:
        barrier.mark_drained(role)
        return
      if draining:
        time.sleep(min(0.25, max(0.05, poll_s)))
      elif did == 0 and not append_inflight:
        time.sleep(max(0.05, poll_s))
      else:
        time.sleep(min(0.05, poll_s))
  finally:
    barrier.mark_drained(role)


def _day_close_coordinator_loop(
  *,
  client: Any,
  directory: str,
  tgz_archive_dir: str,
  day_executor: ThreadPoolExecutor,
  poll_s: float,
  barrier: SubsystemShutdownBarrier,
  day_inflight: dict[str, Future],
  day_leases: dict[str, Any],
  busy_flags: dict[str, bool],
  busy_lock: threading.Lock,
  log_fn: Callable[..., None] | None,
) -> None:
  """
  Own day_close LIST fill/drain into the day_close worker executor.

  Full filesystem remaining-raw probes run only on day_close **workers**.

  Args:
    client (Any): Redis job client.
    directory (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    day_executor (ThreadPoolExecutor): Worker pool for ``_run_day_close_job``.
    poll_s (float): Idle poll seconds.
    barrier (SubsystemShutdownBarrier): Shared draining/drained Events.
    day_inflight (dict[str, Future]): Local day_close Future map.
    day_leases (dict[str, Any]): Local day_close claim map.
    busy_flags (dict[str, bool]): Shared busy flags (mutated under lock).
    busy_lock (threading.Lock): Guards ``busy_flags``.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    None

  Examples:
    >>> callable(_day_close_coordinator_loop)
    True
  """
  set_daemon_thread_title(
      "day-close-coordinator",
      script_name="sync_timedb.py",
      role="day-close-coordinator",
  )
  role = "day-close-coordinator"
  last_reap = time.monotonic()
  try:
    while True:
      draining = barrier.draining.is_set() or shutdown_requested()
      if draining:
        barrier.draining.set()
      did = 0
      if not draining:
        did += _fill_day_close_slots(
            client,
            executor=day_executor,
            inflight=day_inflight,
            leases=day_leases,
            tgz_archive_dir=tgz_archive_dir,
            archive_data_dir=directory,
            log_fn=log_fn,
        )
      did += _drain_day_close_ready(
          client,
          inflight=day_inflight,
          leases=day_leases,
          archive_data_dir=directory,
          log_fn=log_fn,
      )
      now = time.monotonic()
      if now - last_reap >= max(poll_s, 5.0):
        last_reap = now
        _reap_stale_inflight(
            client,
            kinds=(jq.JOB_KIND_DAY_CLOSE,),
            skip_identities=tuple(day_inflight) + tuple(day_leases),
            log_fn=log_fn,
        )
      with busy_lock:
        busy_flags["day_close"] = bool(day_inflight)
      if draining and not day_inflight:
        barrier.mark_drained(role)
        return
      if draining:
        if day_inflight:
          wait(
              list(day_inflight.values()),
              timeout=min(poll_s, 0.5),
              return_when=FIRST_COMPLETED,
          )
        else:
          time.sleep(min(0.25, max(0.05, poll_s)))
      elif did == 0 and not day_inflight:
        time.sleep(max(0.05, poll_s))
      else:
        if day_inflight:
          wait(
              list(day_inflight.values()),
              timeout=min(poll_s, 0.5),
              return_when=FIRST_COMPLETED,
          )
        else:
          time.sleep(min(0.05, poll_s))
  finally:
    barrier.mark_drained(role)


def _reconstruct_coordinator_loop(
  *,
  client: Any,
  directory: str,
  tgz_archive_dir: str,
  poll_s: float,
  barrier: SubsystemShutdownBarrier,
  rescan_mtime_days: int,
  startdate: Any,
  enddate: Any,
  run_once: bool,
  run_once_exit: threading.Event,
  busy_flags: dict[str, bool],
  busy_lock: threading.Lock,
  log_fn: Callable[..., None] | None,
) -> None:
  """
  Own idle reconstruct, census logs, and run_once idle exit signaling.

  Does not submit discover while discover-bg is busy. Reaps orphan discover
  inflight leases on an interval (kind-scoped ``JOB_KIND_DISCOVER`` only).

  Args:
    client (Any): Redis job client.
    directory (str): Archive data directory.
    tgz_archive_dir (str): Daily archive directory.
    poll_s (float): Idle poll seconds.
    barrier (SubsystemShutdownBarrier): Shared draining/drained Events.
    rescan_mtime_days (int): Incremental find window for idle reconstruct.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.
    run_once (bool): When True, signal ``run_once_exit`` after idle reconstruct.
    run_once_exit (threading.Event): Set when run_once should exit.
    busy_flags (dict[str, bool]): Shared busy flags (read under lock).
    busy_lock (threading.Lock): Guards ``busy_flags``.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    None

  Examples:
    >>> callable(_reconstruct_coordinator_loop)
    True
  """
  set_daemon_thread_title(
      "reconstruct-coordinator",
      script_name="sync_timedb.py",
      role="reconstruct-coordinator",
  )
  role = "reconstruct-coordinator"
  last_census = 0.0
  last_reap = time.monotonic()
  idle_rounds = 0
  try:
    while True:
      draining = barrier.draining.is_set() or shutdown_requested()
      if draining:
        barrier.draining.set()
        barrier.mark_drained(role)
        return
      _idle_reconstruct_pass(
          client,
          directory,
          tgz_archive_dir=tgz_archive_dir,
          log_fn=log_fn,
          mtime_days=rescan_mtime_days,
          startdate=startdate,
          enddate=enddate,
      )
      now = time.monotonic()
      if now - last_census >= CENSUS_LOG_INTERVAL_S:
        last_census = now
        try:
          census = jq.queue_census(client)
        except Exception:
          census = {}
        with busy_lock:
          busy_kinds = [k for k, v in busy_flags.items() if v]
        if _discover_bg_is_busy() and "discover" not in busy_kinds:
          busy_kinds = list(busy_kinds) + ["discover"]
        busy_tok = progress.format_busy_token(busy_kinds)
        if census:
          _log(
              "queue_orchestrator census %s%s"
              % (
                  jq.format_queue_census(census),
                  (" " + busy_tok) if busy_tok else "",
              ),
              log_fn=log_fn,
          )
      _emit_progress_report_if_due(
          client,
          busy_flags=busy_flags,
          busy_lock=busy_lock,
          log_fn=log_fn,
      )
      with busy_lock:
        busy = any(busy_flags.values())
      if run_once and not busy and _queues_appear_idle(client):
        idle_rounds += 1
        if idle_rounds >= 1:
          recon = _idle_reconstruct_pass(
              client,
              directory,
              tgz_archive_dir=tgz_archive_dir,
              log_fn=log_fn,
              force=True,
              mtime_days=None,
              startdate=startdate,
              enddate=enddate,
          )
          with busy_lock:
            busy = any(busy_flags.values())
          if recon == 0 and not busy and _queues_appear_idle(client):
            _log("queue_orchestrator run_once idle exit", log_fn=log_fn)
            run_once_exit.set()
            barrier.mark_drained(role)
            return
          idle_rounds = 0
      else:
        idle_rounds = 0
      now = time.monotonic()
      if now - last_reap >= max(poll_s, 5.0):
        last_reap = now
        _reap_stale_inflight(
            client,
            kinds=(jq.JOB_KIND_DISCOVER,),
            log_fn=log_fn,
        )
      time.sleep(max(0.05, poll_s))
  finally:
    barrier.mark_drained(role)

def run_sync_timedb_queue_orchestrator(
  archive_dir: str,
  startdate: Any,
  enddate: Any,
  host_name_ext: str,
  manager_lock: Any,
  archive_pool: Any,
  *,
  run_once: bool = False,
  log_fn: Callable[..., None] | None = None,
) -> None:
  """
  Run the greenfield ``job:v1`` orchestrator for one ``archive_dir``.

  Acquires an exclusive flock, ensures the persistence contract, boots
  streaming discover, then loops: fill/drain ingest (hot/catchup caps),
  append (archive pool), and day_close (thread pool, max inflight 4). Idle
  poll uses ``sync_pool_poll_timeout_s``. Never calls
  ``run_sync_timedb_supervisor_loop`` or starts ``ArchiveJanitor``.

  Args:
    archive_dir (str): Archive data directory root.
    startdate (Any): CLI date mode (``backlog`` / ``current`` / date); retained
      for entry parity (date filtering remains find/discover responsibility).
    enddate (Any): CLI end date (retained for entry parity).
    host_name_ext (str): Host directory suffix (validated by caller).
    manager_lock (Any): Manager write lock or shard list.
    archive_pool (Any): Spawn pool for append workers.
    run_once (bool): When True, exit after one idle pass with empty queues and
      no in-flight work (pipeline / once mode).
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    None

  Raises:
    ValueError: When ``archive_dir`` is empty.
    OSError: When the exclusive ``archive_dir`` flock cannot be acquired.
    RuntimeError: When Redis job client cannot be obtained (fail-closed).

  Examples:
    >>> run_sync_timedb_queue_orchestrator(  # doctest: +SKIP
    ...   "/data", "backlog", None, ".ext", None, None, run_once=True
    ... )
  """
  del host_name_ext  # entry parity; discover uses find root + CLI dates
  directory = os.path.normpath(str(archive_dir or ""))
  if not directory:
    raise ValueError("archive_dir is required")

  with exclusive_archive_dir_flock(directory, blocking=True):
    ensure_persistence_contract(directory, log_fn=log_fn, allow_reset=False)
    try:
      client = get_archive_members_redis_client(required=True)
    except Exception as exc:
      raise RuntimeError(
          "queue orchestrator requires Redis (fail-closed): %s"
          % type(exc).__name__
      ) from exc

    tgz_archive_dir = cfg.get_daily_archive_dir_path()
    install_cooperative_shutdown_handlers(log_fn=log_fn)
    assert_redis_queue_safety(client)
    try:
      stolen = jq.steal_dead_owner_leases(client)
    except Exception as exc:
      raise RuntimeError(
          "queue orchestrator steal_dead_owner_leases failed: %s"
          % type(exc).__name__
      ) from exc
    if stolen:
      _log(
          "queue_orchestrator stole dead-owner leases count=%d" % stolen,
          log_fn=log_fn,
      )
    _reap_stale_inflight(client, log_fn=log_fn)

    ingest_pool_size = max(1, int(cfg.get_sync_ingest_pool_processes()))
    hot_cap, catchup_cap = jq.ingest_band_slot_caps(ingest_pool_size)
    append_cap = max(1, int(cfg.get_sync_archive_pool_processes()))
    poll_s = float(cfg.get_sync_pool_poll_timeout_s())
    day_close_workers = max(1, int(cfg.get_sync_day_close_max_inflight()))

    ingest_inflight: dict[str, AsyncResult] = {}
    ingest_leases: dict[str, Any] = {}
    ingest_submitted: dict[str, float] = {}
    append_inflight: dict[str, AsyncResult] = {}
    append_leases: dict[str, Any] = {}
    day_inflight: dict[str, Future] = {}
    day_leases: dict[str, Any] = {}
    claim_maps = (
        (jq.JOB_KIND_INGEST, ingest_leases),
        (jq.JOB_KIND_APPEND, append_leases),
        (jq.JOB_KIND_DAY_CLOSE, day_leases),
    )
    last_census_mono = 0.0
    last_reap_mono = time.monotonic()
    drain_deadline = 0.0
    try:
      rescan_mtime_days = int(cfg.get_sync_ingest_rescan_mtime_days())
    except Exception:
      rescan_mtime_days = 1

    def _new_ingest_pool() -> Any:
      """
      Create a spawn ingest pool with worker process titles.

      Returns:
        Any: New ``multiprocessing.Pool`` for ingest workers.

      Examples:
        >>> callable(_new_ingest_pool)
        True
      """
      return create_sync_timedb_spawn_pool(
          processes=ingest_pool_size,
          initializer=apply_pool_worker_process_title,
          initargs=("sync_timedb.py", "ingest-pool"),
          pool_kind_log_label="ingest-pool",
      )

    # Start ingest + populate before boot discover so classify never inlines
    # sealed populate on MainThread (populate controller None → execute_…).
    # Not a `with` block: a watchdog abandonment recycles the pool, and the
    # `with` statement would only ever close the pool it first entered.
    ingest_pool = _new_ingest_pool()
    populate = PopulatePoolController()
    set_populate_pool_controller(populate)
    try:
      populate.start(script_name="sync_timedb.py", registry=None)
    except Exception as exc:
      _log(
          "queue_orchestrator populate-pool start err=%s"
          % type(exc).__name__,
          log_fn=log_fn,
      )
    # Boot discover off MainThread (same executor as idle reconstruct).
    try:
      jq.enqueue_list_job(
          client,
          kind=jq.JOB_KIND_DISCOVER,
          identity=discover_job_identity(directory, None),
          dedupe=True,
      )
    except Exception as exc:
      _log(
          "queue_orchestrator boot discover enqueue err=%s"
          % type(exc).__name__,
          log_fn=log_fn,
      )
    _submit_background_discover(
        client,
        directory,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
        mtime_days=None,
        startdate=startdate,
        enddate=enddate,
    )
    # Log only after submit returns — a pre-submit line lied when MainThread
    # deadlocked inside nested ``_discover_bg_lock`` acquire (hpcperfstats03).
    _log("queue_orchestrator boot discover submitted", log_fn=log_fn)
    pool_ref = AtomicPoolRef(ingest_pool)
    recycle_gate = IngestRecycleGate()
    barrier = SubsystemShutdownBarrier(
        (
            "ingest-coordinator",
            "append-coordinator",
            "day-close-coordinator",
            "reconstruct-coordinator",
        )
    )
    busy_flags: dict[str, bool] = {
        "ingest": False,
        "append": False,
        "day_close": False,
    }
    busy_lock = threading.Lock()
    run_once_exit = threading.Event()
    day_executor = ThreadPoolExecutor(
        max_workers=day_close_workers,
        thread_name_prefix=DAY_CLOSE_THREAD_NAME_PREFIX,
    )
    threads: list[tuple[str, threading.Thread]] = []

    def _spawn(name: str, target: Callable[..., None], kwargs: dict) -> None:
      """
      Start one titled subsystem coordinator thread.

      Args:
        name (str): Thread / role name.
        target (Callable[..., None]): Coordinator loop entry.
        kwargs (dict): Keyword args for ``target``.

      Returns:
        None

      Examples:
        >>> callable(_spawn)
        True
      """
      thr = threading.Thread(
          target=target,
          name=name,
          kwargs=kwargs,
          daemon=True,
      )
      threads.append((name, thr))
      thr.start()

    _spawn(
        "ingest-coordinator",
        _ingest_coordinator_loop,
        {
            "client": client,
            "directory": directory,
            "tgz_archive_dir": tgz_archive_dir,
            "manager_lock": manager_lock,
            "pool_ref": pool_ref,
            "recycle_gate": recycle_gate,
            "barrier": barrier,
            "hot_cap": hot_cap,
            "catchup_cap": catchup_cap,
            "ingest_pool_size": ingest_pool_size,
            "poll_s": poll_s,
            "ingest_inflight": ingest_inflight,
            "ingest_leases": ingest_leases,
            "ingest_submitted": ingest_submitted,
            "busy_flags": busy_flags,
            "busy_lock": busy_lock,
            "log_fn": log_fn,
        },
    )
    _spawn(
        "append-coordinator",
        _append_coordinator_loop,
        {
            "client": client,
            "directory": directory,
            "tgz_archive_dir": tgz_archive_dir,
            "archive_pool": archive_pool,
            "append_cap": append_cap,
            "poll_s": poll_s,
            "barrier": barrier,
            "append_inflight": append_inflight,
            "append_leases": append_leases,
            "busy_flags": busy_flags,
            "busy_lock": busy_lock,
            "log_fn": log_fn,
        },
    )
    _spawn(
        "day-close-coordinator",
        _day_close_coordinator_loop,
        {
            "client": client,
            "directory": directory,
            "tgz_archive_dir": tgz_archive_dir,
            "day_executor": day_executor,
            "poll_s": poll_s,
            "barrier": barrier,
            "day_inflight": day_inflight,
            "day_leases": day_leases,
            "busy_flags": busy_flags,
            "busy_lock": busy_lock,
            "log_fn": log_fn,
        },
    )
    _spawn(
        "reconstruct-coordinator",
        _reconstruct_coordinator_loop,
        {
            "client": client,
            "directory": directory,
            "tgz_archive_dir": tgz_archive_dir,
            "poll_s": poll_s,
            "barrier": barrier,
            "rescan_mtime_days": rescan_mtime_days,
            "startdate": startdate,
            "enddate": enddate,
            "run_once": run_once,
            "run_once_exit": run_once_exit,
            "busy_flags": busy_flags,
            "busy_lock": busy_lock,
            "log_fn": log_fn,
        },
    )
    try:
      while True:
        if shutdown_requested() and not barrier.draining.is_set():
          barrier.draining.set()
          if drain_deadline == 0.0:
            drain_deadline = time.monotonic() + SHUTDOWN_DRAIN_TIMEOUT_S
          with busy_lock:
            local_n = sum(1 for v in busy_flags.values() if v)
          _log(
              "queue_orchestrator shutdown requested; draining inflight=%d"
              % local_n,
              log_fn=log_fn,
          )
        try:
          populate.reap_and_restart()
        except Exception:
          pass
        if (
            recycle_gate.recycle_requested.is_set()
            and recycle_gate.paused.is_set()
        ):
          old_pool = pool_ref.get()
          new_pool = _recycle_ingest_pool(
              old_pool, factory=_new_ingest_pool,
          )
          pool_ref.set(new_pool)
          recycle_gate.recycle_requested.clear()
          recycle_gate.paused.clear()
          _log(
              "queue_orchestrator ingest pool recycled via pause protocol",
              log_fn=log_fn,
          )
        for name, thr in threads:
          if thr.is_alive():
            continue
          if barrier.draining.is_set() or run_once_exit.is_set():
            continue
          fail_closed_on_coordinator_death(role=name, log_fn=log_fn)
        if run_once_exit.is_set():
          barrier.draining.set()
          if barrier.all_drained():
            break
        if barrier.draining.is_set():
          if barrier.all_drained():
            _log("queue_orchestrator drained; exiting", log_fn=log_fn)
            _release_claims_on_shutdown(
                client, claim_maps, log_fn=log_fn,
            )
            break
          if drain_deadline and time.monotonic() >= drain_deadline:
            for fut in list(day_inflight.values()):
              try:
                fut.cancel()
              except Exception:
                pass
            try:
              day_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
              pass
            _log(
                "queue_orchestrator drain timeout; dirty_tar_recovery "
                "append_inflight=%d ingest_inflight=%d"
                % (len(append_inflight), len(ingest_inflight)),
                log_fn=log_fn,
            )
            _release_claims_on_shutdown(
                client, claim_maps, log_fn=log_fn,
            )
            break
          time.sleep(min(0.25, max(0.05, poll_s)))
          continue
        time.sleep(max(0.05, poll_s))
    finally:
      _shutdown_background_discover()
      try:
        populate.stop()
      except Exception:
        pass
      set_populate_pool_controller(None)
      try:
        day_executor.shutdown(wait=False, cancel_futures=True)
      except Exception:
        pass
      # Closes whichever pool object is current, including one installed by a
      # watchdog recycle (a `with` block would only close the first).
      current_pool = pool_ref.get() if "pool_ref" in locals() else ingest_pool
      for method in ("terminate", "join"):
        call = getattr(current_pool, method, None)
        if call is None:
          continue
        try:
          call()
        except Exception:
          pass
