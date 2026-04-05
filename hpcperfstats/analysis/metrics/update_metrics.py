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
import gc
import signal
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections, transaction
from django.db.models import Count, F, IntegerField, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.db.utils import OperationalError, DatabaseError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    LiveDistinctHostTimeCount,
)
from hpcperfstats.analysis.metrics.metrics import expected_job_metric_row_count
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.site.machine.job_plot_artifacts import persist_job_plot_artifacts_for_jid
from hpcperfstats.site.machine.models import host_data, job_data, metrics_data

DEBUG = cfg.get_debug()

# Process jobs in chunks to bound memory; full job rows are not all held at once.
CHUNK_SIZE = 500

# host_data "latest sample per host" probes: keep each round-trip bounded.
# PostgreSQL uses a per-host LATERAL + LIMIT 1 (index probe on (host, time))
# inside a short transaction with parallel workers disabled for the probe.
HOST_LAST_TIME_LOOKUP_BATCH = 64

# Running a full GC every chunk is expensive on large backfills; amortize it.
GC_COLLECT_EVERY_N_CHUNKS = 20

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
  """Install SIGTERM handler that requests shutdown.

  Important: do not raise `SystemExit` from inside the signal handler. Raising
  exceptions from signal delivery can interrupt GC/finalizers and lead to noisy
  "Exception ignored in: ..." tracebacks during interpreter teardown.
  """
  sigterm_received = [False]

  previous_handler = signal.getsignal(signal.SIGTERM)

  def _sigterm_handler(signum, frame):
    sigterm_received[0] = True
    shutdown_requested[0] = True
    # Request a graceful stop; the main loops check `shutdown_requested`.
    _ = exit_code  # Keep closure stable if we later surface the exit code.

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


@functools.lru_cache(maxsize=1)
def _host_name_suffix():
  """FQDN suffix used by host_data host names."""
  return "." + cfg.get_host_name_ext()


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
    annotated = annotated.annotate(
        live_distinct_time_count=LiveDistinctHostTimeCount(_host_name_suffix()),
    )
    need_metrics |= (
        Q(metrics_distinct_time_count__isnull=False)
        & Q(live_distinct_time_count__gt=F("metrics_distinct_time_count"))
    )
  return annotated.filter(need_metrics).order_by("-end_time", "-jid")


def _iter_chunked_pks(queryset, chunk_size):
  """Yield (pk_list, total_so_far) in bounded chunks via queryset slicing.

  We intentionally avoid a long-lived streaming cursor here because the caller
  performs additional ORM queries while iterating chunks. On PostgreSQL, mixing
  a still-open server-side cursor with nested queries on the same connection can
  trigger protocol desynchronisation errors.

  For Django QuerySets ordered by ``-end_time, -jid`` we use keyset pagination
  to avoid large OFFSET scans during big backfills. For non-ORM/fake query-like
  objects (e.g. unit-test stubs), we transparently fall back to offset slicing.
  """
  total = 0
  # Primary path: keyset pagination for ORM querysets.
  try:
    last_end_time = None
    last_jid = None
    while True:
      page_qs = queryset
      if last_end_time is not None and last_jid is not None:
        page_qs = page_qs.filter(
            Q(end_time__lt=last_end_time) | (
                Q(end_time=last_end_time) & Q(jid__lt=last_jid)
            )
        )
      rows = list(page_qs.values_list("jid", "end_time")[:chunk_size])
      if not rows:
        break
      chunk = [jid for jid, _ in rows]
      total += len(chunk)
      yield chunk, total
      last_jid, last_end_time = rows[-1]
    return
  except Exception:
    # Fall back to bounded offset slicing for test doubles/non-ORM objects.
    pass

  offset = 0
  pk_values = queryset.values_list("jid", flat=True)
  while True:
    chunk = list(pk_values[offset:offset + chunk_size])
    if not chunk:
      break
    total += len(chunk)
    yield chunk, total
    offset += chunk_size


def _job_refs_from_jids(jids):
  """Return lightweight job references that only carry jid.

  metrics.Metrics().run() only requires ``job.jid``. Using tiny objects instead
  of ORM model instances avoids per-chunk model allocation and a redundant DB
  round-trip, which lowers memory usage and query pressure for large backfills.
  """
  return [SimpleNamespace(jid=jid) for jid in jids]


def _fqdn_hosts_for_job(job_row):
  """Return job host_list as FQDN hostnames used by host_data."""
  suffix = _host_name_suffix()
  hosts = []
  for host in (job_row.get("host_list") or []):
    h = str(host or "").strip()
    if not h:
      continue
    hosts.append(h if "." in h else (h + suffix))
  return hosts


def _latest_sample_time_by_host(hosts):
  """Map host -> max(host_data.time) for ``hosts``, using bounded batches."""
  latest_by_host = {}
  if not hosts:
    return latest_by_host
  host_list = sorted(hosts)
  batch = max(1, int(HOST_LAST_TIME_LOOKUP_BATCH))
  conn = connections["default"]
  if conn.vendor == "postgresql":
    ops = conn.ops
    tbl = ops.quote_name(host_data._meta.db_table)
    col_host = ops.quote_name("host")
    col_time = ops.quote_name("time")
    # DISTINCT ON + ORDER BY over many hypertable chunks can still trigger huge
    # parallel sorts. One backward index scan per host (LATERAL LIMIT 1) stays
    # bounded when (host, time) is indexed.
    sql = (
        "SELECT h.host_val, m.{t} FROM unnest(%s::text[]) AS h(host_val) "
        "LEFT JOIN LATERAL ("
        " SELECT d.{t} FROM {tbl} d WHERE d.{h} = h.host_val "
        " ORDER BY d.{t} DESC LIMIT 1"
        ") AS m ON TRUE"
    ).format(h=col_host, t=col_time, tbl=tbl)
    using = getattr(conn, "alias", None) or "default"
    for i in range(0, len(host_list), batch):
      chunk = host_list[i:i + batch]
      with transaction.atomic(using=using):
        with conn.cursor() as cursor:
          cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
          cursor.execute(sql, [chunk])
          for row_host, row_time in cursor.fetchall():
            if row_time is not None:
              latest_by_host[row_host] = row_time
    return latest_by_host

  for i in range(0, len(host_list), batch):
    chunk = host_list[i:i + batch]
    qs = (
        host_data.objects.filter(host__in=chunk)
        .values("host")
        .annotate(last_time=Max("time"))
    )
    for row in qs:
      latest_by_host[row.get("host")] = row.get("last_time")
  return latest_by_host


def _ready_jids_from_job_rows(jobs):
  """Return ready jids from pre-fetched job rows (jid/end_time/host_list)."""
  if not jobs:
    return []

  unique_hosts = set()
  job_hosts = {}
  for row in jobs:
    # De-duplicate host_list entries; gating is based on unique hosts and each
    # unique host must have data strictly after job end.
    hosts = set(_fqdn_hosts_for_job(row))
    job_hosts[row["jid"]] = hosts
    unique_hosts.update(hosts)

  latest_by_host = _latest_sample_time_by_host(unique_hosts)

  ready = []
  for row in jobs:
    jid = row["jid"]
    end_time = row.get("end_time")
    hosts = job_hosts.get(jid) or set()
    if end_time is None or not hosts:
      continue
    if all(
        (latest_by_host.get(host) is not None and latest_by_host[host] > end_time)
        for host in hosts
    ):
      ready.append(jid)
  return ready


def _filter_jids_with_samples_after_end(jids):
  """Keep jids where every job host has latest host_data.time strictly after end_time."""
  if not jids:
    return []

  jobs = list(
      job_data.objects.filter(jid__in=jids).values("jid", "end_time", "host_list")
  )
  return _ready_jids_from_job_rows(jobs)


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
    date_ymd = date.strftime("%Y-%m-%d")
    qs = _jobs_queryset(date, min_time, rerun)
    # Avoid a separate COUNT(*) that repeats the same filter work as the iterator below.
    log_print(
        "Streaming jobs needing metrics for date {0}".format(date_ymd)
    )

    def _persist_plot_artifacts_best_effort(ready_jid_list):
      for jid in ready_jid_list:
        if shutdown_requested[0]:
          break
        try:
          close_old_connections()
          persist_job_plot_artifacts_for_jid(jid)
          log_print(
              "jid {0}: plot artifacts prewarm completed (plots done).".format(jid)
          )
        except Exception as exc_plot:
          log_print(
              "plot artifact prewarm failed for jid {0}: {1}".format(
                  jid, exc_plot
              )
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
    skipped_not_ready = 0
    shared_pool = metrics_manager.ensure_pool()
    try:
      for chunk_number, (pk_chunk, _) in enumerate(_iter_chunked_pks(qs, CHUNK_SIZE), start=1):
        if shutdown_requested[0]:
          break
        jobs_chunk = None
        ready_jids = None
        try:
          ready_jids = _filter_jids_with_samples_after_end(pk_chunk)
          skipped_not_ready += (len(pk_chunk) - len(ready_jids))
          if not ready_jids:
            continue
          jobs_chunk = _job_refs_from_jids(ready_jids)
          metrics_manager.run(jobs_chunk, pool=shared_pool)
          processed += len(jobs_chunk)
          _persist_plot_artifacts_best_effort(ready_jids)
        except (OperationalError, DatabaseError) as exc:
          # Drop any broken/unsynchronised connection and retry this chunk once
          # with a fresh connection. If it still fails, let the exception bubble.
          log_print(
              "Database error while processing metrics chunk (size {0}) "
              "for date {1}: {2}".format(len(pk_chunk), date, exc)
          )
          close_old_connections()
          # If DB failed after readiness filtering (e.g. during run/persist), reuse
          # computed ready_jids to avoid extra query/CPU on retry.
          if ready_jids is None:
            ready_jids = _filter_jids_with_samples_after_end(pk_chunk)
            skipped_not_ready += (len(pk_chunk) - len(ready_jids))
          if not ready_jids:
            continue
          jobs_chunk = _job_refs_from_jids(ready_jids)
          metrics_manager.run(jobs_chunk, pool=shared_pool)
          processed += len(jobs_chunk)
          _persist_plot_artifacts_best_effort(ready_jids)
        finally:
          # Release references promptly; GC can then free memory before next chunk.
          jobs_chunk = None
          ready_jids = None
          if (
              GC_COLLECT_EVERY_N_CHUNKS > 0
              and chunk_number % GC_COLLECT_EVERY_N_CHUNKS == 0
              and gc.get_count()[0] > 10000
          ):
            gc.collect()

        if shutdown_requested[0]:
          break
    finally:
      metrics_manager.close_pool()

    log_print(
        "Finished metrics for date {0}: processed {1} jobs, skipped {2} not-ready jobs".format(
            date_ymd, processed, skipped_not_ready
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

  day_count = (enddate - startdate).days + 1
  # Newest calendar day first (end of range / today before older days).
  all_dates = [enddate - timedelta(days=i) for i in range(day_count)]
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
