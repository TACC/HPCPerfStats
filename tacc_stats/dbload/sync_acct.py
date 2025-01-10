#!/usr/bin/env python3
import os,sys,time

# Append your local repository path here:
# sys.path.append("/home/sg99/tacc_stats")

from datetime import timedelta, datetime

import psycopg2
from pgcopy import CopyManager

import pandas as pd
from pandas import read_csv, to_datetime, to_timedelta, concat

import hostlist

from tacc_stats.analysis.gen.utils import read_sql

import tacc_stats.conf_parser as cfg


def sync_acct(acct_file, date_str):
    print(date_str)
    conn = psycopg2.connect(CONNECTION)
    edf = read_sql("select jid from job_data where date(end_time) = '{0}' ".format(date_str), conn)
    print("Total number of existing entries:", edf.shape[0])

# Junjie: ensure job name is treated as str. 
    data_types = {8: str}
 
    df = read_csv(acct_file, sep='|')
    df.rename(columns = {'JobID': 'jid', 'User': 'username', 'Account' : 'account', 'Start' : 'start_time', 
                         'End' : 'end_time', 'Submit' : 'submit_time', 'Partition' : 'queue', 
                         'Timelimit' : 'timelimit', 'JobName' : 'jobname', 'State' : 'state', 
                         'NNodes' : 'nhosts', 'ReqCPUS' : 'ncores', 'NodeList' : 'host_list'}, inplace = True)
    df["jid"] = df["jid"].apply(str)

 # Junjie: in case newer slurm gives "None" time for unstarted jobs.  Older slurm prints start_time=end_time=cancelled_time.
 ### >>>
    df['start_time'].replace('^None$', pd.NA, inplace=True, regex=True)
    df['start_time'].replace('^Unknown$', pd.NA, inplace=True, regex=True)
    df['start_time'].fillna(df['end_time'], inplace=True)
 #### <<<

    df["start_time"] = to_datetime(df["start_time"]).dt.tz_localize('US/Central')
    df["end_time"] = to_datetime(df["end_time"]).dt.tz_localize('US/Central')
    df["submit_time"] = to_datetime(df["submit_time"]).dt.tz_localize('US/Central')

    df["runtime"] = to_timedelta(df["end_time"] - df["start_time"]).dt.total_seconds()    
    df["timelimit"] = df["timelimit"].str.replace('-', ' days ')
    df["timelimit"] = to_timedelta(df["timelimit"]).dt.total_seconds()
                         
    df["host_list"] = df["host_list"].apply(hostlist.expand_hostlist)
    df["node_hrs"] = df["nhosts"]*df["runtime"]/3600.

    df = df[~df["jid"].isin(edf["jid"])]
    print("Total number of new entries:", df.shape[0])


    mgr = CopyManager(conn, 'job_data', df.columns)
    mgr.copy(df.values.tolist())
    conn.commit()
    conn.close()
    
if __name__ == "__main__":
        CONNECTION = cfg.get_db_connection_string()
        conn = psycopg2.connect(CONNECTION)

#    while True:

        #################################################################
        try:
            startdate = datetime.strptime(sys.argv[1], "%Y-%m-%d")
        except:
         startdate = datetime.combine(datetime.today(), datetime.min.time())
        try:
            enddate   = datetime.strptime(sys.argv[2], "%Y-%m-%d")
        except:
            enddate = startdate + timedelta(days = 1)

        print("###Date Range of job files to ingest: {0} -> {1}####".format(startdate, enddate))
        #################################################################

        # Parse and convert raw stats files to pandas dataframe
        start = time.time()
        directory = cfg.get_accounting_path()

        
        while startdate <= enddate:            
            for entry in os.scandir(directory):
                if not entry.is_file(): continue
                if entry.name.startswith(str(startdate.date())):
                    print(entry.path)
                    sync_acct(entry.path, str(startdate.date()))
            startdate += timedelta(days=1)
        print("loading time", time.time() - start)

        time.sleep(900)
