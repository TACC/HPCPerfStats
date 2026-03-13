#!/usr/bin/env python3
"""Load stats from existing .tar archives into the database. Workers read from
tar by (path, member_name) so the main process never holds file contents.

"""
import io
import multiprocessing
import sys
import tarfile
import time
from functools import partial

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload import sync_timedb
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.sync_timedb_archive_helpers import get_tar_file_tasks
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
)

thread_count = cfg.get_worker_thread_count(4)


def _process_tar_member(lock, tar_path, member_name):
  """Open tar, extract one member, pass contents to add_stats_file_to_db.
  Keeps file contents only in the worker process."""
  log_print("extracting %s from %s" % (member_name, tar_path))
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


if __name__ == '__main__':
  sync_timedb.database_startup()

  tar_files = sys.argv[1:]

  start = time.time()

  # Build only (tar_path, member_name) pairs; no file contents in main process.
  tasks = []
  for tar_file_name in tar_files:
    log_print(tar_file_name)
    tasks.extend(get_tar_file_tasks(tar_file_name))

  manager = multiprocessing.Manager()
  try:
    manager_lock = manager.Lock()
    with multiprocessing.get_context('spawn').Pool(
        processes=thread_count) as pool:
      worker = partial(_process_tar_member, manager_lock)
      # Process in chunks so SIGTERM can exit between chunks.
      chunk_size = max(1, min(50, len(tasks) or 1))
      for i in range(0, len(tasks), chunk_size):
        if shutdown_requested[0]:
          log_print("Exiting due to SIGTERM")
          break
        chunk = tasks[i:i + chunk_size]
        pool.starmap(worker, chunk)
  finally:
    manager.shutdown()
  if shutdown_requested[0]:
    sys.exit(143)
