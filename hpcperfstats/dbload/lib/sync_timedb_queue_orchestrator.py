"""
Greenfield Redis ``job:v1`` queue orchestrator for ``sync_timedb``.

Replaces ``run_sync_timedb_supervisor_loop`` as the sole coordinator inside
the same CLI entry. Holds an exclusive ``archive_dir`` flock, boots streaming
discover into ``job:v1``, then fills reserved hot/catchup ingest slots,
append slots, and day_close threads with sliding-window refill (never join
an entire batch before the next hop). Never starts ``ArchiveJanitor`` or the
retired supervisor loop.

Attributes:
  DAY_CLOSE_THREAD_NAME_PREFIX: Thread name prefix for day_close workers.
"""
from __future__ import annotations

from typing import Any, Callable

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date
from multiprocessing.pool import AsyncResult
import os
import time

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
from hpcperfstats.dbload.lib.sync_timedb_archive_dir_lock import (
    exclusive_archive_dir_flock,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    daily_tar_path_for_stats_path,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    get_archive_members_redis_client,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    ensure_persistence_contract,
)
from hpcperfstats.dbload.lib.sync_timedb_stats_find import run_find_stats
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    create_sync_timedb_spawn_pool,
)
from hpcperfstats.dbload.lib.process_title import apply_pool_worker_process_title

DAY_CLOSE_THREAD_NAME_PREFIX = "day-close"


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
) -> jd.StreamingDiscoverStats:
  """
  Run GNU find and stream-classify incomplete ingest/append jobs.

  Empty Redis before or after this call does **not** mean caught up.

  Args:
    client (Any): Redis client for ``job:v1``.
    archive_dir (str): Archive data directory (find root).
    tgz_archive_dir (str): Daily archive directory for append classify.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    StreamingDiscoverStats: Boot discover counters.

  Examples:
    >>> class _C:
    ...   def zadd(self, *a, **k):
    ...     return 0
    ...   def rpush(self, *a, **k):
    ...     return 0
    >>> isinstance(
    ...   _boot_stream_discover(_C(), "/nope", tgz_archive_dir="/d"),
    ...   jd.StreamingDiscoverStats,
    ... )
    True
  """
  today = date.today()
  records = run_find_stats(archive_dir, log_fn=log_fn)
  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      records,
      tgz_archive_dir=tgz_archive_dir,
      today=today,
      hot_days=_hot_days(),
      archive_data_dir=archive_dir,
  )
  _log(
      "queue_orchestrator boot discover seen=%d ingest=%d append=%d skipped=%d"
      % (
          stats.seen,
          stats.enqueued_ingest,
          stats.enqueued_append,
          stats.skipped_complete,
      ),
      log_fn=log_fn,
  )
  return stats


def _ingest_worker(lock: Any, path: str) -> Any:
  """
  Spawn-pool ingest entry: parse + write one closed raw stats path.

  Args:
    lock (Any): Manager write lock (or shard list handled by callee).
    path (str): Absolute closed raw stats path.

  Returns:
    Any: Packed ingest worker result from ``add_stats_file_to_db``.

  Examples:
    >>> # doctest: +SKIP
    >>> _ingest_worker(None, "/x")
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
    >>> # doctest: +SKIP
    >>> _append_worker(("/d/2026-01-01.tar", []))
  """
  from hpcperfstats.dbload.sync_timedb import archive_stats_files

  return archive_stats_files(archive_info)


def _run_day_close_job(
  identity: str,
  *,
  tgz_archive_dir: str,
  archive_data_dir: str | None = None,
  log_fn: Callable[..., None] | None = None,
) -> str:
  """
  Day-close thread body: age gate, seal, raw removal, tar-drop when ready.

  Re-enqueues when the age gate is not yet satisfied. Does not start
  ``ArchiveJanitor``.

  Args:
    identity (str): Day token (``YYYY-MM-DD``) or daily ``.tar`` path.
    tgz_archive_dir (str): Daily archive directory.
    archive_data_dir (str | None): Archive root for day_raw_removal manifests.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    str: Outcome token (``complete``, ``deferred_age``, ``sealed``,
    ``raw_removed``, ``tar_dropped``, ``skipped``).

  Examples:
    >>> _run_day_close_job(
    ...   "2099-01-01",
    ...   tgz_archive_dir="/tmp",
    ...   log_fn=lambda *a, **k: None,
    ... ) in (
    ...   "complete", "deferred_age", "sealed", "raw_removed",
    ...   "tar_dropped", "skipped",
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
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        seal_dirty_daily_archives,
    )
    from hpcperfstats.dbload.lib.conf_parser import (
        get_archive_keep_uncompressed_tar,
        get_archive_zstd_level,
        get_archive_zstd_threads,
        get_host_name_ext,
        get_local_timezone,
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
        log_fn=log_fn or (lambda *a, **k: None),
    )
    outcome = "sealed"

    root = str(archive_data_dir or "").strip() or os.path.dirname(
        os.path.normpath(tgz_archive_dir)
    )
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        DayRawRemovalCoordinator,
    )
    from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
        stats_file_head_ingested_in_db,
    )

    coord = DayRawRemovalCoordinator(
        archive_data_dir=root,
        host_name_ext=get_host_name_ext() or "",
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn or (lambda *a, **k: None),
        get_quarantine_skip_paths=lambda: set(),
        ingest_ready_fn=stats_file_head_ingested_in_db,
    )
    deleted = int(coord.apply_batch_delete(tar_path) or 0)
    if deleted > 0:
      outcome = "raw_removed"
    # Prefer filesystem complete + sealed sibling → tar_drop.
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
  return outcome


def _fill_ingest_band(
  client: Any,
  *,
  band: str,
  cap: int,
  inflight: dict[str, AsyncResult],
  leases: dict[str, str],
  ingest_pool: Any,
  manager_lock: Any,
) -> int:
  """
  Pop ranged ingest jobs and submit until ``cap`` in-flight for ``band``.

  Args:
    client (Any): Redis client.
    band (str): ``hot`` or ``catchup``.
    cap (int): Max concurrent jobs for this band.
    inflight (dict[str, AsyncResult]): Identity → async result map.
    leases (dict[str, str]): Identity → lease token.
    ingest_pool (Any): Spawn ``multiprocessing.Pool``.
    manager_lock (Any): Write lock for ingest workers.

  Returns:
    int: Newly submitted job count.

  Examples:
    >>> _fill_ingest_band(
    ...   type("C", (), {"evalsha": lambda *a: None, "eval": lambda *a: None,
    ...                  "script_load": lambda s: "x"})(),
    ...   band="hot",
    ...   cap=0,
    ...   inflight={},
    ...   leases={},
    ...   ingest_pool=None,
    ...   manager_lock=None,
    ... )
    0
  """
  submitted = 0
  band_inflight = sum(1 for i in inflight if leases.get(i, "").startswith(band + ":"))
  # Track band via parallel dict instead — use simple count of free slots.
  del band_inflight
  while len(inflight) < cap:
    identity = jq.pop_ingest_job_ranged(client, band=band)
    if identity is None:
      break
    token = jq.try_acquire_job_lease(
        client, kind=jq.JOB_KIND_INGEST, identity=identity
    )
    if not token:
      # Another worker holds the lease; drop (identity already popped).
      continue
    path = _path_from_ingest_identity(identity)
    if not path or not os.path.isfile(path):
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_INGEST, identity=identity, owner_token=token
      )
      continue
    async_res = ingest_pool.apply_async(_ingest_worker, (manager_lock, path))
    inflight[identity] = async_res
    leases[identity] = token
    submitted += 1
  return submitted


def _drain_ingest_ready(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  leases: dict[str, str],
  tgz_archive_dir: str,
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished ingest async results; enqueue append on success.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight ingest map (mutated).
    leases (dict[str, str]): Lease tokens (mutated).
    tgz_archive_dir (str): Daily archive dir (unused; append uses path).
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of completed ingest jobs drained.

  Examples:
    >>> _drain_ingest_ready(
    ...   type("C", (), {"eval": lambda *a: 1, "evalsha": lambda *a: 1,
    ...                  "script_load": lambda s: "x", "rpush": lambda *a: 1})(),
    ...   inflight={},
    ...   leases={},
    ...   tgz_archive_dir="/d",
    ... )
    0
  """
  del tgz_archive_dir
  done = 0
  for identity, async_res in list(inflight.items()):
    if not async_res.ready():
      continue
    token = leases.pop(identity, None)
    inflight.pop(identity, None)
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
      if token:
        jq.release_job_lease(
            client, kind=jq.JOB_KIND_INGEST, identity=identity, owner_token=token
        )
      continue
    ingest_ok = False
    need_archival = False
    if isinstance(result, (tuple, list)) and len(result) >= 3:
      need_archival = bool(result[1])
      ingest_ok = bool(result[2])
    if ingest_ok and need_archival and path:
      jq.enqueue_list_job(
          client, kind=jq.JOB_KIND_APPEND, identity=path
      )
    if token:
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_INGEST, identity=identity, owner_token=token
      )
  return done


def _fill_append_slots(
  client: Any,
  *,
  cap: int,
  inflight: dict[str, AsyncResult],
  leases: dict[str, str],
  archive_pool: Any,
  tgz_archive_dir: str,
) -> int:
  """
  Pop append LIST jobs and submit grouped ``archive_stats_files`` tasks.

  Args:
    client (Any): Redis client.
    cap (int): Max concurrent append jobs.
    inflight (dict[str, AsyncResult]): Path → async result.
    leases (dict[str, str]): Path → lease token.
    archive_pool (Any): Archive spawn pool.
    tgz_archive_dir (str): Daily archive directory.

  Returns:
    int: Newly submitted append jobs.

  Examples:
    >>> _fill_append_slots(
    ...   type("C", (), {"lpop": lambda *a: None})(),
    ...   cap=0,
    ...   inflight={},
    ...   leases={},
    ...   archive_pool=None,
    ...   tgz_archive_dir="/d",
    ... )
    0
  """
  submitted = 0
  while len(inflight) < cap:
    path = jq.pop_list_job(client, kind=jq.JOB_KIND_APPEND)
    if path is None:
      break
    token = jq.try_acquire_job_lease(
        client, kind=jq.JOB_KIND_APPEND, identity=path
    )
    if not token:
      continue
    if not path or not os.path.isfile(path):
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_APPEND, identity=path, owner_token=token
      )
      continue
    tar_path = daily_tar_path_for_stats_path(path, tgz_archive_dir)
    if not tar_path:
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_APPEND, identity=path, owner_token=token
      )
      continue
    async_res = archive_pool.apply_async(
        _append_worker, ((tar_path, [path]),)
    )
    inflight[path] = async_res
    leases[path] = token
    submitted += 1
  return submitted


def _drain_append_ready(
  client: Any,
  *,
  inflight: dict[str, AsyncResult],
  leases: dict[str, str],
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished append async results and release leases.

  Args:
    client (Any): Redis client.
    inflight (dict[str, AsyncResult]): In-flight append map (mutated).
    leases (dict[str, str]): Lease tokens (mutated).
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Number of append jobs drained.

  Examples:
    >>> _drain_append_ready(
    ...   type("C", (), {"eval": lambda *a: 1, "evalsha": lambda *a: 1,
    ...                  "script_load": lambda s: "x"})(),
    ...   inflight={},
    ...   leases={},
    ... )
    0
  """
  done = 0
  for path, async_res in list(inflight.items()):
    if not async_res.ready():
      continue
    token = leases.pop(path, None)
    inflight.pop(path, None)
    done += 1
    try:
      async_res.get(timeout=0)
    except Exception as exc:
      _log(
          "queue_orchestrator append fail path=%s err=%s"
          % (path, type(exc).__name__),
          log_fn=log_fn,
      )
    if token:
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_APPEND, identity=path, owner_token=token
      )
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
    leases (dict[str, str]): Identity → lease token.
    tgz_archive_dir (str): Daily archive directory.
    archive_data_dir (str): Archive data root for day_raw_removal.
    log_fn (Callable[..., None] | None): Optional logger.

  Returns:
    int: Newly submitted day_close jobs.

  Examples:
    >>> from concurrent.futures import ThreadPoolExecutor
    >>> with ThreadPoolExecutor(max_workers=1) as ex:
    ...   _fill_day_close_slots(
    ...     type("C", (), {"lpop": lambda *a: None})(),
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
    identity = jq.pop_list_job(client, kind=jq.JOB_KIND_DAY_CLOSE)
    if identity is None:
      break
    token = jq.try_acquire_job_lease(
        client, kind=jq.JOB_KIND_DAY_CLOSE, identity=identity
    )
    if not token:
      continue
    fut = executor.submit(
        _run_day_close_job,
        identity,
        tgz_archive_dir=tgz_archive_dir,
        archive_data_dir=archive_data_dir,
        log_fn=log_fn,
    )
    inflight[identity] = fut
    leases[identity] = token
    submitted += 1
  return submitted


def _drain_day_close_ready(
  client: Any,
  *,
  inflight: dict[str, Future],
  leases: dict[str, str],
  log_fn: Callable[..., None] | None = None,
) -> int:
  """
  Collect finished day_close futures; re-enqueue when age-deferred.

  Args:
    client (Any): Redis client.
    inflight (dict[str, Future]): In-flight day_close map (mutated).
    leases (dict[str, str]): Lease tokens (mutated).
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
    token = leases.pop(identity, None)
    inflight.pop(identity, None)
    done += 1
    outcome = "skipped"
    try:
      outcome = str(fut.result())
    except Exception as exc:
      _log(
          "queue_orchestrator day_close fail id=%s err=%s"
          % (identity, type(exc).__name__),
          log_fn=log_fn,
      )
    if outcome == "deferred_age":
      jq.enqueue_list_job(
          client, kind=jq.JOB_KIND_DAY_CLOSE, identity=identity
      )
    if token:
      jq.release_job_lease(
          client, kind=jq.JOB_KIND_DAY_CLOSE, identity=identity, owner_token=token
      )
  return done


def _queues_appear_idle(client: Any) -> bool:
  """
  True when ingest ZSET and append/day_close LISTs report empty lengths.

  Empty queues still do **not** mean caught up (reconstruct law).

  Args:
    client (Any): Redis client with ``zcard`` / ``llen``.

  Returns:
    bool: True when all job structures report zero length.

  Examples:
    >>> class _C:
    ...   def zcard(self, k):
    ...     return 0
    ...   def llen(self, k):
    ...     return 0
    >>> _queues_appear_idle(_C())
    True
  """
  try:
    ingest_n = int(client.zcard(jq.job_queue_key(jq.JOB_KIND_INGEST)) or 0)
    append_n = int(client.llen(jq.job_queue_key(jq.JOB_KIND_APPEND)) or 0)
    day_n = int(client.llen(jq.job_queue_key(jq.JOB_KIND_DAY_CLOSE)) or 0)
  except Exception:
    return False
  return ingest_n == 0 and append_n == 0 and day_n == 0


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
    >>> # doctest: +SKIP
    >>> run_sync_timedb_queue_orchestrator(
    ...   "/data", "backlog", None, ".ext", None, None, run_once=True
    ... )
  """
  del startdate, enddate, host_name_ext  # entry parity; discover uses find root
  directory = os.path.normpath(str(archive_dir or ""))
  if not directory:
    raise ValueError("archive_dir is required")

  with exclusive_archive_dir_flock(directory, blocking=True):
    ensure_persistence_contract(directory, log_fn=log_fn)
    try:
      client = get_archive_members_redis_client(required=True)
    except Exception as exc:
      raise RuntimeError(
          "queue orchestrator requires Redis (fail-closed): %s"
          % type(exc).__name__
      ) from exc

    tgz_archive_dir = cfg.get_daily_archive_dir_path()
    _boot_stream_discover(
        client,
        directory,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_fn,
    )

    ingest_pool_size = max(1, int(cfg.get_sync_ingest_pool_processes()))
    hot_cap, catchup_cap = jq.ingest_band_slot_caps(ingest_pool_size)
    append_cap = max(1, int(cfg.get_sync_archive_pool_processes()))
    poll_s = float(cfg.get_sync_pool_poll_timeout_s())
    day_close_workers = max(1, int(cfg.get_sync_day_close_max_inflight()))

    ingest_inflight: dict[str, AsyncResult] = {}
    ingest_leases: dict[str, str] = {}
    append_inflight: dict[str, AsyncResult] = {}
    append_leases: dict[str, str] = {}
    day_inflight: dict[str, Future] = {}
    day_leases: dict[str, str] = {}

    with create_sync_timedb_spawn_pool(
        processes=ingest_pool_size,
        initializer=apply_pool_worker_process_title,
        initargs=("sync_timedb.py", "ingest-pool"),
        pool_kind_log_label="ingest-pool",
    ) as ingest_pool:
      with ThreadPoolExecutor(
          max_workers=day_close_workers,
          thread_name_prefix=DAY_CLOSE_THREAD_NAME_PREFIX,
      ) as day_executor:
        idle_rounds = 0
        while True:
          did = 0
          # Reserved hot/catchup slots; empty band may use remaining free slots.
          while len(ingest_inflight) < hot_cap:
            n = _fill_ingest_band(
                client,
                band="hot",
                cap=hot_cap,
                inflight=ingest_inflight,
                leases=ingest_leases,
                ingest_pool=ingest_pool,
                manager_lock=manager_lock,
            )
            did += n
            if n == 0:
              break
          catchup_limit = (
              hot_cap + catchup_cap
              if catchup_cap > 0
              else ingest_pool_size
          )
          while len(ingest_inflight) < min(ingest_pool_size, catchup_limit):
            n = _fill_ingest_band(
                client,
                band="catchup",
                cap=min(ingest_pool_size, catchup_limit),
                inflight=ingest_inflight,
                leases=ingest_leases,
                ingest_pool=ingest_pool,
                manager_lock=manager_lock,
            )
            did += n
            if n == 0:
              break
          # When catchup empty, let hot use remaining pool slots.
          while len(ingest_inflight) < ingest_pool_size:
            n = _fill_ingest_band(
                client,
                band="hot",
                cap=ingest_pool_size,
                inflight=ingest_inflight,
                leases=ingest_leases,
                ingest_pool=ingest_pool,
                manager_lock=manager_lock,
            )
            did += n
            if n == 0:
              break
          did += _drain_ingest_ready(
              client,
              inflight=ingest_inflight,
              leases=ingest_leases,
              tgz_archive_dir=tgz_archive_dir,
              log_fn=log_fn,
          )
          did += _fill_append_slots(
              client,
              cap=append_cap,
              inflight=append_inflight,
              leases=append_leases,
              archive_pool=archive_pool,
              tgz_archive_dir=tgz_archive_dir,
          )
          did += _drain_append_ready(
              client,
              inflight=append_inflight,
              leases=append_leases,
              log_fn=log_fn,
          )
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
              log_fn=log_fn,
          )

          busy = bool(ingest_inflight or append_inflight or day_inflight)
          if run_once and not busy and _queues_appear_idle(client):
            if did == 0:
              idle_rounds += 1
            else:
              idle_rounds = 0
            if idle_rounds >= 1:
              _log("queue_orchestrator run_once idle exit", log_fn=log_fn)
              break
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
