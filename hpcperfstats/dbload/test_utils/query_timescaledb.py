#!/usr/bin/env python3
import psycopg
import os, sys, stat
from multiprocessing import Pool
from datetime import datetime, timedelta
import time, string
from pandas import DataFrame, to_datetime, Timedelta, concat, read_sql
#import pandas
#pandas.set_option('display.max_rows', 100)


CONNECTION = "dbname=hpcperfstats user=postgres port=5433"

conn = psycopg.connect(CONNECTION)
print(conn.server_version)
cur = conn.cursor()

#print(read_sql("select distinct(jid) from host_data;", conn))
jid = sys.argv[1]


qtime = time.time()
cur.execute("DROP VIEW IF EXISTS job_detail CASCADE;")
cur.execute("create temp view job_detail as select * from host_data where jid = '{0}' order by host, time;".format(jid))
print(read_sql("select count(distinct(host)) as nodes from job_detail;", conn))
print("query time: {0:.1f}".format(time.time()-qtime))


df = DataFrame()
df = read_sql("select jid, host, time, 1e-9*sum(arc) as flops from job_detail where event in ('FP_ARITH_INST_RETIRED_SCALAR_DOUBLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE') group by jid, host, time;", conn)

df["mbw"] = read_sql("select 64*sum(arc)/(1024*1024*1024) from job_detail where event in ('CAS_READS', 'CAS_WRITES') group by jid, host, time;", conn)
df["ibbw"] = read_sql("select sum(arc)/(1024*1024) from job_detail where event in ('port_rcv_data', 'port_xmit_data') group by jid, host, time;", conn)
df["lbw"] = read_sql("select sum(arc)/(1024*1024) from job_detail where event in ('read_bytes', 'write_bytes') group by jid, host, time;", conn)


df["mem"] = read_sql("select value/(1024*1024) as mem from job_detail where type = 'mem' and event in ('MemUsed') order by jid, host, time;", conn)
df["cpu"] = read_sql("select 0.01*sum(arc) as cpu from job_detail where event in ('user', 'system', 'nice') group by jid, host, time;", conn)
df["instr"] = read_sql("select sum(diff) from job_detail where event in ('INST_RETIRED') group by jid, host, time;", conn)
df["mcycles"] = read_sql("select sum(diff) from job_detail where event in ('MPERF') group by jid, host, time;", conn)
df["acycles"] = read_sql("select sum(diff) from job_detail where event in ('APERF') group by jid, host, time;", conn)
df["freq"]  = 2.7*(df["acycles"]/df["mcycles"]).fillna(0)
df["cpi"]  = (df["acycles"]/df["instr"]).fillna(0)

del df["instr"], df["mcycles"], df["acycles"]

print(df)

conn.close()
