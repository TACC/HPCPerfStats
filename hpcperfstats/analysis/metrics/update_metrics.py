#!/usr/bin/env python
"""Update metrics_data for jobs ending on a given date. Filters by runtime, optionally skips jobs that already have metrics, runs Metrics().run(jobs_list).

"""
import os
import sys
import time
from datetime import datetime, timedelta
from itertools import islice

from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections
from django.db.models import Count, Q
from django.db.utils import OperationalError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.site.machine.models import job_data

DEBUG = cfg.get_debug()

# Process jobs in chunks to bound memory; full job rows are not all held at once.
CHUNK_SIZE = 500


def _jobs_queryset(date, min_time, rerun):
  """Base queryset: jobs ending on date with runtime >= min_time."""
  qs = job_data.objects.filter(end_time__date=date.date()).exclude(
      runtime__lt=min_time)
  if rerun:
    return qs
  # Filter in DB: only jobs with no metrics or with any null value
  return qs.annotate(
      md_count=Count("metrics_data_set"),
      null_count=Count(
          "metrics_data_set", filter=Q(metrics_data_set__value__isnull=True)
      ),
  ).filter(Q(md_count=0) | Q(null_count__gt=0))


def _iter_chunked_pks(queryset, chunk_size):
  """Yield (pk_list, total_so_far) in chunks without loading full rows."""
  pk_iter = queryset.values_list("pk", flat=True).iterator(chunk_size=chunk_size)
  total = 0
  while True:
    chunk = list(islice(pk_iter, chunk_size))
    if not chunk:
      break
    total += len(chunk)
    yield chunk, total


def update_metrics(date, rerun=False):
  """Compute and persist metrics for all jobs ending on date (runtime >= min_time). If not rerun, skip jobs that already have metrics. Uses metrics.Metrics().run(jobs_list).

  Memory-optimized: filters in DB, processes in chunks, no full-list cache.
  """
  close_old_connections()
  min_time = 300

  def _run():
    qs = _jobs_queryset(date, min_time, rerun)
    total_jobs = qs.count()
    print(
        "Total jobs {0} for date {1}".format(
            total_jobs, date.strftime("%Y-%m-%d")
        )
    )

    metrics_manager = metrics.Metrics()
    print(
        "Compute for following metrics for date {0} on {1} jobs".format(
            date, total_jobs
        )
    )
    for name in metrics_manager.simple_metrics_list:
      print(name)
    for name in metrics_manager.complex_metrics_list:
      print(name)

    processed = 0
    for pk_chunk, _ in _iter_chunked_pks(qs, CHUNK_SIZE):
      jobs_chunk = list(
          job_data.objects.filter(pk__in=pk_chunk).prefetch_related(
              "metrics_data_set"
          )
      )
      metrics_manager.run(jobs_chunk)
      processed += len(jobs_chunk)
      del jobs_chunk  # release before next chunk
      close_old_connections()

    if DEBUG:
      close_old_connections()
      qs_after = _jobs_queryset(date, min_time, rerun=False)
      remaining = qs_after.count()
      print("jobs that don't have data after run (count): {0}".format(remaining))

  try:
    _run()
  except OperationalError:
    close_old_connections()
    _run()


if __name__ == "__main__":

  #################################################################
  default_start = datetime.combine(datetime.today(), datetime.min.time())
  startdate, enddate = parse_start_end_dates(sys.argv, default_start, default_start)

  log_date_range("metrics to update", startdate, enddate)
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
