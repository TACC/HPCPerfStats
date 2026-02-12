#!/usr/bin/env python3
import os,sys,time

from datetime import timedelta, datetime

import psycopg2
from pgcopy import CopyManager

import pandas as pd
from pandas import read_csv, to_datetime, to_timedelta, concat

import hostlist

from hpcperfstats.analysis.gen.utils import read_sql

import hpcperfstats.conf_parser as cfg
from django.conf import settings

settings.configure()

CONNECTION = cfg.get_db_connection_string()

local_timezone = cfg.get_timezone()

def sync_acct(acct_file, jobs_in_db):

    # Junjie: ensure job name is treated as str.
    data_types = {8: str}

    columns_to_read = ['JobID', 'User', 'Account','Start', 'End', 'Submit', 'Partition',
                       'Timelimit', 'JobName', 'State', 'NNodes', 'ReqCPUS', 'NodeList']
    df = read_csv(acct_file, sep='|')

    # cycle through collumns so we can remove those we don't want to import.
    for c in df:
        if c in columns_to_read:
            continue
        df = df.drop(columns=c)

    df = df.rename(columns = {'JobID': 'jid', 'User': 'username', 'Account' : 'account', 'Start' : 'start_time',
                         'End' : 'end_time', 'Submit' : 'submit_time', 'Partition' : 'queue',
                         'Timelimit' : 'timelimit', 'JobName' : 'jobname', 'State' : 'state',
                         'NNodes' : 'nhosts', 'ReqCPUS' : 'ncores', 'NodeList' : 'host_list'})
                         
    df = df[~df["jid"].isin(jobs_in_db["jid"])]
    df["jid"] = df["jid"].apply(str)

    restricted_queue_keywords = cfg.get_restricted_queue_keywords()

    df_len = len(df)
    queue_col_index = df.columns.get_loc("queue")
    job_id_col_index = df.columns.get_loc("jid")

    restricted_job_ids = []

    restricted_df_indices = []

    for i in range(df_len):
        for q in restricted_queue_keywords:
            if q in df.iloc[i,queue_col_index]:
                if(settings.DEBUG):
                    restricted_job_ids.append(df.iloc[i,job_id_col_index])
                    restricted_df_indices.append(i)

    df = df.drop(restricted_df_indices)

    if len(restricted_job_ids) > 0:
        print("The following jobs are restricted and will be skipped: "+ str(restricted_job_ids))

    # In case newer slurm gives "None" time for unstarted jobs.  Older slurm prints start_time=end_time=cancelled_time.
    df['start_time'] = df['start_time'].replace('^None$', pd.NA, regex=True)
    df['start_time'] = df['start_time'].replace('^Unknown$', pd.NA, regex=True)
    df['start_time'] = df['start_time'].fillna(df['end_time'])

    df["start_time"] = to_datetime(df["start_time"]).dt.tz_localize(local_timezone, ambiguous=True)
    df["end_time"] = to_datetime(df["end_time"]).dt.tz_localize(local_timezone, ambiguous=True)
    df["submit_time"] = to_datetime(df["submit_time"]).dt.tz_localize(local_timezone, ambiguous=True)

    df["runtime"] = to_timedelta(df["end_time"] - df["start_time"]).dt.total_seconds()
    df["timelimit"] = df["timelimit"].str.replace('-', ' days ')
    df["timelimit"] = to_timedelta(df["timelimit"]).dt.total_seconds()

    df["host_list"] = df["host_list"].apply(hostlist.expand_hostlist)
    df["node_hrs"] = df["nhosts"]*df["runtime"]/3600.



    print("Total number of new entries:", df.shape[0])


    with psycopg2.connect(CONNECTION) as conn:
        mgr = CopyManager(conn, 'job_data', df.columns)
        try:
            mgr.copy(df.values.tolist())
        except Exception as e:
            print("error in mrg.copy: " , str(e))
            conn.rollback()
            copy_data_to_pgsql_individually(conn, df, 'job_data')
        else:
            conn.commit()


def copy_data_to_pgsql_individually(conn, data, table):
    with conn.cursor() as curs:
        for row in data.values.tolist():

            sql_columns = ','.join(['"%s"' % value for value in data.columns.values])

            sql_insert = 'INSERT INTO "%s" (%s) VALUES ' % (table, sql_columns)
            sql_insert = sql_insert + "(" + ','.join(["%s" for i in row]) + ");"


            try:
                curs.execute(sql_insert, row)
            except psycopg2.errors.UniqueViolation as uv:
                conn.rollback()
            except Exception as e:
                print("error in single insert: ", e.pgcode, " ", str(e), "while executing", str(sql_insert))
                conn.rollback()
            else:
                conn.commit()

if __name__ == "__main__":
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


        searchdate = startdate - timedelta(days = 2)
        with psycopg2.connect(CONNECTION) as conn:
            jobs_in_db = read_sql("select jid from job_data where date(end_time) >=  '{0}' ".format(searchdate.date()), conn)
        
        print("Jobs found in DB in this date range: %s" % jobs_in_db.shape[0])

        while startdate <= enddate:
            for entry in os.scandir(directory):
                if not entry.is_file(): continue
                if entry.name.startswith(str(startdate.date())):
                    print(entry.path)
                    try:
                        sync_acct(entry.path, jobs_in_db)
                    except Exception as e:
                        print("Unable to load file: %s" % entry.path)
            startdate += timedelta(days=1)
        print("loading time", time.time() - start)

        time.sleep(900)
