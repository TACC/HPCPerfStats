"""
Management command: recompute ``delta`` / ``arc`` on shared-stamped rows.

Repairs ``host_data`` rows stamped ``dev='j:<jid>'`` that ingest wrote
before the per-device delta fix (2026-11-03): their ``delta`` / ``arc`` were
the difference between neighbouring devices, not between samples. Work units
are ``(jid, host)`` pairs from ``job_affinity`` joined to ``job_data`` (the
monitor marks a host shared only while it sends ``%affinity`` for the job),
so every read is one host over one job window on the ``(host, time)`` index
and the ``dev`` filter matches only that job's stamp. Planning is
``hpcperfstats.dbload.lib.host_data_jstamp_delta_repair``.

``--census`` reports the scope from ``job_affinity`` / ``job_data`` only (no
``host_data`` read unless ``--sample`` asks for row counts). The default is
a dry run; ``--apply`` writes, one transaction per unit slice, and marks each
repaired job ``metrics_dirty_at`` so ``update_metrics`` recomputes it.
Re-running is safe: rows that already hold the recomputed pair are skipped.

Attributes:
  _NO_TIMEOUT_SQL: Disables the statement timeout for one transaction.
  _DML_DECOMPRESS_SQL: Lifts Timescale's cap on rows decompressed by DML.
  _UNITS_SQL: ``(jid, host, start, end)`` per marked host of an accounted
    job, newest first; ``{where}`` takes the job filters.
  _PENDING_SQL: Count of affinity rows (and jobs) without accounting yet.
  _ROWS_SQL: One unit slice's stamped rows (non-skipped types).
  _UPDATE_SQL: Batched ``delta`` / ``arc`` update keyed by the row key;
    ``{values}`` takes the VALUES placeholders.
  _WINDOW_PAD: Margin around the job window (samples near its edges).
  _UPDATE_BATCH: Rows per UPDATE statement.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, NamedTuple

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from hpcperfstats.analysis.metrics.lib.job_host_data_scope import job_dev_token
from hpcperfstats.dbload.lib.host_data_jstamp_delta_repair import (
  SKIP_TYPES,
  RowFix,
  SeriesCarry,
  group_series,
  plan_series,
)

_NO_TIMEOUT_SQL = "SET LOCAL statement_timeout = 0"
_DML_DECOMPRESS_SQL = (
  "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
)
_UNITS_SQL = (
  "SELECT a.jid, a.host, j.start_time, j.end_time FROM job_affinity a "
  "JOIN job_data j ON j.jid = a.jid WHERE j.start_time IS NOT NULL "
  "AND j.end_time IS NOT NULL {where} ORDER BY j.end_time DESC, a.host"
)
_PENDING_SQL = (
  "SELECT count(*), count(DISTINCT a.jid) FROM job_affinity a "
  "LEFT JOIN job_data j ON j.jid = a.jid "
  "WHERE j.jid IS NULL OR j.end_time IS NULL"
)
_ROWS_SQL = (
  "SELECT time, extract(epoch FROM time)::float8, type, event, value, delta, "
  "arc FROM host_data WHERE host = %s AND time >= %s AND time < %s "
  "AND dev = %s AND type <> ALL(%s)"
)
_UPDATE_SQL = (
  "UPDATE host_data AS h SET delta = v.delta, arc = v.arc FROM (VALUES "
  "{values}) AS v(time, type, event, delta, arc) WHERE h.host = %s "
  "AND h.dev = %s AND h.time >= %s AND h.time < %s AND h.time = v.time "
  "AND h.type = v.type AND h.event = v.event"
)
_WINDOW_PAD = dt.timedelta(minutes=10)
_UPDATE_BATCH = 1000


class Unit(NamedTuple):
  """One ``(jid, host)`` repair unit and its job window.

  Subclasses ``typing.NamedTuple``, the standard immutable record base; this
  class only names the four fields.

  Attributes:
    jid: Job id.
    host: ``host_data.host`` (FQDN, as ``job_affinity`` stores it).
    start: Job start time.
    end: Job end time.
  """

  jid: str
  host: str
  start: dt.datetime
  end: dt.datetime


class UnitResult(NamedTuple):
  """Counts for one processed unit.

  Subclasses ``typing.NamedTuple``, the standard immutable record base; this
  class only names the four fields.

  Attributes:
    unit: The unit.
    rows: Stamped rows read.
    fixed: Rows whose ``delta`` / ``arc`` were (or would be) rewritten.
    nulled: Fixed rows whose new ``delta`` is NULL (series start, gap).
  """

  unit: Unit
  rows: int
  fixed: int
  nulled: int


def _parse_day(value: str) -> dt.datetime:
  """Parse ``YYYY-MM-DD`` into midnight UTC.

  Args:
    value (str): Day text.

  Returns:
    dt.datetime: Timezone-aware midnight UTC.

  Raises:
    CommandError: When ``value`` is not ``YYYY-MM-DD``.

  Examples:
    >>> _parse_day("2026-11-03").isoformat()
    '2026-11-03T00:00:00+00:00'
  """
  try:
    day = dt.datetime.strptime(value, "%Y-%m-%d")
  except ValueError as exc:
    raise CommandError(f"expected YYYY-MM-DD, got {value!r}") from exc
  return day.replace(tzinfo=dt.UTC)


def _parse_cutoff(value: str) -> dt.datetime:
  """Parse the ``--before`` cutoff (a day or an ISO time; naive is UTC).

  Args:
    value (str): ``YYYY-MM-DD`` or an ISO-8601 date-time.

  Returns:
    dt.datetime: Timezone-aware cutoff in UTC.

  Raises:
    CommandError: When ``value`` is not an ISO date or date-time.

  Examples:
    >>> _parse_cutoff("2026-11-04T06:30").isoformat()
    '2026-11-04T06:30:00+00:00'
  """
  try:
    when = dt.datetime.fromisoformat(value)
  except ValueError as exc:
    raise CommandError(
      f"--before: expected an ISO time, got {value!r}"
    ) from exc
  if when.tzinfo is None:
    when = when.replace(tzinfo=dt.UTC)
  return when.astimezone(dt.UTC)


def _slices(
  start: dt.datetime, end: dt.datetime, hours: int
) -> Iterator[tuple[dt.datetime, dt.datetime]]:
  """Yield ``hours``-long ``[a, b)`` slices covering ``[start, end]``.

  Args:
    start (dt.datetime): First instant.
    end (dt.datetime): Last instant (included by the final slice).
    hours (int): Slice length in hours (at least 1).

  Yields:
    tuple[dt.datetime, dt.datetime]: Consecutive slices, the last one ending
    just past ``end``.

  Examples:
    >>> t = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    >>> len(list(_slices(t, t + dt.timedelta(hours=13), 6)))
    3
  """
  step = dt.timedelta(hours=max(1, int(hours)))
  stop = end + dt.timedelta(microseconds=1)
  cur = start
  while cur < stop:
    yield cur, min(cur + step, stop)
    cur += step


def _update_params(
  fixes: list[tuple[str, RowFix]],
  host: str,
  dev: str,
  start: dt.datetime,
  end: dt.datetime,
) -> tuple[str, list[Any]]:
  """Build one batched UPDATE for ``(type, fix)`` pairs of a unit slice.

  The slice's constant ``[start, end)`` lets TimescaleDB exclude every other
  chunk at plan time; times joined from ``VALUES`` alone made each batch
  scan the host's whole history (hpcperfstats04: 37 min on the first batch).

  Args:
    fixes (list[tuple[str, RowFix]]): ``(type, fix)`` rows.
    host (str): ``host_data.host``.
    dev (str): The unit's ``j:<jid>`` stamp.
    start (dt.datetime): Slice start (inclusive).
    end (dt.datetime): Slice end (exclusive).

  Returns:
    tuple[str, list[Any]]: SQL and its parameters.

  Examples:
    >>> fix = RowFix("2026-11-03T00:00:00+00:00", "user", 1.0, 0.5)
    >>> sql, params = _update_params([("host_cpu", fix)], "h", "j:7", 1, 2)
    >>> params[-4:], sql.count("%s::real")
    (['h', 'j:7', 1, 2], 2)
  """
  values = ", ".join(
    "(%s::timestamptz, %s::text, %s::text, %s::real, %s::real)" for _ in fixes
  )
  params: list[Any] = []
  for typ, fix in fixes:
    params += [fix.time, typ, fix.event, fix.delta, fix.arc]
  return _UPDATE_SQL.format(values=values), [*params, host, dev, start, end]


class Command(BaseCommand):
  """Recompute shared-stamp ``delta`` / ``arc`` from consecutive values.

  Subclasses Django ``BaseCommand``, the framework entry for ``manage.py``
  commands (argument parsing, ``handle`` dispatch, stdout/stderr). This
  subclass adds the census, the per-unit repair and a thread pool over
  units. Requires PostgreSQL.

  Invoke: ``manage.py repair_jstamp_deltas --census`` (scope only), then
  ``--before <rebuild time> --jid 79282`` (dry run of one job), then
  ``--before <rebuild time> --apply`` for everything. ``--before`` is
  required outside ``--census``: rows ingested after the fixed parser went
  live already hold exact per-device deltas and must never be rewritten.
  """

  help = (
    "Recompute delta/arc of host_data rows stamped dev='j:<jid>' from "
    "consecutive values (per-device delta bug before 2026-11-03). Dry run "
    "unless --apply; --census reports the scope only."
  )

  def add_arguments(self, parser: Any) -> None:
    """Declare the command-line options (override of ``BaseCommand``).

    ``BaseCommand.add_arguments`` is the hook Django calls to let a command
    extend its ``argparse`` parser.

    Args:
      parser (Any): The ``argparse`` parser Django built for this command.

    Returns:
      None

    Examples:
      >>> # manage.py repair_jstamp_deltas --help
      >>> Command().add_arguments  # doctest: +SKIP
    """
    parser.add_argument(
      "--census",
      action="store_true",
      help="Report units per day from job tables only, then exit.",
    )
    parser.add_argument(
      "--sample",
      type=int,
      default=0,
      help="With --census: count stamped rows for the N longest units.",
    )
    parser.add_argument(
      "--apply", action="store_true", help="Write changes (default: dry run)."
    )
    parser.add_argument(
      "--jid", action="append", default=[], help="Limit to this job (repeat)."
    )
    parser.add_argument(
      "--before",
      help="Cutoff (YYYY-MM-DD or ISO time, naive = UTC): when the fixed "
      "parser went live. Rows at/after it are never read or written; jobs "
      "starting at/after it are skipped. Required except with --census.",
    )
    parser.add_argument("--since", help="First job end day, YYYY-MM-DD UTC.")
    parser.add_argument("--until", help="Last job end day, YYYY-MM-DD UTC.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--slice-hours", type=int, default=6)

  def handle(self, *args: Any, **options: Any) -> None:
    """Run the census or the repair (override of ``BaseCommand.handle``).

    ``BaseCommand.handle`` is the hook Django calls after parsing options.

    Args:
      *args (Any): Unused; Django may pass positional leftovers.
      **options (Any): Parsed options: ``census``, ``sample``, ``apply``,
        ``jid``, ``before``, ``since``, ``until``, ``workers`` and
        ``slice_hours``.

    Returns:
      None

    Raises:
      CommandError: When the database is not PostgreSQL, ``--before`` is
        missing outside ``--census``, the date range is invalid, or an UPDATE
        touches a different row count than planned (that slice is rolled
        back).

    Examples:
      >>> # manage.py repair_jstamp_deltas --census
      >>> Command().handle(census=True)  # doctest: +SKIP
    """
    if connection.vendor != "postgresql":
      raise CommandError("This command requires PostgreSQL.")
    if not options.get("before") and not options.get("census"):
      raise CommandError(
        "--before is required: give the time the fixed parser went live "
        "(the pipeline rebuild); later rows already hold exact deltas"
      )
    cutoff = _parse_cutoff(options["before"]) if options.get("before") else None
    units = self._units(options, cutoff)
    if options["census"]:
      self._census(units, int(options["sample"] or 0))
      return
    apply = bool(options["apply"])
    self.stdout.write(
      f"{'APPLY' if apply else 'DRY RUN'} units={len(units)} "
      f"workers={options['workers']}"
    )
    totals = {"units": 0, "rows": 0, "fixed": 0, "nulled": 0}
    repaired: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, options["workers"])) as pool:
      futures = [
        pool.submit(self._run_unit, u, apply, options["slice_hours"], cutoff)
        for u in units
      ]
      for fut in as_completed(futures):
        res = fut.result()
        totals["units"] += 1
        totals["rows"] += res.rows
        totals["fixed"] += res.fixed
        totals["nulled"] += res.nulled
        if res.fixed:
          repaired.add(res.unit.jid)
        self.stdout.write(
          f"repair_jstamp unit jid={res.unit.jid} host={res.unit.host} "
          f"rows={res.rows} fixed={res.fixed} nulled={res.nulled}"
        )
    if apply and repaired:
      from hpcperfstats.site.lib.machine.models import job_data

      job_data.objects.filter(jid__in=sorted(repaired)).update(
        metrics_dirty_at=timezone.now()
      )
    self.stdout.write(
      "TOTAL "
      + " ".join(f"{k}={v}" for k, v in totals.items())
      + f" jobs_marked_dirty={len(repaired) if apply else 0}"
    )

  def _units(
    self, options: dict[str, Any], cutoff: dt.datetime | None
  ) -> list[Unit]:
    """Return the ``(jid, host)`` units selected by the options.

    Args:
      options (dict[str, Any]): Parsed options (``jid``, ``since``,
        ``until``).
      cutoff (dt.datetime | None): ``--before``; jobs starting at or after
        it are not units (their rows came from the fixed parser). ``None``
        only for ``--census`` without ``--before``.

    Returns:
      list[Unit]: Units, newest job first.

    Raises:
      CommandError: When only one of ``--since`` / ``--until`` is given or
        the range is reversed.

    Examples:
      >>> Command()._units({"jid": ["1"]}, None)  # doctest: +SKIP
    """
    where, params = [], []
    if cutoff is not None:
      where.append("AND j.start_time < %s")
      params.append(cutoff)
    if options.get("jid"):
      where.append("AND j.jid = ANY(%s)")
      params.append([str(j) for j in options["jid"]])
    if bool(options.get("since")) != bool(options.get("until")):
      raise CommandError("give both --since and --until, or neither")
    if options.get("since"):
      since = _parse_day(options["since"])
      until = _parse_day(options["until"]) + dt.timedelta(days=1)
      if until <= since:
        raise CommandError("--until must not be before --since")
      where.append("AND j.end_time >= %s AND j.end_time < %s")
      params += [since, until]
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cursor.execute(_UNITS_SQL.format(where=" ".join(where)), params)
      return [Unit(str(j), str(h), s, e) for j, h, s, e in cursor.fetchall()]

  def _census(self, units: list[Unit], sample: int) -> None:
    """Print units, jobs and host-hours per job end day, plus pending rows.

    Args:
      units (list[Unit]): Selected units.
      sample (int): Count stamped rows for this many longest units.

    Returns:
      None

    Examples:
      >>> Command()._census([], 0)  # doctest: +SKIP
    """
    by_day: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
      by_day[u.end.astimezone(dt.UTC).strftime("%Y-%m-%d")].append(u)
    self.stdout.write("day jobs units host_hours")
    for day in sorted(by_day):
      us = by_day[day]
      hours = sum((u.end - u.start).total_seconds() for u in us) / 3600
      self.stdout.write(
        f"{day} {len({u.jid for u in us})} {len(us)} {hours:.0f}"
      )
    total_h = sum((u.end - u.start).total_seconds() for u in units) / 3600
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cursor.execute(_PENDING_SQL)
      pend_rows, pend_jobs = cursor.fetchone()
    self.stdout.write(
      f"TOTAL days={len(by_day)} jobs={len({u.jid for u in units})} "
      f"units={len(units)} host_hours={total_h:.0f} "
      f"pending_no_accounting_rows={pend_rows} jobs={pend_jobs}"
    )
    longest = sorted(units, key=lambda u: u.end - u.start, reverse=True)
    for u in longest[: max(0, sample)]:
      with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(_NO_TIMEOUT_SQL)
        cursor.execute(
          "SELECT count(*) FROM host_data WHERE host = %s AND time >= %s "
          "AND time <= %s AND dev = %s",
          [
            u.host,
            u.start - _WINDOW_PAD,
            u.end + _WINDOW_PAD,
            job_dev_token(u.jid),
          ],
        )
        (n,) = cursor.fetchone()
      self.stdout.write(f"sample jid={u.jid} host={u.host} stamped_rows={n}")

  def _run_unit(
    self,
    unit: Unit,
    apply: bool,
    slice_hours: int,
    cutoff: dt.datetime | None = None,
  ) -> UnitResult:
    """Repair (or plan) one unit slice by slice on this worker thread.

    Each slice reads and, with ``apply``, writes in one transaction; carry
    keeps every series continuous across slices. The window stops just
    before ``cutoff``: rows at or after it hold exact per-device deltas.

    Args:
      unit (Unit): The unit.
      apply (bool): Write when true, plan only when false.
      slice_hours (int): Hours per slice.
      cutoff (dt.datetime | None): ``--before``; ``None`` is no limit.

    Returns:
      UnitResult: Counts for the unit.

    Raises:
      CommandError: When an UPDATE changes a different number of rows than
        planned (the slice rolls back).

    Examples:
      >>> Command()._run_unit(unit, False, 6)  # doctest: +SKIP
    """
    close_old_connections()
    dev = job_dev_token(unit.jid)
    carries: dict[tuple[str, str], SeriesCarry | None] = {}
    rows = fixed = nulled = 0
    stop = unit.end + _WINDOW_PAD
    if cutoff is not None:
      # _slices includes its end instant; stay strictly before the cutoff.
      stop = min(stop, cutoff - dt.timedelta(microseconds=1))
    try:
      for a, b in _slices(unit.start - _WINDOW_PAD, stop, slice_hours):
        with transaction.atomic(), connection.cursor() as cursor:
          cursor.execute(_NO_TIMEOUT_SQL)
          cursor.execute(_ROWS_SQL, [unit.host, a, b, dev, list(SKIP_TYPES)])
          raw = cursor.fetchall()
          rows += len(raw)
          plan: list[tuple[str, RowFix]] = []
          for (typ, event), series in group_series(raw).items():
            fixes, carries[(typ, event)] = plan_series(
              typ, event, series, carries.get((typ, event))
            )
            plan += [(typ, f) for f in fixes]
          fixed += len(plan)
          nulled += sum(1 for _t, f in plan if f.delta is None)
          if apply and plan:
            cursor.execute(_DML_DECOMPRESS_SQL)
            for i in range(0, len(plan), _UPDATE_BATCH):
              batch = plan[i : i + _UPDATE_BATCH]
              sql, params = _update_params(batch, unit.host, dev, a, b)
              cursor.execute(sql, params)
              if cursor.rowcount != len(batch):
                raise CommandError(
                  f"jid={unit.jid} host={unit.host} slice={a:%Y-%m-%d %H:%M}"
                  f": updated {cursor.rowcount} rows, planned {len(batch)}"
                )
    finally:
      connection.close()
    return UnitResult(unit, rows, fixed, nulled)
