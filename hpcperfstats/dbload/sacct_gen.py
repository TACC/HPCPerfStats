"""Generate sacct commands to export job accounting data for a date range. Prints and optionally runs sacct for each day.

AI generated.
"""
import os
import sys
from datetime import datetime, timedelta

from dateutil.parser import parse

from hpcperfstats.dbload.date_utils import daterange

#import hpcperfstats.conf_parser as cfg

#acct_path = cfg.get_accounting_path()


if __name__ == "__main__":
  try:
    start_date = parse(sys.argv[1])
  except Exception:
    start_date = datetime.now()

  try:
    end_date = parse(sys.argv[2])
  except Exception:
    end_date = start_date + timedelta(1)

  for single_date in daterange(start_date, end_date):
    file_name = os.path.join("./", single_date.strftime("%Y-%m-%d")) + ".txt"
    sacct_command = (
        "/bin/sacct -a -s CANCELLED,COMPLETED,FAILED,NODE_FAIL,PREEMPTED,TIMEOUT,OUT_OF_MEMORY -P -X -S "
        + single_date.strftime("%Y-%m-%d") + " -E " +
        (single_date + timedelta(1)).strftime("%Y-%m-%d") +
        " -o jobid,jobidraw,cluster,partition,qos,account,group,gid,user,uid,submit,eligible,start,end,elapsed,exitcode,state,nnodes,ncpus,reqcpus,reqmem,reqtres,reqtres,timelimit,nodelist,jobname > "
        + file_name)
    print(sacct_command)
    os.system(sacct_command)
