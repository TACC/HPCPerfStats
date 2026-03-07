#!/usr/bin/env python3
"""Load stats from existing .tar archives into the database. Workers read from
tar by (path, member_name) so the main process never holds file contents.

AI generated.
"""
import multiprocessing
import sys
import tarfile
import time
from functools import partial

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload import sync_timedb

thread_count = int(int(cfg.get_total_cores()) / 4)
if thread_count < 1:
  thread_count = 1


def _process_tar_member(lock, tar_path, member_name):
  """Open tar, extract one member, pass contents to add_stats_file_to_db.
  Keeps file contents only in the worker process."""
  print("extracting %s" % member_name)
  with tarfile.open(tar_path, 'r') as tar:
    member = tar.getmember(member_name)
    f = tar.extractfile(member)
    if f is None:
      return  # directories / unsupported entries
    content = f.read().decode('utf-8').split('\n')
  sync_timedb.add_stats_file_to_db(lock, member_name, content)


if __name__ == '__main__':

  sync_timedb.database_startup()

  tar_files = sys.argv[1:]

  start = time.time()

  # Build only (tar_path, member_name) pairs; no file contents in main process.
  tasks = []
  for tar_file_name in tar_files:
    with tarfile.open(tar_file_name, 'r') as archive_tar:
      print(archive_tar)
      for member_info in archive_tar.getmembers():
        if not member_info.isfile():
          continue
        tasks.append((tar_file_name, member_info.name))

  with multiprocessing.get_context('spawn').Pool(
      processes=thread_count) as pool:
    manager = multiprocessing.Manager()
    manager_lock = manager.Lock()
    worker = partial(_process_tar_member, manager_lock)
    # Distribute work; each worker loads only one member at a time.
    pool.starmap(worker, tasks)
