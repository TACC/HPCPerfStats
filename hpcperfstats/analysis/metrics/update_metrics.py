#!/usr/bin/env python
"""Update metrics_data for jobs ending on a given date. Filters by runtime, optionally skips jobs that already have metrics, runs Metrics().run(jobs_list).

"""
import os
import sys
import time
from datetime import datetime, timedelta
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections
from django.db.models import Count, Q
from django.db.utils import OperationalError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.shutdown_utils import (
    register_sigterm_handler,
    shutdown_requested,
    sleep_until_shutdown,
)
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
  """Yield (pk_list, total_so_far) in chunks. Uses a new query per chunk to avoid
  long-lived cursors and connection timeouts (psycopg 'connection is closed').
  """
  total = 0
  last_pk = 0
  while True:
    chunk = list(
        queryset.filter(pk__gt=last_pk)
        .order_by("pk")
        .values_list("pk", flat=True)[:chunk_size]
    )
    if not chunk:
      break
    last_pk = chunk[-1]
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
    log_print(
        "Total jobs {0} for date {1}".format(
            total_jobs, date.strftime("%Y-%m-%d")
        )
    )

    metrics_manager = metrics.Metrics()
    log_print(
        "Compute for following metrics for date {0} on {1} jobs".format(
            date, total_jobs
        )
    )
    for name in metrics_manager.simple_metrics_list:
      log_print(name)
    for name in metrics_manager.complex_metrics_list:
      log_print(name)

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
      log_print("jobs that don't have data after run (count): {0}".format(remaining))

  try:
    _run()
  except OperationalError:
    close_old_connections()
    _run()


def main(argv=None, sleep_after=True):
  """Entry point for updating metrics_data for a date or date range.

  When invoked as a script, argv defaults to sys.argv. Management commands
  can pass a custom argv list (e.g. parsed from options). If sleep_after is
  True, the function sleeps 3600s at the end (to match legacy usage).
  """
  if argv is None:
    argv = sys.argv

  #################################################################
  default_start = datetime.combine(datetime.today(), datetime.min.time())
  startdate, enddate = parse_start_end_dates(argv, default_start, default_start)

  log_date_range("metrics to update", startdate, enddate)
  #################################################################

  date = startdate
  all_dates = []
  while date <= enddate:
    all_dates.append(date)
    date += timedelta(days=1)

  sorted(all_dates, reverse=True)
  log_print(all_dates)
  for d in all_dates:
    if shutdown_requested[0]:
      break
    result = update_metrics(d)
    log_print(result)

  if sleep_after and not shutdown_requested[0]:
    # Close DB connections before long sleep to avoid idle connections.
    close_old_connections()
    connections.close_all()
    sleep_until_shutdown(3600)


if __name__ == "__main__":
  register_sigterm_handler("Received SIGTERM, will exit after current work")
  main()
  if shutdown_requested[0]:
    sys.exit(143)
