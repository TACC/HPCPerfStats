#!/usr/bin/env python3
"""Ad-hoc script to query host_data for a job via Django ORM/connection. Uses Django DB connection instead of raw psycopg. Requires DJANGO_SETTINGS_MODULE.

AI generated.
"""
import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")

import django
django.setup()

from pandas import DataFrame

from django.db import connection


def run_sql(sql, params=None):
  """Execute SQL and return a DataFrame (columns from cursor.description).

    AI generated.
    """
  with connection.cursor() as cur:
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
  return DataFrame(rows, columns=cols) if cols else DataFrame()


if __name__ == "__main__":
  if len(sys.argv) < 2:
    print("Usage: query_timescaledb.py <jid>")
    sys.exit(1)

  jid = sys.argv[1]
  print(connection.connection.server_version if hasattr(connection, "connection"
                                                        ) else "N/A")

  qtime = time.time()
  with connection.cursor() as cur:
    cur.execute("DROP VIEW IF EXISTS job_detail CASCADE;")
    cur.execute(
        "CREATE TEMP VIEW job_detail AS SELECT * FROM host_data WHERE jid = %s ORDER BY host, time;",
        [jid],
    )
  df = run_sql("SELECT count(distinct(host)) AS nodes FROM job_detail;")
  print(df)
  print("query time: {0:.1f}".format(time.time() - qtime))

  df = run_sql("""SELECT jid, host, time, 1e-9*sum(arc) AS flops FROM job_detail
           WHERE event IN ('FP_ARITH_INST_RETIRED_SCALAR_DOUBLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE',
                          'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE')
           GROUP BY jid, host, time;""")
  df["mbw"] = run_sql(
      "SELECT 64*sum(arc)/(1024*1024*1024) FROM job_detail WHERE event IN ('CAS_READS', 'CAS_WRITES') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["ibbw"] = run_sql(
      "SELECT sum(arc)/(1024*1024) FROM job_detail WHERE event IN ('port_rcv_data', 'port_xmit_data') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["lbw"] = run_sql(
      "SELECT sum(arc)/(1024*1024) FROM job_detail WHERE event IN ('read_bytes', 'write_bytes') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["mem"] = run_sql(
      "SELECT value/(1024*1024) AS mem FROM job_detail WHERE type = 'mem' AND event IN ('MemUsed') ORDER BY jid, host, time;"
  ).iloc[:, 0]
  df["cpu"] = run_sql(
      "SELECT 0.01*sum(arc) AS cpu FROM job_detail WHERE event IN ('user', 'system', 'nice') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["instr"] = run_sql(
      "SELECT sum(delta) FROM job_detail WHERE event IN ('INST_RETIRED') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["mcycles"] = run_sql(
      "SELECT sum(delta) FROM job_detail WHERE event IN ('MPERF') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["acycles"] = run_sql(
      "SELECT sum(delta) FROM job_detail WHERE event IN ('APERF') GROUP BY jid, host, time;"
  ).iloc[:, 0]
  df["freq"] = 2.7 * (df["acycles"] / df["mcycles"]).fillna(0)
  df["cpi"] = (df["acycles"] / df["instr"]).fillna(0)
  del df["instr"], df["mcycles"], df["acycles"]

  print(df)
