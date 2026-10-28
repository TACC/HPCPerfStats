"""
Management command: sum per-core ``host_data`` rows into ``dev=''`` rows.

Repairs rows written between commit ce8f834f (2026-10-24) and the collapse fix,
when whole-node non-GPU metrics were stored once per hardware device. For each
(host batch, time window) it plans the whole-node rows with
``plan_collapse``, inserts them, checks the inserted rows against the plan, and
only then deletes the per-core rows that now have a ``dev=''`` row. A key that
already has a ``dev=''`` row keeps it; only its per-core copies are deleted.
Dry run by default.

Attributes:
  _PERCORE_WHERE: SQL predicate selecting per-core rows (non-GPU, hardware dev).
  _COLUMNS: ``host_data`` columns read for planning and written on insert.
  _INSERT_SQL: Insert of one planned whole-node row; an existing row wins.
  _DELETE_SQL: Delete of per-core rows that have a ``dev=''`` row at the same
    (host, type, event, time).
  _DISCOVER_DAYS_SQL: Distinct UTC days that hold ``cpu`` rows whose ``dev``
    starts with a digit (the per-core rows), found over the whole table.
  _NO_TIMEOUT_SQL: Disables the statement timeout for the current transaction;
    one-off repair queries scan large windows and must not be cancelled.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from hpcperfstats.dbload.lib.host_data_percore_collapse import (
  GPU_TYPES,
  KEY_COLS,
  plan_collapse,
)

_PERCORE_WHERE = (
  "dev IS NOT NULL AND dev <> '' AND dev NOT LIKE 'j:%%' "
  "AND type IS NOT NULL AND type <> ALL(%s)"
)
_COLUMNS = [
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "delta",
  "arc",
]
_INSERT_SQL = (
  f"INSERT INTO host_data ({', '.join(_COLUMNS)}) "
  f"VALUES ({', '.join(['%s'] * len(_COLUMNS))}) ON CONFLICT DO NOTHING"
)
_DELETE_SQL = (
  "DELETE FROM host_data hd WHERE hd.host = ANY(%s) AND hd.time >= %s "
  "AND hd.time < %s AND hd.dev IS NOT NULL AND hd.dev <> '' "
  "AND hd.dev NOT LIKE 'j:%%' AND hd.type IS NOT NULL "
  "AND hd.type <> ALL(%s) AND EXISTS (SELECT 1 FROM host_data w "
  "WHERE w.host = hd.host AND w.type = hd.type AND w.event = hd.event "
  "AND w.time = hd.time AND w.dev = '')"
)
_DISCOVER_DAYS_SQL = (
  "SELECT DISTINCT date_trunc('day', time AT TIME ZONE 'UTC') FROM host_data "
  "WHERE type = 'cpu' AND dev ~ '^[0-9]'"
)
_NO_TIMEOUT_SQL = "SET LOCAL statement_timeout = 0"


def _parse_day(value: str) -> dt.datetime:
  """Parse ``YYYY-MM-DD`` into midnight UTC.

  Args:
    value (str): Day text.

  Returns:
    dt.datetime: Timezone-aware midnight UTC for that day.

  Raises:
    CommandError: When ``value`` is not a valid ``YYYY-MM-DD`` date.

  Examples:
    >>> _parse_day("2026-10-24").isoformat()
    '2026-10-24T00:00:00+00:00'
  """
  try:
    day = dt.datetime.strptime(value, "%Y-%m-%d")
  except ValueError as exc:
    raise CommandError(f"expected YYYY-MM-DD, got {value!r}") from exc
  return day.replace(tzinfo=dt.UTC)


def _windows(
  since: dt.datetime, until: dt.datetime, hours: int
) -> Iterator[tuple[dt.datetime, dt.datetime]]:
  """Yield consecutive ``hours``-long windows covering ``since``..``until``.

  Args:
    since (dt.datetime): First day, midnight UTC.
    until (dt.datetime): Last day, midnight UTC (its whole day is included).
    hours (int): Window length in hours.

  Yields:
    tuple[dt.datetime, dt.datetime]: ``(start, end)`` with ``end`` exclusive.

  Examples:
    >>> day = _parse_day("2026-10-24")
    >>> len(list(_windows(day, day, 6)))
    4
  """
  start = since
  stop = until + dt.timedelta(days=1)
  step = dt.timedelta(hours=hours)
  while start < stop:
    yield start, min(start + step, stop)
    start += step


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
  """Yield ``items`` in lists of at most ``size``.

  Args:
    items (Sequence[Any]): Items to split.
    size (int): Maximum chunk length (at least 1).

  Yields:
    list[Any]: Consecutive slices of ``items``.

  Examples:
    >>> list(_chunks([1, 2, 3, 4, 5], 2))
    [[1, 2], [3, 4], [5]]
  """
  size = max(1, int(size))
  for i in range(0, len(items), size):
    yield list(items[i : i + size])


def _query_frame(
  cursor: Any, sql: str, params: list[Any], cols: list[str]
) -> Any:
  """Run ``sql`` and return the rows as a DataFrame with ``cols``.

  Args:
    cursor (Any): Open DB cursor.
    sql (str): Statement with ``%s`` placeholders.
    params (list[Any]): Bound parameters.
    cols (list[str]): Column names for the result.

  Returns:
    Any: ``pandas.DataFrame`` (possibly empty).

  Examples:
    >>> # frame = _query_frame(cursor, "SELECT 1", [], ["one"])
    >>> _query_frame  # doctest: +SKIP
  """
  cursor.execute(sql, params)
  return pd.DataFrame(cursor.fetchall(), columns=cols)


def _records(frame: Any) -> list[tuple[Any, ...]]:
  """Return planned rows as insert tuples, with NaN turned into NULL.

  Args:
    frame (Any): Planned rows in ``_COLUMNS`` order.

  Returns:
    list[tuple[Any, ...]]: One tuple per row.

  Examples:
    >>> import numpy as np
    >>> import pandas as pd
    >>> df = pd.DataFrame({c: [None] for c in _COLUMNS})
    >>> df["value"] = [np.nan]
    >>> _records(df)[0][_COLUMNS.index("value")] is None
    True
  """
  clean = frame[_COLUMNS].astype(object).where(frame[_COLUMNS].notna(), None)
  return [tuple(row) for row in clean.itertuples(index=False, name=None)]


class Command(BaseCommand):
  """Sum per-core ``host_data`` rows into one ``dev=''`` row per metric.

  Subclasses Django ``BaseCommand``, the framework entry for ``manage.py``
  commands (argument parsing, ``handle`` dispatch, stdout/stderr). This
  subclass adds the arguments and the repair loop. Requires PostgreSQL.

  Invoke: ``manage.py collapse_percore_host_data`` (dry run over every day
  that holds per-core cpu rows), or ``--since 2026-10-22 --until 2026-10-27``
  to limit the range; add ``--apply`` to write.
  """

  help = (
    "Sum per-core host_data rows (non-GPU, non-j: dev) into one dev='' row "
    "per (host, type, event, time). Dry run unless --apply."
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
      >>> # manage.py collapse_percore_host_data --help
      >>> Command().add_arguments  # doctest: +SKIP
    """
    parser.add_argument(
      "--since",
      help="First day, YYYY-MM-DD (UTC). Omit with --until to search the "
      "whole table for days holding per-core cpu rows.",
    )
    parser.add_argument(
      "--until", help="Last day, YYYY-MM-DD (UTC), inclusive."
    )
    parser.add_argument(
      "--apply", action="store_true", help="Write changes (default: dry run)."
    )
    parser.add_argument(
      "--host",
      action="append",
      default=[],
      help="Limit to this host (repeatable).",
    )
    parser.add_argument("--window-hours", type=int, default=1)
    parser.add_argument("--host-batch", type=int, default=8)
    parser.add_argument("--lookback-minutes", type=int, default=120)

  def handle(self, *args: Any, **options: Any) -> None:
    """Run the repair (override of ``BaseCommand.handle``).

    ``BaseCommand.handle`` is the hook Django calls after parsing options.

    Args:
      *args (Any): Unused; Django may pass positional leftovers.
      **options (Any): Parsed options: ``since``, ``until``, ``apply``,
        ``host``, ``window_hours``, ``host_batch`` and ``lookback_minutes``.

    Returns:
      None

    Raises:
      CommandError: When the database is not PostgreSQL, the range is
        invalid, or a window fails verification (that window is rolled back).

    Examples:
      >>> # manage.py collapse_percore_host_data --since 2026-10-22 --until 2026-10-27
      >>> Command().handle(
      ...   since="2026-10-22", until="2026-10-27"
      ... )  # doctest: +SKIP
    """
    if connection.vendor != "postgresql":
      raise CommandError("This command requires PostgreSQL.")
    if bool(options["since"]) != bool(options["until"]):
      raise CommandError("give both --since and --until, or neither")
    apply = bool(options["apply"])
    totals = {"percore_rows": 0, "groups": 0, "kept_existing": 0, "new_rows": 0}
    if options["since"]:
      since = _parse_day(options["since"])
      until = _parse_day(options["until"])
      if until < since:
        raise CommandError("--until must not be before --since")
      ranges = [(since, until)]
      label = f"{since.date()}..{until.date()}"
    else:
      days = self._discover_days()
      ranges = [(day, day) for day in days]
      label = f"all days with per-core cpu rows ({len(days)} days)"
    self.stdout.write(f"{'APPLY' if apply else 'DRY RUN'} {label}")
    for start, end in (
      w
      for first, last in ranges
      for w in _windows(first, last, options["window_hours"])
    ):
      hosts = self._percore_hosts(start, end, options["host"])
      self.stdout.write(
        f"{start:%Y-%m-%d %H:%M} window hosts_with_percore={len(hosts)}"
      )
      for batch in _chunks(hosts, options["host_batch"]):
        plan = self._migrate(start, end, batch, apply, options)
        totals["percore_rows"] += plan.percore_rows
        totals["groups"] += plan.groups
        totals["kept_existing"] += plan.kept_existing
        totals["new_rows"] += len(plan.new_rows)
        if plan.percore_rows:
          self.stdout.write(
            f"{start:%Y-%m-%d %H:%M} hosts={len(batch)} "
            f"percore_rows={plan.percore_rows} groups={plan.groups} "
            f"kept_existing={plan.kept_existing} new_rows={len(plan.new_rows)}"
          )
    self.stdout.write(
      "TOTAL " + " ".join(f"{k}={v}" for k, v in totals.items())
    )

  def _discover_days(self) -> list[dt.datetime]:
    """Return every UTC day that holds ``cpu`` rows with a digit-leading dev.

    One scan of the whole table with the statement timeout disabled, so the
    repair does not assume the backlog fits a date range.

    Returns:
      list[dt.datetime]: Sorted midnight-UTC datetimes.

    Examples:
      >>> # days = Command()._discover_days()
      >>> Command._discover_days  # doctest: +SKIP
    """
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cursor.execute(_DISCOVER_DAYS_SQL)
      return sorted(row[0].replace(tzinfo=dt.UTC) for row in cursor.fetchall())

  def _percore_hosts(
    self, start: dt.datetime, end: dt.datetime, only: list[str]
  ) -> list[str]:
    """Return hosts with per-core rows in ``[start, end)``.

    With an explicit allow-list no scan is needed: those hosts are returned
    and a host without per-core rows is a cheap no-op in ``_migrate``.
    Otherwise the scan runs with the statement timeout disabled.

    Args:
      start (dt.datetime): Window start (inclusive).
      end (dt.datetime): Window end (exclusive).
      only (list[str]): Optional host allow-list (empty means all hosts).

    Returns:
      list[str]: Sorted host names.

    Examples:
      >>> Command()._percore_hosts(None, None, ["c002", "c001", "c001"])
      ['c001', 'c002']
    """
    if only:
      return sorted(set(only))
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cursor.execute(
        "SELECT DISTINCT host FROM host_data WHERE time >= %s AND time < %s "
        f"AND host IS NOT NULL AND {_PERCORE_WHERE}",
        [start, end, list(GPU_TYPES)],
      )
      return sorted(row[0] for row in cursor.fetchall())

  def _migrate(
    self,
    start: dt.datetime,
    end: dt.datetime,
    hosts: list[str],
    apply: bool,
    options: dict[str, Any],
  ) -> Any:
    """Plan (and with ``apply`` write) one host batch in one window.

    All writes happen in one transaction: insert the planned rows, check them
    against the plan, then delete the per-core rows that have a ``dev=''``
    row. Any mismatch raises and rolls the whole window back.

    Args:
      start (dt.datetime): Window start (inclusive).
      end (dt.datetime): Window end (exclusive).
      hosts (list[str]): Host batch.
      apply (bool): Write when true, plan only when false.
      options (dict[str, Any]): Parsed command options.

    Returns:
      Any: The ``CollapsePlan`` for the batch.

    Raises:
      CommandError: When inserted rows differ from the plan or the delete
        count differs from the per-core rows that were summed.

    Examples:
      >>> # plan = Command()._migrate(start, end, ["c001"], False, options)
      >>> Command._migrate  # doctest: +SKIP
    """
    lookback = dt.timedelta(minutes=options["lookback_minutes"])
    gpu = list(GPU_TYPES)
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cols = ", ".join(_COLUMNS)
      percore = _query_frame(
        cursor,
        f"SELECT {cols} FROM host_data WHERE host = ANY(%s) AND time >= %s "
        f"AND time < %s AND {_PERCORE_WHERE}",
        [hosts, start, end, gpu],
        _COLUMNS,
      )
      existing = _query_frame(
        cursor,
        "SELECT host, type, event, time FROM host_data WHERE host = ANY(%s) "
        "AND time >= %s AND time < %s AND dev = '' AND type <> ALL(%s)",
        [hosts, start, end, gpu],
        KEY_COLS,
      )
      context = _query_frame(
        cursor,
        "SELECT DISTINCT host, type, event, time FROM host_data "
        "WHERE host = ANY(%s) AND time >= %s AND time < %s "
        "AND (dev IS NULL OR dev NOT LIKE 'j:%%') AND type <> ALL(%s)",
        [hosts, start - lookback, end, gpu],
        KEY_COLS,
      )
      plan = plan_collapse(
        percore, list(existing.itertuples(index=False, name=None)), context
      )
      if not apply or not plan.percore_rows:
        return plan
      if len(plan.new_rows):
        cursor.executemany(_INSERT_SQL, _records(plan.new_rows))
        self._verify(cursor, plan.new_rows, hosts, start, end, gpu)
      cursor.execute(_DELETE_SQL, [hosts, start, end, gpu])
      if cursor.rowcount != plan.percore_rows:
        raise CommandError(
          f"{start:%Y-%m-%d %H:%M}: deleted {cursor.rowcount} per-core rows but "
          f"summed {plan.percore_rows}; rolling back this window"
        )
    return plan

  def _verify(
    self,
    cursor: Any,
    planned: Any,
    hosts: list[str],
    start: dt.datetime,
    end: dt.datetime,
    gpu: list[str],
  ) -> None:
    """Check the inserted ``dev=''`` rows against the plan.

    Args:
      cursor (Any): Cursor inside the window's transaction.
      planned (Any): Planned rows (``plan_collapse().new_rows``).
      hosts (list[str]): Host batch.
      start (dt.datetime): Window start (inclusive).
      end (dt.datetime): Window end (exclusive).
      gpu (list[str]): GPU types to exclude.

    Returns:
      None

    Raises:
      CommandError: When a planned row is missing or its value or delta
        differs from the stored row.

    Examples:
      >>> # self._verify(cursor, planned, hosts, start, end, gpu)
      >>> Command._verify  # doctest: +SKIP
    """
    stored = _query_frame(
      cursor,
      "SELECT host, type, event, time, value, delta FROM host_data "
      "WHERE host = ANY(%s) AND time >= %s AND time < %s AND dev = '' "
      "AND type <> ALL(%s)",
      [hosts, start, end, gpu],
      [*KEY_COLS, "value", "delta"],
    )
    stored["time"] = pd.to_datetime(stored["time"], utc=True)
    merged = planned.merge(
      stored, on=KEY_COLS, how="left", suffixes=("", "_db"), indicator=True
    )
    if (merged["_merge"] == "left_only").any():
      raise CommandError(
        f"{start:%Y-%m-%d %H:%M}: inserted rows are missing; rolling back"
      )
    for col in ("value", "delta"):
      same = np.isclose(
        merged[col].astype(float),
        merged[f"{col}_db"].astype(float),
        rtol=1e-5,
        atol=1e-6,
        equal_nan=True,
      )
      if not same.all():
        raise CommandError(
          f"{start:%Y-%m-%d %H:%M}: stored {col} differs from plan; "
          "rolling back"
        )
