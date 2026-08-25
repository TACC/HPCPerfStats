"""
Greenfield Redis ``job:v1`` queue orchestrator for ``sync_timedb``.

Replaces ``run_sync_timedb_supervisor_loop`` as the sole coordinator inside
the same CLI entry. Holds an exclusive ``archive_dir`` flock, boots streaming
discover into ``job:v1``, then fills reserved hot/catchup ingest slots,
append slots, and day_close threads with sliding-window refill (never join
an entire batch before the next hop). Never starts ``ArchiveJanitor`` or the
retired supervisor loop.

Attributes:
  CENSUS_LOG_INTERVAL_S: Minimum seconds between structured census log lines.
  DAY_CLOSE_THREAD_NAME_PREFIX: Thread name prefix for day_close workers.
  INGEST_WATCHDOG_GRACE_S: Slack added to the per-file ingest budget before a
    still-unready worker is treated as dead.
  SHUTDOWN_DRAIN_TIMEOUT_S: Bounded wall clock for the cooperative drain.
  _IDLE_RECONSTRUCT_MIN_INTERVAL_S: Min seconds between idle reconstruct passes.
  _SHUTDOWN_REQUESTED: Event set by ``SIGTERM``/``SIGINT`` handlers.
  _last_idle_reconstruct_mono: Monotonic timestamp of last idle reconstruct.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date, datetime
from multiprocessing.pool import AsyncResult
import os
import signal
import threading
import time

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as it
from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
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
from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
    PopulatePoolController,
    set_populate_pool_controller,
)
from hpcperfstats.dbload.lib.process_title import (
    apply_pool_worker_process_title,
    set_daemon_thread_title,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    ensure_persistence_contract,
)
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
    iter_find_stats_stdout_chunks,
)
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    create_sync_timedb_spawn_pool,
)

DAY_CLOSE_THREAD_NAME_PREFIX = "day-close"
CENSUS_LOG_INTERVAL_S = 60.0
SHUTDOWN_DRAIN_TIMEOUT_S = 120.0
# Slack over the per-file budget: a worker that is merely slow (contended DB,
# large file) must not be abandoned before the ingest path itself gives up.
INGEST_WATCHDOG_GRACE_S = it.STALL_ABORT_GRACE_S
_IDLE_RECONSTRUCT_MIN_INTERVAL_S = 30.0
_last_idle_reconstruct_mono = 0.0
_SHUTDOWN_REQUESTED = threading.Event()


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


def reset_shutdown_for_tests() -> None:
  """
  Clear the cooperative shutdown flag (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_shutdown_for_tests()
  """
  _SHUTDOWN_REQUESTED.clear()


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
  Emit an orchestrator log line via ``log_fn`` or stdout print.

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
    print(msg, flush=True)


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
  (boot); a positive window is the periodic reconstruct rescan.

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
    if jr.enqueue_day_close_if_needed(
        client,
        tar_path,
        calendar_day=cal,
    ):
      enqueued += 1
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
  ``force`` (``run_once`` exit path). Runs even while ingest/append are busy.

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
    try:
      jq.enqueue_list_job(
          client,
          kind=jq.JOB_KIND_DISCOVER,
          identity=identity,
      )
    except Exception:
      pass
  work = 0
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
  if work:
    _log(
        "queue_orchestrator idle reconstruct work=%d" % work,
        log_fn=log_fn,
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
  from hpcperfstats.dbload.sync_timedb import add_stats_file_to_db

  return add_stats_file_to_db(lock, path)


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
    str: Outcome token (``complete``, ``deferred_age``, ``sealed``,
    ``raw_removed``, ``tar_dropped``, ``skipped``, ``verify_failed``,
    ``yielded``).

  Examples:
    >>> _run_day_close_job(
    ...   "2099-01-01",
    ...   tgz_archive_dir="/tmp",
    ...   log_fn=lambda *a, **k: None,
    ... ) in (
    ...   "complete", "deferred_age", "sealed", "raw_removed",
    ...   "tar_dropped", "skipped", "verify_failed", "yielded",
    ... )
    True
  """
  text = str(identity or "").strip()
  if not text:
    return "skipped"
  if text.endswith(".tar"):
    tar_path = os.path.normpath(text)
    day_token = os.path.basename(tar_path)[:10]
  else:
    day_token = text[:10]
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
  outcome = "skipped"
  try:
    from django.db import close_old_connections

    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        dedupe_tar_keep_largest_file_per_member,
        seal_dirty_daily_archives,
    )
    from hpcperfstats.dbload.lib.conf_parser import (
        get_archive_keep_uncompressed_tar,
        get_archive_zstd_level,
        get_archive_zstd_threads,
        get_host_name_ext,
        get_local_timezone,
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
    outcome = "sealed"

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
      outcome = "raw_removed"
    zst_path = tar_path + ".zst"
    if (
        os.path.isfile(zst_path)
        and os.path.isfile(tar_path)
        and not coord.has_closed_raw_on_disk(tar_path)
    ):
      try:
        os.remove(tar_path)
        outcome = "tar_dropped"
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
  return outcome


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
  attempt = jq.bump_job_attempt(client, kind=kind, identity=identity)
  if attempt < jq.job_max_attempts():
    jq.requeue_job(
        client,
        kind=kind,
        identity=identity,
        owner_token=claim.owner_token,
        score=getattr(claim, "score", None),
    )
    return "requeued"
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
      continue
    path = _path_from_ingest_identity(claim.identity)
    if not path or not os.path.isfile(path):
      # Transient NFS / rename: never terminal-ack; requeue for reconstruct.
      jq.requeue_job(
          client,
          kind=jq.JOB_KIND_INGEST,
          identity=claim.identity,
          owner_token=claim.owner_token,
          score=claim.score,
      )
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
  del tgz_archive_dir
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
    except Exception as exc:
      _log(
          "queue_orchestrator ingest fail identity=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
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
    if isinstance(result, (tuple, list)) and len(result) >= 3:
      need_archival = bool(result[1])
      ingest_ok = bool(result[2])
    if not ingest_ok:
      _retry_or_dead_letter(
          client,
          kind=jq.JOB_KIND_INGEST,
          claim=claim,
          archive_data_dir=archive_data_dir,
          reason="ingest_incomplete",
          log_fn=log_fn,
      )
      continue
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
  batch_size = max(1, int(cfg.get_sync_timedb_tar_append_batch_size()))
  while len(inflight) < cap:
    batch_claims: list[Any] = []
    tar_path = ""
    while len(batch_claims) < batch_size:
      claim = jq.claim_list_job(
          client,
          kind=jq.JOB_KIND_APPEND,
          owner_token=jq.make_lease_owner_token(),
      )
      if claim is None:
        break
      path = claim.identity
      if not path or not os.path.isfile(path):
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
        continue
      this_tar = daily_tar_path_for_stats_path(path, tgz_archive_dir)
      if not this_tar:
        jq.requeue_job(
            client,
            kind=jq.JOB_KIND_APPEND,
            identity=path,
            owner_token=claim.owner_token,
        )
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
  done = 0
  for key, async_res in list(inflight.items()):
    if not async_res.ready():
      continue
    claim = claims.pop(key, None)
    inflight.pop(key, None)
    done += 1
    jobs = _iter_claim_jobs(claim)
    failed = False
    try:
      async_res.get(timeout=0)
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
    for job in jobs:
      jq.ack_job(
          client,
          kind=jq.JOB_KIND_APPEND,
          identity=job.identity,
          owner_token=job.owner_token,
      )
    tar = key if str(key).endswith(".tar") else daily_tar_path_for_stats_path(
        key, tgz_archive_dir,
    )
    if tar:
      jr.enqueue_day_close_if_needed(client, tar)
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
  Collect finished day_close futures; retry retryable outcomes.

  ``deferred_age`` and ``skipped`` are retryable (the day is simply not ready
  or hit a transient error), so they go back on the queue with an attempt
  bump instead of being acked away.

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
    ...                  "script_load": lambda s: "x", "rpush": lambda *a: 1})(),
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
    if failure or outcome in (
        "deferred_age",
        "skipped",
        "verify_failed",
        "yielded",
    ):
      _retry_or_dead_letter(
          client,
          kind=jq.JOB_KIND_DAY_CLOSE,
          claim=claim,
          archive_data_dir=archive_data_dir,
          reason=failure or outcome,
          log_fn=log_fn,
      )
      continue
    if claim is not None:
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

  A claim that stops renewing is treated as abandoned by
  :func:`sync_timedb_job_queue.reap_expired_inflight`, so renewal must run on
  every tick while work is outstanding.

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


def _reap_stale_inflight(
  client: Any,
  *,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Recover in-flight jobs abandoned by a dead or hung owner.

  Args:
    client (Any): Redis client.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of identities returned to their queues.

  Examples:
    >>> _reap_stale_inflight(None)
    0
  """
  if client is None:
    return 0
  total = 0
  for kind in jq.JOB_KINDS_ALL:
    try:
      recovered = jq.reap_expired_inflight(client, kind=kind)
    except Exception as exc:
      _log(
          "queue_orchestrator reap error kind=%s err=%s"
          % (kind, type(exc).__name__),
          log_fn=log_fn,
      )
      continue
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
      stolen = 0
      _log(
          "queue_orchestrator steal_dead_owner_leases err=%s"
          % type(exc).__name__,
          log_fn=log_fn,
      )
    if stolen:
      _log(
          "queue_orchestrator stole dead-owner leases count=%d" % stolen,
          log_fn=log_fn,
      )
    _reap_stale_inflight(client, log_fn=log_fn)
    _boot_stream_discover(
        client,
        directory,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
        mtime_days=None,
        startdate=startdate,
        enddate=enddate,
    )

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
    try:
      with ThreadPoolExecutor(
          max_workers=day_close_workers,
          thread_name_prefix=DAY_CLOSE_THREAD_NAME_PREFIX,
      ) as day_executor:
        idle_rounds = 0
        while True:
          did = 0
          draining = shutdown_requested()
          if draining and drain_deadline == 0.0:
            drain_deadline = time.monotonic() + SHUTDOWN_DRAIN_TIMEOUT_S
            _log(
                "queue_orchestrator shutdown requested; draining inflight=%d"
                % (
                    len(ingest_inflight)
                    + len(append_inflight)
                    + len(day_inflight)
                ),
                log_fn=log_fn,
            )
          if not draining:
            try:
              populate.reap_and_restart()
            except Exception:
              pass
            # Reserved hot/catchup slots; an empty band may use free slots.
            while len(ingest_inflight) < hot_cap:
              n = _fill_ingest_band(
                  client,
                  band="hot",
                  cap=hot_cap,
                  inflight=ingest_inflight,
                  claims=ingest_leases,
                  submitted=ingest_submitted,
                  ingest_pool=ingest_pool,
                  manager_lock=manager_lock,
                  band_cap=hot_cap,
                  tgz_archive_dir=tgz_archive_dir,
              )
              did += n
              if n == 0:
                break
            try:
              lo, hi = jq.ingest_score_range("hot")
              hot_queued = int(
                  client.zcount(
                      jq.job_queue_key(jq.JOB_KIND_INGEST), lo, hi,
                  ) or 0
              )
            except Exception:
              hot_queued = 1
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
                  ingest_pool=ingest_pool,
                  manager_lock=manager_lock,
                  band_cap=catchup_limit,
                  tgz_archive_dir=tgz_archive_dir,
              )
              did += n
              if n == 0:
                break
            # When catchup is empty, let hot use remaining pool slots.
            while len(ingest_inflight) < ingest_pool_size:
              n = _fill_ingest_band(
                  client,
                  band="hot",
                  cap=ingest_pool_size,
                  inflight=ingest_inflight,
                  claims=ingest_leases,
                  submitted=ingest_submitted,
                  ingest_pool=ingest_pool,
                  manager_lock=manager_lock,
                  tgz_archive_dir=tgz_archive_dir,
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
            # A Pool with a presumed-dead worker cannot be trusted to schedule
            # the next task, so replace it before refilling the freed slots.
            did += len(abandoned)
            collateral = _requeue_pool_collateral(
                client,
                inflight=ingest_inflight,
                claims=ingest_leases,
                submitted=ingest_submitted,
                log_fn=log_fn,
            )
            ingest_pool = _recycle_ingest_pool(
                ingest_pool, factory=_new_ingest_pool,
            )
            _log(
                "queue_orchestrator ingest pool recycled abandoned=%d requeued=%d"
                % (len(abandoned), collateral),
                log_fn=log_fn,
            )
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

          busy = bool(ingest_inflight or append_inflight or day_inflight)
          now_mono = time.monotonic()
          if now_mono - last_reap_mono >= max(poll_s, 5.0):
            last_reap_mono = now_mono
            _reap_stale_inflight(client, log_fn=log_fn)
          if now_mono - last_census_mono >= CENSUS_LOG_INTERVAL_S:
            last_census_mono = now_mono
            try:
              census = jq.queue_census(client)
            except Exception:
              census = {}
            if census:
              _log(
                  "queue_orchestrator census %s local=%d/%d/%d"
                  % (
                      jq.format_queue_census(census),
                      len(ingest_inflight),
                      len(append_inflight),
                      len(day_inflight),
                  ),
                  log_fn=log_fn,
              )

          if not draining:
            _idle_reconstruct_pass(
                client,
                directory,
                tgz_archive_dir=tgz_archive_dir,
                log_fn=log_fn,
                mtime_days=rescan_mtime_days,
                startdate=startdate,
                enddate=enddate,
            )

          if draining:
            if not busy:
              _log("queue_orchestrator drained; exiting", log_fn=log_fn)
              break
            if time.monotonic() >= drain_deadline:
              _release_claims_on_shutdown(
                  client, claim_maps, log_fn=log_fn,
              )
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
                  "queue_orchestrator drain timeout; requeued outstanding work",
                  log_fn=log_fn,
              )
              break
            if day_inflight:
              wait(
                  list(day_inflight.values()),
                  timeout=min(poll_s, 0.5),
                  return_when=FIRST_COMPLETED,
              )
            else:
              time.sleep(min(0.25, max(0.05, poll_s)))
            continue

          if run_once and not busy and _queues_appear_idle(client):
            if did == 0:
              idle_rounds += 1
            else:
              idle_rounds = 0
            if idle_rounds >= 1:
              # One reconstruct pass before run_once exit (empty ≠ caught up).
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
              if recon == 0 and _queues_appear_idle(client):
                _log("queue_orchestrator run_once idle exit", log_fn=log_fn)
                break
              idle_rounds = 0
          elif did == 0 and not busy:
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
      try:
        populate.stop()
      except Exception:
        pass
      set_populate_pool_controller(None)
      # Closes whichever pool object is current, including one installed by a
      # watchdog recycle (a `with` block would only close the first).
      for method in ("terminate", "join"):
        call = getattr(ingest_pool, method, None)
        if call is None:
          continue
        try:
          call()
        except Exception:
          pass
