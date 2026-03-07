#!/usr/bin/env python3
"""Load stats from existing .tar archives into the database. Workers read from
tar by (path, member_name) so the main process never holds file contents.

AI generated.
"""
import io
import multiprocessing
import sys
import tarfile
import time
from functools import partial

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload import sync_timedb
from hpcperfstats.dbload.tar_utils import get_tar_file_tasks

thread_count = cfg.get_worker_thread_count(4)


def _process_tar_member(lock, tar_path, member_name):
  """Open tar, extract one member, pass contents to add_stats_file_to_db.
  Keeps file contents only in the worker process."""
  print("extracting %s" % member_name)
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
    print(tar_file_name)
    tasks.extend(get_tar_file_tasks(tar_file_name))

  with multiprocessing.get_context('spawn').Pool(
      processes=thread_count) as pool:
    manager = multiprocessing.Manager()
    manager_lock = manager.Lock()
    worker = partial(_process_tar_member, manager_lock)
    # Distribute work; each worker loads only one member at a time.
    pool.starmap(worker, tasks)
