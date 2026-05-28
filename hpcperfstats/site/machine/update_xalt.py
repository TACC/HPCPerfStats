#!/usr/bin/env python
"""XALT data enrichment script. Uses Django ORM for xalt DB (run, join_run_object, lib). Note: Current job_data has no exe, exec_path, cwd, threads or a link to Libraries. XALT run data is still queried in views. This script optionally iterates by date and logs xalt runs for jobs.

"""
import sys
from datetime import datetime

from hpcperfstats.django_bootstrap import ensure_django

ensure_django()

from hpcperfstats.dbload.date_utils import daterange, parse_start_end_dates
from hpcperfstats.print_utils import log_print
from hpcperfstats.site.machine.models import job_data
from hpcperfstats.site.xalt.models import run


def run_update_xalt_for_range(start, end, log_fn=log_print):
  """Iterate job_data by end date and log XALT exec_path lines (for operators)."""
  for date in daterange(start, end, inclusive_end=True):
    directory = date.strftime("%Y-%m-%d")
    log_fn(directory)
    jobs_on_date = job_data.objects.filter(end_time__date=date).values_list(
        "jid", flat=True)
    for jid in jobs_on_date:
      runs = list(run.objects.using("xalt").filter(job_id=jid))
      if not runs:
        continue
      for r in runs:
        if "usr" in r.exec_path.split("/"):
          continue
        log_fn("  jid=%s exec_path=%s" % (jid, r.exec_path))


def main(argv=None):
  from hpcperfstats.process_title import set_script_process_title

  set_script_process_title()
  argv = argv if argv is not None else sys.argv
  default_start = datetime.now()
  default_end = default_start
  start, end = parse_start_end_dates(argv, default_start, default_end)
  run_update_xalt_for_range(start, end)


if __name__ == "__main__":
  main()
