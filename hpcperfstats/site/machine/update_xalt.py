#!/usr/bin/env python
"""XALT data enrichment script. Uses Django ORM for xalt DB (run, join_run_object, lib). Note: Current job_data has no exe/exec_path/cwd/threads; XALT run data is still queried in views. This script optionally iterates by date and logs xalt runs for jobs.

AI generated.
"""
import os
import sys
from datetime import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")

import django
django.setup()

from hpcperfstats.dbload.date_utils import daterange, parse_start_end_dates
from hpcperfstats.site.machine.models import job_data
from hpcperfstats.site.xalt.models import join_run_object, lib, run

default_start = datetime.now()
default_end = default_start
start, end = parse_start_end_dates(sys.argv, default_start, default_end)

# Current job_data model does not have exe, exec_path, cwd, threads or a link to Libraries.
# XALT run data is still available via run.objects.using('xalt').filter(job_id=jid) in views.
# Optionally, iterate by date and log xalt runs for jobs in job_data for that date:
for date in daterange(start, end, inclusive_end=True):
  directory = date.strftime("%Y-%m-%d")
  print(directory)
  # Jobs ending on this date (by day)
  jobs_on_date = job_data.objects.filter(end_time__date=date).values_list(
      "jid", flat=True)
  for jid in jobs_on_date:
    runs = list(run.objects.using("xalt").filter(job_id=jid))
    if not runs:
      continue
    for r in runs:
      if "usr" in r.exec_path.split("/"):
        continue
      print("  jid=%s exec_path=%s" % (jid, r.exec_path))
