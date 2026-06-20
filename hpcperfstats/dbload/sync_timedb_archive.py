#!/usr/bin/env python3
"""Load stats from sealed daily archives (``.tar.zst`` / legacy ``.tar.gz``) into the database.

Operator backfill tool: reads **only** sealed archives via in-memory zstd→tar streaming.
Uncompressed ``YYYY-MM-DD.tar`` is never opened; unsealed days are skipped.

CLI (explicit dates required):
  sync_timedb_archive.py YYYY-MM-DD              # single sealed day
  sync_timedb_archive.py YYYY-MM-DD YYYY-MM-DD   # inclusive range
  sync_timedb_archive.py all                     # all sealed days under daily_archive_dir
  sync_timedb_archive.py /path/to/day.tar.zst    # explicit sealed path(s)

Ingest uses Django ORM bulk paths via ``sync_timedb.add_stats_file_to_db``. Heavy
``sync_timedb`` / DB driver imports are deferred until a worker writes.

``sync_timedb_archive_helpers`` transitively imports numpy/pandas; BLAS/OpenMP
thread caps are applied before those imports (override via env).
"""
import multiprocessing
import os
import re
import sys

from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env


def _configure_blas_thread_env():
  """Cap BLAS/OpenMP worker threads before numpy is first imported."""
  configure_blas_thread_env()


configure_blas_thread_env()

SYNC_TIMEDB_ARCHIVE_PROCESS_TITLE = "sync_timedb_archive.py"

from hpcperfstats.dbload.lib.process_title import set_daemon_process_title
import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    detect_compressed_format,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    STREAM_ARCHIVE_TASK,
    collect_sealed_daily_archive_paths_in_range,
    iter_archive_ingest_tasks,
    iter_sealed_daily_archive_member_paths,
    resolve_sealed_archive_path_for_ingest,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
    max_sealed_archive_ingest_budget_for_paths,
    sealed_archive_member_count_hint,
    stall_abort_polls_for_sealed_archives,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_dispatch_worker_stages,
    seed_dispatch_worker_stages,
)
from hpcperfstats.dbload.lib.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
    reraise_database_unavailable_chain,
)
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    MultiprocessingWorkerExitError,
    imap_unordered_watch_pool,
    terminate_pool_bounded,
)
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested

_DATE_ARG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_USAGE = (
    "usage: sync_timedb_archive.py <YYYY-MM-DD> [YYYY-MM-DD] | all | "
    "<path.tar.zst> | <path.tar.gz>"
)


def _archive_worker_process_count():
  """Archive ingest pool size (default 4); matches ``get_sync_archive_pool_processes``."""
  return cfg.get_sync_archive_pool_processes()


def _log_archive_ingest_startup(sealed_days, skipped_tar_only):
  log_print(
      "sync_timedb_archive: pool_processes=%d zstd_threads=%s "
      "ionice=c%s-n%s nice=%s sealed_days=%d skipped_tar_only=%d "
      "max_concurrent_sealed=%d"
      % (
          _archive_worker_process_count(),
          cfg.get_archive_zstd_threads(),
          cfg.get_archive_zstd_ionice_class(),
          cfg.get_archive_zstd_ionice_level(),
          cfg.get_archive_zstd_nice(),
          sealed_days,
          skipped_tar_only,
          cfg.get_sync_timedb_archive_max_concurrent_sealed_days(),
      ),
      flush=True,
  )


def parse_sync_timedb_archive_argv(argv):
  """Parse argv into ``(mode, startdate, enddate, path_args)``.

  ``mode`` is ``'date'`` or ``'paths'``. For ``'date'``, ``startdate`` is
  ``datetime`` or ``'all'``; ``enddate`` is ``datetime`` or ``None`` (all/range).
  """
  if len(argv) < 2:
    raise SystemExit(_USAGE)
  args = list(argv[1:])
  if args[0] == "all":
    if len(args) > 1:
      raise SystemExit(_USAGE)
    return "date", "all", None, []

  if len(args) <= 2 and all(_DATE_ARG_RE.match(a) for a in args):
    from datetime import datetime

    start = datetime.strptime(args[0], "%Y-%m-%d")
    if len(args) == 1:
      return "date", start, start, []
    end = datetime.strptime(args[1], "%Y-%m-%d")
    return "date", start, end, []

  for path in args:
    base = os.path.basename(path)
    if base.endswith(".tar") and not (
        base.endswith(DAILY_ARCHIVE_ZST_SUFFIX)
        or base.endswith(DAILY_ARCHIVE_GZ_SUFFIX)
    ):
      raise SystemExit(
          "sync_timedb_archive requires sealed archive (.tar.zst or .tar.gz): %s"
          % path,
      )
    if detect_compressed_format(path) not in ("zst", "gz"):
      raise SystemExit(
          "sync_timedb_archive requires sealed archive path: %s" % path,
      )
  return "paths", None, None, args


def _resolve_sealed_paths_from_argv(mode, startdate, enddate, path_args):
  daily_dir = cfg.get_daily_archive_dir_path()
  if mode == "date":
    sealed_paths, skipped = collect_sealed_daily_archive_paths_in_range(
        daily_dir,
        startdate,
        enddate,
    )
    return sealed_paths, skipped
  sealed_paths = []
  skipped = 0
  for path in path_args:
    sealed = resolve_sealed_archive_path_for_ingest(path, daily_dir)
    if sealed:
      sealed_paths.append(sealed)
    else:
      skipped += 1
  return sealed_paths, skipped


def _archive_spawn_pool_recycle_kwargs():
  maxtasks = cfg.get_sync_ingest_pool_maxtasksperchild()
  if maxtasks > 0:
    return {"maxtasksperchild": int(maxtasks)}
  return {}


def _process_stream_archive(lock, sealed_path):
  """Stream one sealed archive and ingest each member via path-only spool."""
  _configure_blas_thread_env()
  log_print("streaming sealed archive %s" % sealed_path, flush=True)
  add_stats = None
  ensure_django = None
  close_old_connections = None
  DatabaseError = None
  OperationalError = None
  release_heap = None
  from hpcperfstats.dbload.sync_timedb import (
      advance_sealed_archive_ingest_progress,
      clear_sealed_archive_ingest_progress,
      set_sealed_archive_ingest_progress,
  )

  set_sealed_archive_ingest_progress(sealed_archive_member_count_hint(sealed_path))

  try:
    for member_name, member_path in iter_sealed_daily_archive_member_paths(
        sealed_path,
        on_member_skipped=advance_sealed_archive_ingest_progress,
    ):
      if add_stats is None:
        from django.db import close_old_connections as _close
        from django.db.utils import DatabaseError as _DBErr
        from django.db.utils import OperationalError as _OpErr

        from hpcperfstats.dbload.lib.django_bootstrap import ensure_django as _ensure
        from hpcperfstats.dbload.sync_timedb import (
            add_stats_file_to_db as _add,
            _release_ingest_worker_heap as _release,
        )

        close_old_connections = _close
        DatabaseError = _DBErr
        OperationalError = _OpErr
        ensure_django = _ensure
        add_stats = _add
        release_heap = _release
        ensure_django()
        close_old_connections()

      try:
        add_stats(lock, member_path)
      except DatabaseUnavailableExit:
        raise
      except (OperationalError, DatabaseError) as exc:
        if is_database_unavailable_error(exc):
          log_and_raise_database_unavailable(
              exc, context="sync_timedb_archive worker",
          )
        raise
      finally:
        if release_heap is not None:
          release_heap()
        try:
          os.remove(member_path)
        except OSError:
          pass
        parent = os.path.dirname(member_path)
        try:
          if parent and os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
        except OSError:
          pass
  finally:
    clear_sealed_archive_ingest_progress()


def _process_stream_archive_task(task_args):
  """Ingest one sealed archive; ``task_args`` is ``(lock, sealed_path)``.

  Module scope for ``multiprocessing`` spawn pickling.
  Returns ``sealed_path`` so the supervisor can clear dispatch placeholders.
  """
  lock, sealed_path = task_args
  _process_stream_archive(lock, sealed_path)
  return sealed_path


def _iter_stream_tasks_chunked(sealed_paths, chunk_size=None):
  """Yield bounded chunks of ``(STREAM_ARCHIVE_TASK, sealed_path)`` tasks."""
  if chunk_size is None:
    chunk_size = cfg.get_sync_timedb_archive_max_concurrent_sealed_days()
  chunk_size = max(1, int(chunk_size))
  tasks = list(iter_archive_ingest_tasks(sealed_paths, cfg.get_daily_archive_dir_path()))
  for off in range(0, len(tasks), chunk_size):
    yield tasks[off : off + chunk_size]


def _sealed_paths_from_chunk_locked(chunk_locked):
  return [str(sealed_path) for _lock, sealed_path in (chunk_locked or ())]


def _prepare_archive_chunk_stall_diagnostics(sealed_paths, stall_diagnostics):
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  batch_max_s = max_sealed_archive_ingest_budget_for_paths(sealed_paths)
  batch_abort = stall_abort_polls_for_sealed_archives(sealed_paths)
  if stall_diagnostics is not None:
    stall_diagnostics.current_imap_batch_size = len(sealed_paths or ())
    stall_diagnostics.current_imap_in_flight = len(sealed_paths or ())
    stall_diagnostics.current_imap_batch_max_timeout_s = batch_max_s
    stall_diagnostics.dynamic_stall_abort_after_polls = batch_abort
    stall_diagnostics.dynamic_stall_wall_s = batch_abort * poll_s
    stall_diagnostics.imap_batch_cap = len(sealed_paths or ())
    stall_diagnostics.ingest_pipeline = "sealed_archive_backfill"
  log_print(
      "sync_timedb_archive: chunk sealed_days=%d sealed_archive_stall_budget_s=%.1f "
      "dynamic_stall_abort_after=%d dynamic_stall_wall_s=%.0f"
      % (
          len(sealed_paths or ()),
          batch_max_s,
          batch_abort,
          batch_abort * poll_s,
      ),
      flush=True,
  )
  return batch_abort, batch_max_s


def _process_task_chunk_interruptibly(
    pool,
    worker,
    chunk_locked,
    on_result,
    *,
    stall_diagnostics=None,
    stall_poll_state=None,
    worker_registry=None,
):
  """Process one sealed-day chunk with pool stall guards aligned to sync_timedb."""
  if not chunk_locked:
    return
  from hpcperfstats.dbload.sync_timedb import (
      IngestStallDiagnostics,
      _build_ingest_stall_log_suffix,
      _calendar_day_hint_from_sealed_paths,
      _distinct_calendar_days_from_sealed_paths,
      _format_redis_populate_for_sealed_paths,
      _handle_pool_worker_exit_fatal,
      _make_ingest_stall_poll_fn,
      _make_ingest_stall_warning_fn,
      _prewarm_archive_members_redis_for_sealed_chunk,
  )

  sealed_paths = _sealed_paths_from_chunk_locked(chunk_locked)
  if stall_diagnostics is None:
    stall_diagnostics = IngestStallDiagnostics()
  if stall_poll_state is None:
    stall_poll_state = {}
  if worker_registry is not None:
    stall_diagnostics.worker_registry = worker_registry
  stall_diagnostics.active_pool = pool
  prewarm_summary = _prewarm_archive_members_redis_for_sealed_chunk(sealed_paths)
  stall_diagnostics.chunk_prewarm_summary = prewarm_summary
  batch_abort, _batch_max_s = _prepare_archive_chunk_stall_diagnostics(
      sealed_paths,
      stall_diagnostics,
  )
  seed_dispatch_worker_stages(worker_registry, sealed_paths)
  in_flight_sample_fn = lambda: list(sealed_paths)
  pool_health_context = {
      "active_pool": pool,
      "in_flight_sample_fn": in_flight_sample_fn,
  }
  try:
    for result in imap_unordered_watch_pool(
        pool,
        worker,
        chunk_locked,
        context="sync_timedb_archive pool",
        stall_abort_after_timeouts=batch_abort,
        on_stall_warning=_make_ingest_stall_warning_fn(
            None,
            pool=pool,
            thread_count=_archive_worker_process_count(),
            chunk_counter=0,
            pending_count=len(sealed_paths),
            stall_diagnostics=stall_diagnostics,
            progress_state=stall_poll_state,
            day_hint_from_sample_fn=_calendar_day_hint_from_sealed_paths,
            distinct_days_from_sample_fn=_distinct_calendar_days_from_sealed_paths,
            redis_populate_for_sample_fn=_format_redis_populate_for_sealed_paths,
        ),
        on_stall_poll=_make_ingest_stall_poll_fn(
            None,
            stall_poll_state,
            stall_diagnostics=stall_diagnostics,
            day_hint_from_sample_fn=_calendar_day_hint_from_sealed_paths,
        ),
        pool_health_context=pool_health_context,
        on_stall_fatal_summary=(
            lambda consecutive, abort_after, poll_timeout_s, ctx: _build_ingest_stall_log_suffix(
                sample=sealed_paths,
                day_hint=_calendar_day_hint_from_sealed_paths(sealed_paths),
                stall_diagnostics=stall_diagnostics,
                progress_state=stall_poll_state,
                alive_workers=0,
                consecutive=consecutive,
                poll_timeout_s=poll_timeout_s,
                distinct_days_from_sample_fn=_distinct_calendar_days_from_sealed_paths,
                redis_populate_for_sample_fn=_format_redis_populate_for_sealed_paths,
            )
        ),
    ):
      if result:
        clear_dispatch_worker_stages(worker_registry, [result])
      stall_diagnostics.note_imap_completion()
      on_result(result)
      if shutdown_requested[0]:
        break
  except MultiprocessingWorkerExitError as exc:
    _handle_pool_worker_exit_fatal(exc)
  except DatabaseUnavailableExit:
    raise
  except Exception as exc:
    reraise_database_unavailable_chain(
        exc, context="sync_timedb_archive pool",
    )
    raise


if __name__ == "__main__":
  _configure_blas_thread_env()

  from django.db import close_old_connections, connections

  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
  from hpcperfstats.dbload.sync_timedb import (
      IngestStallDiagnostics,
      _handle_pool_worker_exit_fatal,
      _reset_sync_runtime_caches,
      _warn_if_pool_stall_wall_below_ingest_timeout_max,
      database_startup,
  )

  try:
    set_daemon_process_title(name=SYNC_TIMEDB_ARCHIVE_PROCESS_TITLE, role="main")
    ensure_django()
    database_startup()
    _reset_sync_runtime_caches()
    _warn_if_pool_stall_wall_below_ingest_timeout_max()
    close_old_connections()
    connections.close_all()

    mode, startdate, enddate, path_args = parse_sync_timedb_archive_argv(sys.argv)
    sealed_paths, skipped_tar_only = _resolve_sealed_paths_from_argv(
        mode, startdate, enddate, path_args,
    )
    _log_archive_ingest_startup(len(sealed_paths), skipped_tar_only)

    if not sealed_paths:
      log_print("sync_timedb_archive: no sealed archives to ingest", flush=True)
      sys.exit(0)

    for sealed_path in sealed_paths:
      log_print(sealed_path, flush=True)

    lock_manager = multiprocessing.Manager()
    diagnostics_manager = multiprocessing.Manager()
    try:
      worker_diagnostics_registry = diagnostics_manager.dict()
      stall_diagnostics = IngestStallDiagnostics()
      stall_diagnostics.worker_registry = worker_diagnostics_registry
      stall_poll_state = {}
      lock_shards = max(1, int(cfg.get_sync_write_lock_shards()))
      if lock_shards == 1:
        manager_lock = lock_manager.Lock()
      else:
        manager_lock = [lock_manager.Lock() for _ in range(lock_shards)]
        log_print(
            "Using %d sync_timedb_archive write-lock shards" % lock_shards,
            flush=True,
        )
      ctx = multiprocessing.get_context("spawn")
      pool = ctx.Pool(
          processes=_archive_worker_process_count(),
          initializer=apply_ingest_pool_worker_init,
          initargs=(
              SYNC_TIMEDB_ARCHIVE_PROCESS_TITLE,
              "sealed-archive-pool",
              worker_diagnostics_registry,
          ),
          **_archive_spawn_pool_recycle_kwargs(),
      )
      try:
        for chunk in _iter_stream_tasks_chunked(sealed_paths):
          if shutdown_requested[0]:
            log_print("Exiting due to SIGTERM", flush=True)
            break
          chunk_locked = [(manager_lock, p) for _kind, p in chunk]
          _process_task_chunk_interruptibly(
              pool,
              _process_stream_archive_task,
              chunk_locked,
              lambda _result: None,
              stall_diagnostics=stall_diagnostics,
              stall_poll_state=stall_poll_state,
              worker_registry=worker_diagnostics_registry,
          )
          if shutdown_requested[0]:
            break
      except MultiprocessingWorkerExitError:
        raise
      finally:
        terminate_pool_bounded(pool)
        try:
          pool.close()
        except Exception:
          pass
    finally:
      diagnostics_manager.shutdown()
      lock_manager.shutdown()
    try:
      connections.close_all()
    except Exception:
      pass
  except DatabaseUnavailableExit:
    sys.exit(2)
  except MultiprocessingWorkerExitError as exc:
    log_print(
        "sync_timedb_archive exiting after pool worker death: %s" % exc,
        flush=True,
    )
    _handle_pool_worker_exit_fatal(exc)
  if shutdown_requested[0]:
    sys.exit(143)
