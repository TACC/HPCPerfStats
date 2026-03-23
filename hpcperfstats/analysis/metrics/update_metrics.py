#!/usr/bin/env python
"""Update metrics_data for jobs ending on each day in a date range.

Filters by runtime, optionally skips jobs that already have metrics, runs
Metrics().run(jobs_list). With no CLI date arguments, processes the last seven
calendar days through today.

"""
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections
from django.db.models import Count, Q
from django.db.utils import OperationalError, DatabaseError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.site.machine.models import job_data

DEBUG = cfg.get_debug()

# Process jobs in chunks to bound memory; full job rows are not all held at once.
CHUNK_SIZE = 500

# When argv has no start/end dates, process this many calendar days ending today.
DEFAULT_METRICS_RANGE_DAYS = 7


def _today_datetime():
  """Local now for default date-range bounds (monkeypatch in tests)."""
  return datetime.today()


def _default_metrics_date_range():
  """Return (start, end) datetimes at local midnight: inclusive last N days through today."""
  end = datetime.combine(_today_datetime().date(), datetime.min.time())
  start = end - timedelta(days=DEFAULT_METRICS_RANGE_DAYS - 1)
  return start, end


def _shutdown_db_best_effort():
  """Close DB connections without failing shutdown."""
  try:
    close_old_connections()
    connections.close_all()
  except Exception:
    pass


def _install_sigterm_handler(exit_code=143):
  """Install SIGTERM handler that requests shutdown and exits."""
  sigterm_received = [False]

  previous_handler = signal.getsignal(signal.SIGTERM)

  def _sigterm_handler(signum, frame):
    sigterm_received[0] = True
    shutdown_requested[0] = True
    raise SystemExit(exit_code)

  signal.signal(signal.SIGTERM, _sigterm_handler)
  return previous_handler, sigterm_received, _sigterm_handler


def _notify_parent_if_sigterm(sigterm_received):
  """Send SIGCHLD back to parent when SIGTERM triggered."""
  if sigterm_received and sigterm_received[0]:
    send_sigchld_to_parent()


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
  """Yield (pk_list, total_so_far) in chunks, streaming PKs from the database.

  This implementation relies on Django's ``iterator`` with a server-side cursor
  so that we never materialise the full PK list in memory. We accumulate PKs
  into Python lists of at most ``chunk_size`` elements and yield each list
  together with the cumulative total seen so far.
  """
  total = 0
  current_chunk = []
  for pk in queryset.values_list("pk", flat=True).iterator(chunk_size=chunk_size):
    current_chunk.append(pk)
    if len(current_chunk) >= chunk_size:
      total += len(current_chunk)
      yield current_chunk, total
      current_chunk = []

  if current_chunk:
    total += len(current_chunk)
    yield current_chunk, total


def update_metrics(date, rerun=False):
  """Compute and persist metrics for all jobs ending on date (runtime >= min_time). If not rerun, skip jobs that already have metrics. Uses metrics.Metrics().run(jobs_list).

  Memory-optimized: filters in DB, processes in chunks, no full-list cache.
  """
  close_old_connections()
  min_time = 300

  def _run():
    """
    Inner function so we can cleanly retry the whole operation if the database
    connection is dropped or becomes unsynchronised. We deliberately avoid
    closing connections inside the per‑chunk loop to prevent leaving Django's
    ORM holding onto a cursor/connection that has just been closed, which can
    manifest as psycopg 'lost synchronization with server' errors.
    """
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
      try:
        jobs_chunk = list(
            # Metrics computation only needs jid; avoid loading all job_data
            # columns or prefetching related metrics_data rows.
            job_data.objects.filter(pk__in=pk_chunk).only("jid")
        )
        metrics_manager.run(jobs_chunk)
        processed += len(jobs_chunk)
      except (OperationalError, DatabaseError) as exc:
        # Drop any broken/unsynchronised connection and retry this chunk once
        # with a fresh connection. If it still fails, let the exception bubble.
        log_print(
            "Database error while processing metrics chunk (size {0}) "
            "for date {1}: {2}".format(len(pk_chunk), date, exc)
        )
        close_old_connections()
        jobs_chunk = list(
            job_data.objects.filter(pk__in=pk_chunk).only("jid")
        )
        metrics_manager.run(jobs_chunk)
        processed += len(jobs_chunk)
      finally:
        # Release references promptly; GC can then free memory before next chunk.
        del jobs_chunk

    if DEBUG:
      close_old_connections()
      qs_after = _jobs_queryset(date, min_time, rerun=False)
      remaining = qs_after.count()
      log_print("jobs that don't have data after run (count): {0}".format(remaining))

  try:
    _run()
  except (OperationalError, DatabaseError) as exc:
    # Lost‑sync and similar errors require tearing down the connection and
    # retrying the whole run once with a clean connection.
    log_print(
        "Database error while updating metrics for date {0}, retrying once: {1}".format(
            date, exc
        )
    )
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
  default_start, default_end = _default_metrics_date_range()
  startdate, enddate = parse_start_end_dates(argv, default_start, default_end)

  log_date_range("metrics to update", startdate, enddate)
  #################################################################

  date = startdate
  all_dates = []
  while date <= enddate:
    all_dates.append(date)
    date += timedelta(days=1)

  all_dates = sorted(all_dates, reverse=True)
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
  previous_sigterm_handler = None
  sigterm_received = None
  try:
    previous_sigterm_handler, sigterm_received, _ = _install_sigterm_handler(
        exit_code=143
    )
    main()
    if shutdown_requested[0]:
      sys.exit(143)
  finally:
    _shutdown_db_best_effort()
    _notify_parent_if_sigterm(sigterm_received)
    if previous_sigterm_handler is not None:
      try:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
      except Exception:
        pass
