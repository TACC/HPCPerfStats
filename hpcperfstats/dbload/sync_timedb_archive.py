#!/usr/bin/env python3
"""Load stats from existing .tar archives into the database. Workers read from
tar by (path, member_name) so the main process never holds file contents.

"""
import io
import itertools
import multiprocessing
import sys
import tarfile
import time
from functools import partial

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload import sync_timedb
from hpcperfstats.file_locking import file_read_lock_wait
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.sync_timedb_archive_helpers import get_tar_file_tasks
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
)

thread_count = cfg.get_sync_ingest_pool_processes()
TAR_TASK_CHUNK_SIZE = 50


def _process_tar_member(lock, tar_path, member_name):
  """Open tar, extract one member, pass contents to add_stats_file_to_db.
  Keeps file contents only in the worker process."""
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
  sync_timedb.add_stats_file_to_db(lock, member_name, content)


def _iter_tar_tasks_chunked(tar_files, chunk_size=TAR_TASK_CHUNK_SIZE):
  """Yield (tar_path, member_name) tasks in bounded chunks."""
  if chunk_size < 1:
    chunk_size = 1
  tasks_iter = itertools.chain.from_iterable(get_tar_file_tasks(path) for path in tar_files)
  while True:
    chunk = list(itertools.islice(tasks_iter, chunk_size))
    if not chunk:
      break
    yield chunk


if __name__ == '__main__':
  sync_timedb.database_startup()

  tar_files = sys.argv[1:]

  start = time.time()

  for tar_file_name in tar_files:
    log_print(tar_file_name)

  manager = multiprocessing.Manager()
  try:
    manager_lock = manager.Lock()
    with multiprocessing.get_context('spawn').Pool(
        processes=thread_count) as pool:
      worker = partial(_process_tar_member, manager_lock)
      # Process in chunks so SIGTERM can exit between chunks and memory stays bounded.
      for chunk in _iter_tar_tasks_chunked(tar_files, TAR_TASK_CHUNK_SIZE):
        if shutdown_requested[0]:
          log_print("Exiting due to SIGTERM")
          break
        pool.starmap(worker, chunk)
  finally:
    manager.shutdown()
  if shutdown_requested[0]:
    sys.exit(143)
