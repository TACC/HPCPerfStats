#!/usr/bin/env python3
import psycopg2
from pgcopy import CopyManager
import os, sys, stat
import multiprocessing
import itertools
from functools import partial
from multiprocessing import Pool, get_context, Lock, set_start_method

from datetime import datetime, timedelta, date
import time, string
import subprocess
import pytz
import tarfile
import random
import uuid

#pandas.set_option('display.max_rows', 100)
from pandas import DataFrame, to_datetime, Timedelta, Timestamp, concat

from hpcperfstats.analysis.gen.utils import read_sql

import hpcperfstats.conf_parser as cfg

# archive toggle
should_archive = True

# DEBUG message toggle
DEBUG =  cfg.get_debug()

# Thread count for database loading and archival
thread_count = 8

# amount of concurrent pigz using thread_count*2 cores
archive_thread_count = 2

# How many days to process if run without any arguments
days_to_process = 5

# How many files to proccess and archive at once
chunk_size = 100

tgz_archive_dir = cfg.get_daily_archive_dir_path()


CONNECTION = cfg.get_db_connection_string()

amd64_pmc_eventmap = { 0x43ff03 : "FLOPS,W=48", 0x4300c2 : "BRANCH_INST_RETIRED,W=48", 0x4300c3: "BRANCH_INST_RETIRED_MISS,W=48", 
                       0x4308af : "DISPATCH_STALL_CYCLES1,W=48", 0x43ffae :"DISPATCH_STALL_CYCLES0,W=48" }

amd64_df_eventmap = { 0x403807 : "MBW_CHANNEL_0,W=48,U=64B", 0x403847 : "MBW_CHANNEL_1,W=48,U=64B", 0x403887 : "MBW_CHANNEL_2,W=48,U=64B" , 
                      0x4038c7 : "MBW_CHANNEL_3,W=48,U=64B", 0x433907 : "MBW_CHANNEL_4,W=48,U=64B", 0x433947 : "MBW_CHANNEL_5,W=48,U=64B", 
                      0x433987 : "MBW_CHANNEL_6,W=48,U=64B", 0x4339c7 : "MBW_CHANNEL_7,W=48,U=64B" }

intel_8pmc3_eventmap = { 0x4301c7 : 'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1',      0x4302c7 : 'FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1',
                         0x4304c7 : 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2', 0x4308c7 : 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4',
                         0x4310c7 : 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4', 0x4320c7 : 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8',
                         0x4340c7 : 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8', 0x4380c7 : 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16', 
                         "FIXED_CTR0" : 'INST_RETIRED,W=48', "FIXED_CTR1" : 'APERF,W=48', "FIXED_CTR2" : 'MPERF,W=48' }

intel_skx_imc_eventmap = {0x400304 : "CAS_READS,W=48", 0x400c04 : "CAS_WRITES,W=48", 0x400b01 : "ACT_COUNT,W=48", 0x400102 : "PRE_COUNT_MISS,W=48"}


exclude_types = ["ib", "ib_sw", "intel_skx_cha", "ps", "sysv_shm", "tmpfs", "vfs"]
#exclude_types = ["ib", "ib_sw", "intel_skx_cha", "proc", "ps", "sysv_shm", "tmpfs", "vfs"]




# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(lock, stats_file, stats_file_contents=None):

    hostname, create_time = stats_file.split('/')[-2:]

    if stats_file_contents is not None:
        lines = stats_file_contents
    else:
        try:
            with open(stats_file, 'r') as fd:
                lines = fd.readlines()
        except FileNotFoundError:
            print("Stats file disappeared: %s" % stats_file)
            return((stats_file, False))

    for l in lines:
        if not l:
            continue
        try:
            if l[0].isdigit():
                t, jid, host = l.split()
                break
        except:
            print("Error on this line: %s" % l)
    else:
        print("initial timestamp not found")

    timestamp = datetime.fromtimestamp(int(float(t)))

    with psycopg2.connect(CONNECTION) as conn:
        sql = "select distinct(time) from host_data where host = '{0}' and time >= '{1}'::timestamp - interval '48h' and time < '{1}'::timestamp + interval '72h' order by time;".format(hostname, timestamp) 
        times = [float(t.timestamp()) for t in read_sql(sql, conn)["time"].tolist()]
        itimes = [int(t) for t in times]
    
    
        # start reading stats data from file at first - 1 missing time
        start_idx = -1
        last_idx  = 0
        need_archival=True
        for i, line in enumerate(lines): 
            if not line or not line[0]: continue    
            if line[0].isdigit():
                t, jid, host = line.split()
                if jid == '-': continue                                     
    
                if (float(t) not in times) and (int(float(t)) not in itimes):
                    start_idx = last_idx
                    need_archival=False
                    break
                last_idx = i
    
        if start_idx == -1: 
            print("No missing timestamps found for %s" % stats_file)
            return((stats_file, True))
    
        # instrument the code to see what is actually proccessing in each file
        timestamps_found = 0
        counters_found = 0
        labels_found = 0
        unprocessable_lines = 0
        jobs_missing_found = 0
    
        schema = {}
        stats  = []
        proc_stats = [] #process stats
        insert = False
        timestamp_job_missing = False
        start = time.time()
        try:
            for i, line in enumerate(lines): 
                if not line or not line[0]: continue    
    
                if line[0].isalpha() and insert:
                    # Skip any data from a time stamp that doesn't have a jid associated
                    if timestamp_job_missing:
                        continue
                    typ, dev, vals = line.split(maxsplit = 2)        
                    counters_found += 1
                    vals = vals.split()
                    if typ in exclude_types: continue
    
                    # Mapping hardware counters to events 
                    if typ == "amd64_pmc" or typ == "amd64_df" or typ == "intel_8pmc3" or typ == "intel_skx_imc":
                        if typ == "amd64_pmc": eventmap = amd64_pmc_eventmap
                        if typ == "amd64_df": eventmap = amd64_df_eventmap
                        if typ == "intel_8pmc3": eventmap = intel_8pmc3_eventmap
                        if typ == "intel_skx_imc": eventmap = intel_skx_imc_eventmap
                        n = {}
                        rm_idx = []
                        schema_mod = []*len(schema[typ])
    
                        for idx, eve in enumerate(schema[typ]):
                    
                            eve = eve.split(',')[0]
                            if "CTL" in eve:
                                try:
                                    n[eve.lstrip("CTL")] = eventmap[int(vals[idx])]
                                except:
                                    n[eve.lstrip("CTL")] = "OTHER"                    
                                rm_idx += [idx]
                            
                            elif "FIXED_CTR" in eve: 
                                schema_mod += [eventmap[eve]]
    
                            elif "CTR" in eve:
                                schema_mod += [n[eve.lstrip("CTR")]]
                            else:
                                schema_mod += [eve]
                        
                        for idx in sorted(rm_idx, reverse = True): del vals[idx]
                        vals = dict(zip(schema_mod, vals))
                    elif typ == "proc":                                                                   
                         proc_name=(line.split()[1]).split('/')[0]                                        
                         proc_stats += [ { **tags2, "proc": proc_name } ]                                 
                         continue                                                                         
                    else:
                        # Software counters are not programmable and do not require mapping
                        vals = dict(zip(schema[typ], vals))
    
                    rec  =  { **tags, "type" : typ, "dev" : dev }   
    
                    for eve, val in vals.items():
                        eve = eve.split(',')
                        width = 64
                        mult = 1
                        unit = "#"
                        
                        for ele in eve[1:]:                    
                            if "W=" in ele: width = int(ele.lstrip("W="))
                            if "U=" in ele: 
                                ele = ele.lstrip("U=")
                                try:    mult = float(''.join(filter(str.isdigit, ele)))
                                except: pass
                                try:    unit = ''.join(filter(str.isalpha, ele))
                                except: pass
                        
                        stats += [ { **rec, "event" : eve[0], "value" : float(val), "wid" : width, "mult" : mult, "unit" : unit } ]
                    
                elif i >= start_idx and line[0].isdigit():
                    t, jid, host = line.split()
                    if jid == '-':
                        timestamp_job_missing = True
                        jobs_missing_found += 1
                        continue
                    timestamp_job_missing = False
                    timestamps_found += 1
                    insert = True
                    tags = { "time" : float(t), "host" : host, "jid" : jid }
                    tags2 = {"jid": jid, "host" : host}           
                elif line[0] == '!':
                    label, events = line.split(maxsplit = 1)
                    labels_found += 1
                    typ, events = label[1:], events.split()
                    schema[typ] = events 
                else:
                    unprocessable_lines += 1
            
        except Exception as e:
            print("error: process data failed: ", str(e)) 
            print("Possibly corrupt file: %s" % stats_file)
            return((stats_file, False))
    
        unique_entries = set(tuple(d.items()) for d in proc_stats)                                        
    
        # Convert set of tuples back to a list of dictionaries                                            
        proc_stats = [dict(entry) for entry in unique_entries]                                            
        proc_stats = DataFrame.from_records(proc_stats)                                                   
    
        stats = DataFrame.from_records(stats)
    
        if DEBUG:
            print("File Stats for %s:\n %s labels found, %s timestamps found,  %s counters found, %s unprocessable lines, %s timestamps missing jids" % (stats_file, labels_found, timestamps_found, counters_found, unprocessable_lines, jobs_missing_found))
    
        if stats.empty and proc_stats.empty: 
            if DEBUG:
                print("Unable to process stats file %s" % stats_file)
            return((stats_file, False))
    
        # Always drop the first timestamp. For new file this is just first timestamp (at random rotate time). 
        # For update from existing file this is timestamp already in database.
        
        # compute difference between time adjacent stats. if new file first na time diff is backfilled by second time diff
        stats["delta"] = (stats.groupby(["host", "type", "dev", "event"])["value"].diff())
    
        # correct stats for rollover and units (must be done before aggregation over devices)
        stats["delta"].mask(stats["delta"] < 0, 2**stats["wid"] + stats["delta"], inplace = True)
        stats["delta"] = stats["delta"] * stats["mult"]
        del stats["wid"], stats["mult"]
    
        # aggregate over devices
        stats = stats.groupby(["host", "jid", "type", "event", "unit", "time"]).sum().reset_index()            
        stats = stats.sort_values(by=["host", "type", "event", "time"])
        
        # compute average rate of change. 
        deltat = stats.groupby(["host", "type", "event"])["time"].diff()
        stats["arc"] = stats["delta"]/deltat
        stats["time"] = to_datetime(stats["time"], unit = 's').dt.tz_localize('UTC').dt.tz_convert('US/Central')
        
        # drop rows from first timestamp
        stats=stats.dropna()  #junjie DEBUG
        print("processing time for {0} {1:.1f}s".format(stats_file, time.time() - start))
    
        # bulk insertion using pgcopy
        sqltime = time.time()
    
    
        lock.acquire()
        mgr2 = CopyManager(conn, 'proc_data', proc_stats.columns)
        try:
            mgr2.copy(proc_stats.values.tolist())
        except Exception as e:
            if DEBUG:
                print("error in mrg2.copy: %s\nFile %s" %  (e, stats_file))
            conn.rollback()
            lock.release()
            copy_data_to_pgsql_individually(conn, proc_stats, 'proc_data')
        else: 
            conn.commit()
            lock.release()
    
    
    
        lock.acquire()
        mgr = CopyManager(conn, 'host_data', stats.columns)
        try:
            mgr.copy(stats.values.tolist())
        except Exception as e:
            if DEBUG:
                print("error in mrg.copy: " , str(e)) 
            conn.rollback()
            lock.release()
            need_archival = copy_data_to_pgsql_individually(conn, stats, 'host_data')
        else:
            conn.commit()
            lock.release()
    
    #print("sql insert time for {0} {1:.1f}s".format(stats_file, time.time() - sqltime))

    need_archival = True
    if DEBUG:
        print("File successfully added to DB")
    return((stats_file, need_archival))


def copy_data_to_pgsql_individually(conn, data, table):
    
    need_archival = True
    unique_violations = 0
    with conn.cursor() as curs:
        for row in data.values.tolist():

            sql_columns = ','.join(['"%s"' % value for value in data.columns.values])

            sql_insert = 'INSERT INTO "%s" (%s) VALUES ' % (table, sql_columns)
            sql_insert = sql_insert + "(" + ','.join(["%s" for i in row]) + ");"


            try:
                curs.execute(sql_insert, row)
            except psycopg2.errors.UniqueViolation as uv:
                # count for rows that already exist.
                unique_violations += 1
                conn.rollback()
            except Exception as e:
                print("error in single insert: ", e.pgcode, " ", str(e), "while executing", str(sql_insert))
                need_archival = False
                conn.rollback()
            else:
                conn.commit()
    if DEBUG:
        print("Existing Rows Found in DB: %s" % unique_violations)

    return need_archival

def archive_stats_files(archive_info):
    archive_fname, stats_files = archive_info
    archive_tar_fname = archive_fname[:-3]
    if not os.path.exists(archive_tar_fname):
        try:
            # If the file is missing, it will error, catch that error and continue
            print(subprocess.check_output(['/usr/bin/pigz', '-v', '-d', archive_fname]))
        except subprocess.CalledProcessError:
            pass

    existing_archive_file = {}
    if os.path.exists(archive_tar_fname):

        try:
            with tarfile.open(archive_tar_fname, 'r') as archive_tarfile:
                existing_archive_tarinfo = archive_tarfile.getmembers()

            for tar_member_data in existing_archive_tarinfo:
                existing_archive_file[tar_member_data.name] = tar_member_data.size

        except Exception as e:
             pass

    stats_files_to_tar = []
    for stats_fname_path in stats_files:
        fname_parts = stats_fname_path.split('/')
        
        if ((stats_fname_path[1:] in existing_archive_file.keys()) and 
           (tarfile.open('/tmp/test.tar', 'w').gettarinfo(stats_fname_path).size == existing_archive_file[stats_fname_path[1:]])):

            print("file %s found in archive, skipping" % stats_fname_path)
            continue
        stats_files_to_tar.append(stats_fname_path)

    tar_output = subprocess.check_output(['/bin/tar', 'uvf', archive_tar_fname] + stats_files_to_tar)
    if DEBUG:
        print(tar_output, flush=True)
    print("Archived: " + str(stats_files_to_tar))

    ### VERIFY TAR AND DELETE DATA IF IT IS ARCHIVED AND HAS THE SAME FILE SIZE
    with tarfile.open(archive_tar_fname, 'r') as archive_tarfile:
        existing_archive_tarinfo = archive_tarfile.getmembers()
        for tar_member_data in existing_archive_tarinfo:
            existing_archive_file[tar_member_data.name] = tar_member_data.size

        for stats_fname_path in stats_files:

            if ((stats_fname_path[1:] in existing_archive_file.keys()) and 
               (tarfile.open('/tmp/%s.tar' % uuid.uuid4(), 'w').gettarinfo(stats_fname_path).size == 
                   existing_archive_file[stats_fname_path[1:]])):
               print("removing stats file:" + stats_fname_path)
               os.remove(stats_fname_path)


    print(subprocess.check_output(['/usr/bin/pigz', '-f', '-8', '-v', '-p', str(thread_count*2), archive_tar_fname]), flush=True)

def database_startup():
    with psycopg2.connect(CONNECTION) as conn:
        if DEBUG:
            print("Postgresql server version: " + str(conn.server_version))

        with conn.cursor() as cur:

            cur.execute("SELECT pg_size_pretty(pg_database_size('{0}'));".format(cfg.get_db_name()))
            for x in cur.fetchall():
                print("Database Size:", x[0])
            if DEBUG:
                cur.execute("SELECT chunk_name,before_compression_total_bytes/(1024*1024*1024),after_compression_total_bytes/(1024*1024*1024) FROM chunk_compression_stats('host_data');")
                for x in cur.fetchall():
                    try: print("{0} Size: {1:8.1f} {2:8.1f}".format(*x))
                    except: pass
            else:
                print("Reading Chunk Data")



if __name__ == '__main__':

        database_startup()
        #################################################################

        try:
            startdate = datetime.strptime(sys.argv[1], "%Y-%m-%d")
        except: 
            startdate = datetime.combine(datetime.today(), datetime.min.time()) - timedelta(days = days_to_process)
        try:
            enddate   = datetime.strptime(sys.argv[2], "%Y-%m-%d")
        except:
            enddate = startdate + timedelta(days = days_to_process)

        if (len(sys.argv) > 1):  
            if sys.argv[1] == 'all':
                startdate = 'all'
                enddate = datetime.combine(datetime.today(), datetime.min.time()) - timedelta(days = days_to_process + 1)


        print("###Date Range of stats files to ingest: {0} -> {1}####".format(startdate, enddate))
        #################################################################

        # Parse and convert raw stats files to pandas dataframe
        start = time.time()
        directory = cfg.get_archive_dir_path()

        stats_files = []
        for entry in os.scandir(directory):
            if entry.is_file() or not (entry.name.startswith("c") or entry.name.startswith("v")): continue
            for stats_file in os.scandir(entry.path):
                if not stats_file.is_file() or stats_file.name.startswith('.'): continue
                if stats_file.name.startswith("current"): continue
                fdate=None
                try:
                    ### different ways to define the date of the file: use timestamp or use the time of the last piece of data
                    # based on filename
                    name_fdate = datetime.fromtimestamp(int(stats_file.name))

                    # timestamp of rabbitmq modify
                    mtime_fdate = datetime.fromtimestamp(int(os.path.getmtime(stats_file.path)))

                    fdate=mtime_fdate
                except Exception as e:
                       print("error in obtaining timestamp of raw data files: ", str(e))
                       continue
                if startdate == 'all':
                    if fdate > enddate: continue
                    stats_files += [stats_file.path]
                    continue

                if  fdate <= startdate - timedelta(days = 1) or fdate > enddate: continue
                stats_files += [stats_file.path]


        # sort files by oldest first, not based on the node (default os.scandir)
        stats_files.sort(key = lambda x:x.split('/')[-1])
        print("Number of host stats files to process = ", len(stats_files))

        with multiprocessing.get_context('spawn').Pool(processes = archive_thread_count) as archive_pool:
            archive_job = None
            # Process and archive chunk_size files before continuing to process more
            for i in range(int(len(stats_files)/chunk_size) + 1):
                function_time = time.time()
                j = i + 1
                if DEBUG:
                    print("Begining Chunk(%s) #%s Processing" % (chunk_size, i))
    
                ar_file_mapping = {}
                files_to_be_archived = []
    
                try:
                   stats_files[j*chunk_size]
                   stats_files_chunk = stats_files[i*chunk_size:j*chunk_size:]
                except IndexError:
                    stats_files_chunk = stats_files[i*chunk_size:]
    
                print("%s files per chunk" % chunk_size)
    
                with multiprocessing.get_context('spawn').Pool(processes = thread_count) as pool:
                    manager = multiprocessing.Manager()
                    manager_lock = manager.Lock()
                    add_stats_file = partial(add_stats_file_to_db, manager_lock)
                    k = 0
                    for stats_fname, need_archival in pool.imap_unordered(add_stats_file, stats_files_chunk):
                        k += 1
                        if should_archive and need_archival: files_to_be_archived.append(stats_fname)
                        print("chunk %s: completed file %s out of %s\n" % (i, k, chunk_size), flush=True)
    
                print("loading time", time.time() - start)
             
                for stats_fname in files_to_be_archived:
                   stats_start = open(stats_fname, 'r').readlines(8192) # grab first 8k bytes
                   archive_fname = ''
                   for line in stats_start:
                       if line[0].isdigit():
                           t, jid, host = line.split()
                           file_date = datetime.fromtimestamp(float(t))
                           archive_fname =  os.path.join(tgz_archive_dir, file_date.strftime("%Y-%m-%d.tar.gz"))
                           break
    
                   if file_date.date == datetime.today().date:
                       continue
    
                   if not archive_fname:
                       print("Unable to find first timestamp in %s, skipping archiving" % stats_fname)
                       continue
                   if archive_fname not in ar_file_mapping: ar_file_mapping[archive_fname] = []
                   ar_file_mapping[archive_fname].append(stats_fname)
    
                # skip first iteration, on first there will be no archive_job
                if i:
                    if DEBUG:
                        print("Checking/waiting for background archival proccesses")
    
                    # Wait until last archive_job is complete before starting another one
                    for stats_files_archived in archive_job.get():
                        print("[{0:.1f}%] completed".format(100*stats_files.index(stats_fname)/len(stats_files)), end = "\r", flush=True)
    
                if DEBUG:
                    print("files to be archived: %s" % ar_file_mapping)
                
                archive_job = archive_pool.map_async(archive_stats_files, list(ar_file_mapping.items()))
    
                print("Archival running in the background")
    
            archive_job.get()
    
    
    
            if DEBUG:
                print("sync_timedb sleeping")
    
            time.sleep(900)
    
            if DEBUG:
                print("sync_timedb finished")
           

