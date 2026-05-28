#!/usr/bin/env python3
"""Load stats from existing .tar archives into the database. Workers read from
tar by (path, member_name) so the main process never holds file contents.

Ingest uses Django ORM bulk paths via ``sync_timedb.add_stats_file_to_db`` (not
raw SQL). Heavy ``sync_timedb`` / DB driver imports are deferred until a worker
actually writes so lightweight imports (e.g. tests that only chunk tar tasks)
do not load the database stack.

``sync_timedb_archive_helpers`` transitively imports numpy/pandas; without a low
BLAS/OpenMP thread cap, each process tries to spawn many pthreads and
multiprocessing ``spawn`` workers can hit ``Resource temporarily unavailable`` in
containers. Defaults are applied below before those imports (override via env).

"""
import io
import itertools
import multiprocessing
import os
import sys
import tarfile
import time

_BLAS_THREAD_ENV_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def _configure_blas_thread_env():
  """Cap BLAS/OpenMP worker threads before numpy is first imported.

  Uses setdefault so operator-provided env wins. Safe to call repeatedly.
  """
  for key in _BLAS_THREAD_ENV_KEYS:
    os.environ.setdefault(key, "1")


_configure_blas_thread_env()

from hpcperfstats.process_title import set_script_process_title

set_script_process_title()

import hpcperfstats.conf_parser as cfg
from hpcperfstats.file_locking import file_read_lock_wait
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.sync_timedb_archive_helpers import iter_tar_file_tasks
from hpcperfstats.dbload.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
    reraise_database_unavailable_chain,
)
from hpcperfstats.shutdown_utils import shutdown_requested


def _archive_worker_process_count():
  """Archive ingest pool size: half of ``get_sync_ingest_pool_processes()``, min 1."""
  return max(1, cfg.get_sync_ingest_pool_processes() // 2)


thread_count = _archive_worker_process_count()
TAR_TASK_CHUNK_SIZE = 50


def _process_tar_member(lock, tar_path, member_name):
  """Open tar, extract one member, pass contents to add_stats_file_to_db.
  Keeps file contents only in the worker process."""
  _configure_blas_thread_env()
  log_print("extracting %s from %s" % (member_name, tar_path))
  with file_read_lock_wait(tar_path):
    with tarfile.open(tar_path, 'r') as tar:
      member = tar.getmember(member_name)
      f = tar.extractfile(member)
      if f is None:
        return  # directories / unsupported entries
      # Build list of lines by iterating to avoid holding full decoded string in memory
      wrapper = io.TextIOWrapper(f, encoding="utf-8")
      content = list(wrapper)
      wrapper.detach()
  # Defer sync_timedb import until after tar I/O so DB connections and the
  # backend driver are not loaded for idle workers or lightweight module imports.
  from django.db import close_old_connections
  from django.db.utils import DatabaseError, OperationalError

  from hpcperfstats.django_bootstrap import ensure_django
  from hpcperfstats.dbload.sync_timedb import add_stats_file_to_db

  ensure_django()
  close_old_connections()
  try:
    add_stats_file_to_db(lock, member_name, content)
  except DatabaseUnavailableExit:
    raise
  except (OperationalError, DatabaseError) as exc:
    if is_database_unavailable_error(exc):
      log_and_raise_database_unavailable(
          exc, context="sync_timedb_archive worker"
      )
    raise


def _process_tar_member_task(task_args):
  """Ingest one tar member; ``task_args`` is ``(lock, tar_path, member_name)``.

  Must live at module scope: ``multiprocessing`` spawn workers re-import this
  module and cannot unpickle nested functions defined under ``__main__``.
  """
  lock, tar_path, member_name = task_args
  _process_tar_member(lock, tar_path, member_name)


def _iter_tar_tasks_chunked(tar_files, chunk_size=TAR_TASK_CHUNK_SIZE):
  """Yield (tar_path, member_name) tasks in bounded chunks."""
  if chunk_size < 1:
    chunk_size = 1
  tasks_iter = itertools.chain.from_iterable(
      iter_tar_file_tasks(path) for path in tar_files
  )
  while True:
    chunk = list(itertools.islice(tasks_iter, chunk_size))
    if not chunk:
      break
    yield chunk


def _process_tar_chunk_interruptibly(pool, worker, chunk, on_result):
  """Process one task chunk while allowing shutdown checks between completions."""
  try:
    for result in pool.imap_unordered(worker, chunk, chunksize=1):
      on_result(result)
      if shutdown_requested[0]:
        break
  except DatabaseUnavailableExit:
    raise
  except Exception as exc:
    reraise_database_unavailable_chain(
        exc, context="sync_timedb_archive pool"
    )
    raise


if __name__ == '__main__':
  _configure_blas_thread_env()

  from django.db import close_old_connections, connections

  from hpcperfstats.django_bootstrap import ensure_django
  from hpcperfstats.dbload.sync_timedb import (
      _reset_sync_runtime_caches,
      database_startup,
  )

  try:
    ensure_django()
    database_startup()
    _reset_sync_runtime_caches()
    # Parent only needed the DB for startup diagnostics; drop connections before
    # workers run so the server is not charged an extra idle session per archive run.
    close_old_connections()
    connections.close_all()

    tar_files = sys.argv[1:]

    start = time.time()

    for tar_file_name in tar_files:
      log_print(tar_file_name)

    # Spawn workers receive tasks via pickle; plain ``ctx.Lock()`` is not picklable.
    # Match ``sync_timedb.run_sync_timedb_supervisor_from_parsed``: Manager proxies.
    manager = multiprocessing.Manager()
    try:
      lock_shards = max(1, int(cfg.get_sync_write_lock_shards()))
      if lock_shards == 1:
        manager_lock = manager.Lock()
      else:
        manager_lock = [manager.Lock() for _ in range(lock_shards)]
        log_print(
            "Using %d sync_timedb_archive write-lock shards" % lock_shards,
            flush=True,
        )
      ctx = multiprocessing.get_context('spawn')
      with ctx.Pool(processes=_archive_worker_process_count()) as pool:
        # Process in chunks so SIGTERM can exit between chunks and memory stays bounded.
        for chunk in _iter_tar_tasks_chunked(tar_files, TAR_TASK_CHUNK_SIZE):
          if shutdown_requested[0]:
            log_print("Exiting due to SIGTERM")
            break
          chunk_locked = [(manager_lock, p, m) for p, m in chunk]
          _process_tar_chunk_interruptibly(
              pool,
              _process_tar_member_task,
              chunk_locked,
              lambda _result: None,
          )
          if shutdown_requested[0]:
            pool.terminate()
            break
    finally:
      manager.shutdown()
    try:
      connections.close_all()
    except Exception:
      pass
  except DatabaseUnavailableExit:
    sys.exit(2)
  if shutdown_requested[0]:
    sys.exit(143)
