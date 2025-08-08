#!/usr/bin/env python3
from hpcperfstats.dbload import sync_timedb
import sys
import time
import tarfile
import multiprocessing
from functools import partial

thread_count = 8

if __name__ == '__main__':

        sync_timedb.database_startup()

        tar_files = sys.argv[1:]

        start = time.time()

        stats_files = []

        with multiprocessing.get_context('spawn').Pool(processes = thread_count) as pool:
            manager = multiprocessing.Manager()
            manager_lock = manager.Lock()
            add_stats_file = partial(sync_timedb.add_stats_file_to_db, manager_lock)
            for tar_file_name in tar_files:
                procs = []
                with tarfile.TarFile.open(tar_file_name) as archive_tar:
                    print(archive_tar)
                    for member_info in archive_tar.getmembers():
                        file_name = member_info.name.split('/')[-1]
                        print("extracting %s" % member_info.name)
                        file_contents = archive_tar.extractfile(member_info).read().decode("utf-8").split("\n")
                        procs.append(pool.apply_async(add_stats_file, (member_info.name, file_contents)))

                for i in procs:
                    i.get()
