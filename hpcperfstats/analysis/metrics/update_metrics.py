#!/usr/bin/env python
"""Update metrics_data for jobs ending on each day in a date range.

Filters by runtime, optionally skips jobs that already have a full metrics
catalog (one row per metric with either a numeric value or no_data_reason),
runs Metrics().run(jobs_list). With no CLI date arguments, processes the last
seven calendar days through today.

Processing order: **newest calendar day first**, and within each day **newest job
first** (``end_time`` descending, then ``jid`` descending as a stable tiebreaker).

"""
import functools
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce
from django.db.utils import OperationalError, DatabaseError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.analysis.metrics.metrics import expected_job_metric_row_count
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.site.machine.models import job_data, metrics_data

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


@functools.lru_cache(maxsize=1)
def _expected_job_metrics_row_count():
  """Catalog row count; metrics definitions are fixed for the process lifetime."""
  return expected_job_metric_row_count()


def _jobs_queryset(date, min_time, rerun):
  """Jobs ending on ``date`` with runtime >= min_time, newest first (end_time, jid)."""
  qs = job_data.objects.filter(end_time__date=date.date()).exclude(
      runtime__lt=min_time)
  if rerun:
    return qs.order_by("-end_time", "-jid")
  # Jobs need a metrics pass if row count is below the full catalog, or any
  # row still has null value without an explicit no_data_reason (legacy / stuck).
  # Use scalar subqueries on metrics_data (jid_id index) instead of joining
  # metrics_data twice via reverse Count(), which avoids row multiplication.
  expected = _expected_job_metrics_row_count()
  stale_q = Q(value__isnull=True) & (
      Q(no_data_reason__isnull=True) | Q(no_data_reason="")
  )
  int0 = Value(0, output_field=IntegerField())
  md_total_sq = Subquery(
      metrics_data.objects.filter(jid_id=OuterRef("jid"))
      .values("jid_id")
      .annotate(c=Count("id"))
      .values("c")[:1],
      output_field=IntegerField(),
  )
  stale_count_sq = Subquery(
      metrics_data.objects.filter(jid_id=OuterRef("jid"))
      .filter(stale_q)
      .values("jid_id")
      .annotate(c=Count("id"))
      .values("c")[:1],
      output_field=IntegerField(),
  )
  annotated = qs.annotate(
      md_count=Coalesce(md_total_sq, int0),
      stale_null=Coalesce(stale_count_sq, int0),
  )
  need_metrics = Q(md_count__lt=expected) | Q(stale_null__gt=0)
  # PostgreSQL only: re-run when host_data has more per-host sample times (sum
  # of COUNT(DISTINCT time) per host) than at last metrics persist (same window
  # + FQDN host list as jid_table).
  if connections["default"].vendor == "postgresql":
    host_suffix = "." + cfg.get_host_name_ext()
    live_sql = (
        "(SELECT COALESCE(SUM(ph.cnt), 0)::integer FROM ("
        "SELECT h.host, COUNT(DISTINCT h.time)::integer AS cnt "
        "FROM host_data h "
        "WHERE h.time >= %s AND h.time <= %s AND h.host IN ("
        "SELECT (COALESCE(elem::text, '') || %s)::text "
        "FROM unnest(%s) AS t(elem)) "
        "GROUP BY h.host) ph)"
    )
    live_subq = RawSQL(
        live_sql,
        (
            OuterRef("start_time"),
            OuterRef("end_time"),
            host_suffix,
            OuterRef("host_list"),
        ),
        output_field=IntegerField(),
    )
    annotated = annotated.annotate(live_distinct_time_count=live_subq)
    need_metrics |= (
        Q(metrics_distinct_time_count__isnull=False)
        & Q(live_distinct_time_count__gt=F("metrics_distinct_time_count"))
    )
  return annotated.filter(need_metrics).order_by("-end_time", "-jid")


def _iter_chunked_pks(queryset, chunk_size):
  """Yield (pk_list, total_so_far) in chunks, streaming PKs from the database.

  This implementation relies on Django's ``iterator`` with a server-side cursor
  so that we never materialise the full PK list in memory. We accumulate PKs
  into Python lists of at most ``chunk_size`` elements and yield each list
  together with the cumulative total seen so far.
  """
  total = 0
  current_chunk = []
  # jid is the primary key; values_list avoids loading full job_data rows.
  for pk in queryset.values_list("jid", flat=True).iterator(chunk_size=chunk_size):
    current_chunk.append(pk)
    if len(current_chunk) >= chunk_size:
      total += len(current_chunk)
      yield current_chunk, total
      current_chunk = []

  if current_chunk:
    total += len(current_chunk)
    yield current_chunk, total


def update_metrics(date, rerun=False):
  """Compute and persist metrics for all jobs ending on date (runtime >= min_time).

  If not rerun, skip jobs that already have the full metrics catalog (each metric
  has a value or no_data_reason). Uses metrics.Metrics().run(jobs_list).

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
    # Avoid a separate COUNT(*) that repeats the same filter work as the iterator below.
    log_print(
        "Streaming jobs needing metrics for date {0}".format(date.strftime("%Y-%m-%d"))
    )

    metrics_manager = metrics.Metrics()
    log_print(
        "Compute for following metrics for date {0}".format(date)
    )
    for name in metrics_manager.simple_metrics_list:
      log_print(name)
    for name in metrics_manager.complex_metrics_list:
      log_print(name)

    processed = 0
    for pk_chunk, _ in _iter_chunked_pks(qs, CHUNK_SIZE):
      try:
        # Preserve iterator order (newest job first) — SQL IN does not preserve order.
        job_by_jid = {
            j.jid: j
            for j in job_data.objects.filter(pk__in=pk_chunk).only("jid")
        }
        jobs_chunk = [job_by_jid[jid] for jid in pk_chunk if jid in job_by_jid]
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
        job_by_jid = {
            j.jid: j
            for j in job_data.objects.filter(pk__in=pk_chunk).only("jid")
        }
        jobs_chunk = [job_by_jid[jid] for jid in pk_chunk if jid in job_by_jid]
        metrics_manager.run(jobs_chunk)
        processed += len(jobs_chunk)
      finally:
        # Release references promptly; GC can then free memory before next chunk.
        del jobs_chunk

    log_print(
        "Finished metrics for date {0}: processed {1} jobs".format(
            date.strftime("%Y-%m-%d"), processed
        )
    )

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

  Dates in the parsed range are processed **newest day first**; see module
  docstring for per-day job order.
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

  # Newest calendar day first (end of range / today before older days).
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
