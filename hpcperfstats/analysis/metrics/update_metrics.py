#!/usr/bin/env python
"""Update metrics_data for jobs ending on a given date. Filters by runtime, optionally skips jobs that already have metrics, runs Metrics().run(jobs_list).

AI generated.
"""
import multiprocessing
import os
import sys
import time
from datetime import datetime, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")

import django
django.setup()

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.dbload.date_utils import parse_start_end_dates
from hpcperfstats.site.machine.cache_utils import (
    KEY_UPDATE_METRICS_JOBS,
    cached_orm,
    TIMEOUT_SHORT,
)
from hpcperfstats.site.machine.models import job_data

DEBUG = cfg.get_debug()


def update_metrics(date, rerun=False):
  """Compute and persist metrics for all jobs ending on date (runtime >= min_time). If not rerun, skip jobs that already have metrics. Uses metrics.Metrics().run(jobs_list).

    AI generated.
    """
  min_time = 300
  date_key = date.date().isoformat()

  def _jobs_fn():
    return list(
        job_data.objects.filter(end_time__date=date.date())
        .exclude(runtime__lt=min_time)
        .prefetch_related("metrics_data_set"))

  jobs_list = cached_orm(
      f"{KEY_UPDATE_METRICS_JOBS}:{date_key}",
      TIMEOUT_SHORT,
      _jobs_fn,
  ) or []
  print("Total jobs {0}".format(len(jobs_list)) + " for date " +
        date.strftime("%Y-%m-%d"))

  if not rerun:
    jobs_list = [
        job for job in jobs_list if not job.metrics_data_set.all().exists() or
        job.metrics_data_set.all().filter(value__isnull=True).count() > 0
    ]

  if DEBUG:
    print("jobs that don't have data before run:")
    print(jobs_list)
  # Set up metric computation manager
  metrics_manager = metrics.Metrics()

  print("Compute for following metrics for date {0} on {1} jobs".format(
      date, len(jobs_list)))
  for name in metrics_manager.simple_metrics_list:
    print(name)
  for name in metrics_manager.complex_metrics_list:
    print(name)

  metrics_manager.run(jobs_list)

  if DEBUG:
    jobs_list_fresh = list(
        job_data.objects.filter(end_time__date=date.date())
        .exclude(runtime__lt=min_time)
        .prefetch_related("metrics_data_set"))
    jobs_list_fresh = [
        job for job in jobs_list_fresh
        if not job.metrics_data_set.all().exists() or
        job.metrics_data_set.all().filter(value__isnull=True).count() > 0
    ]
    print("jobs that don't have data after run:")
    print(jobs_list_fresh)


if __name__ == "__main__":

  #################################################################
  default_start = datetime.combine(datetime.today(), datetime.min.time())
  startdate, enddate = parse_start_end_dates(sys.argv, default_start, default_start)

  print("###Date Range of metrics to update: {0} -> {1}####".format(
      startdate, enddate))
  #################################################################

  date = startdate
  all_dates = []
  while date <= enddate:
    all_dates.append(date)
    date += timedelta(days=1)

  print(all_dates)
  for result in map(update_metrics, all_dates):
    print(result)

#update_metrics(date, rerun = False)

  time.sleep(3600)
