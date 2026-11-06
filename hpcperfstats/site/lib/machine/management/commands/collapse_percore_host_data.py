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
  _PERCORE_WHERE: SQL predicate selecting per-core rows (hardware dev, type
    not in the keep-``dev`` set of GPUs and ``host_power_socket``).
  _COLUMNS: ``host_data`` columns read for planning and written on insert.
  _INSERT_SQL: Insert of one planned whole-node row; an existing row wins.
  _DELETE_SQL: Delete of the window's per-core rows, with the same predicate
    the plan read them by. It runs after every key was given a ``dev=''`` row
    and checked, so it needs no per-row probe for one (that probe read every
    device row of a (host, time) per deleted row and made a window take
    tens of minutes on hpcperfstats01). The row count must match the plan.
    It targets each chunk (``{chunk}``) rather than the hypertable: a
    hypertable DELETE locks every chunk while planning, so compression of
    any other day made every window time out (hpcperfstats04, 2026-11-05).
  _WINDOW_CHUNKS_SQL: Qualified names of the ``host_data`` chunks that
    overlap the window, for ``_DELETE_SQL``.
  _DISCOVER_DAYS_SQL: Distinct UTC days that hold per-core rows
    (``_PERCORE_WHERE``), found over the whole table.
  _NO_TIMEOUT_SQL: Disables the statement timeout for the current transaction;
    one-off repair queries scan large windows and must not be cancelled.
  _DML_DECOMPRESS_SQL: Lifts Timescale's per-transaction cap on rows
    decompressed by INSERT/DELETE (default 100000), so a window can be
    repaired in place on a compressed chunk.
  _LOCK_SQLSTATES: SQLSTATEs of a window that could not get its locks in
    time (lock_not_available from lock_timeout, deadlock_detected); such a
    window is rolled back, deferred and retried later.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connection, transaction

from hpcperfstats.dbload.lib.host_data_percore_collapse import (
  KEEP_DEV_TYPES,
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
  "DELETE FROM {chunk} WHERE host = ANY(%s) AND time >= %s AND time < %s "
  f"AND {_PERCORE_WHERE}"
)
_WINDOW_CHUNKS_SQL = (
  "SELECT format('%%I.%%I', chunk_schema, chunk_name) "
  "FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data' "
  "AND range_start < %s AND range_end > %s"
)
_DISCOVER_DAYS_SQL = (
  "SELECT DISTINCT date_trunc('day', time AT TIME ZONE 'UTC') FROM host_data "
  f"WHERE {_PERCORE_WHERE}"
)
_NO_TIMEOUT_SQL = "SET LOCAL statement_timeout = 0"
_DML_DECOMPRESS_SQL = (
  "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
)
_LOCK_SQLSTATES = frozenset({"55P03", "40P01"})


def _lock_sqlstate(exc: BaseException) -> str | None:
  """Return the SQLSTATE when ``exc`` is a lock wait that ran out.

  Django re-raises the driver's error as ``OperationalError`` with the
  original as ``__cause__``; psycopg 3 names its code ``sqlstate``,
  psycopg2 ``pgcode``.

  Args:
    exc (BaseException): Error raised by a window's transaction.

  Returns:
    str | None: ``"55P03"`` (lock_timeout) or ``"40P01"`` (deadlock), else
    ``None`` for any other error.

  Examples:
    >>> class E(Exception):
    ...   sqlstate = "55P03"
    >>> err = OperationalError("x")
    >>> err.__cause__ = E()
    >>> _lock_sqlstate(err)
    '55P03'
    >>> _lock_sqlstate(OperationalError("x")) is None
    True
  """
  cause = exc.__cause__
  code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
  return code if code in _LOCK_SQLSTATES else None


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
    parser.add_argument(
      "--lock-timeout-seconds",
      type=int,
      default=30,
      help="Give up a window whose locks are not granted in this time, "
      "defer it and retry it later (0 waits forever).",
    )
    parser.add_argument(
      "--lock-retry-sleep-seconds",
      type=float,
      default=60.0,
      help="Pause between passes over deferred (locked) windows.",
    )

  def handle(self, *args: Any, **options: Any) -> None:
    """Run the repair (override of ``BaseCommand.handle``).

    ``BaseCommand.handle`` is the hook Django calls after parsing options.

    Args:
      *args (Any): Unused; Django may pass positional leftovers.
      **options (Any): Parsed options: ``since``, ``until``, ``apply``,
        ``host``, ``window_hours``, ``host_batch``, ``lookback_minutes``,
        ``lock_timeout_seconds`` and ``lock_retry_sleep_seconds``.

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
    deferred: list[tuple[dt.datetime, dt.datetime, list[str]]] = []
    for start, end in (
      w
      for first, last in ranges
      for w in _windows(first, last, options["window_hours"])
    ):
      hosts = self._percore_hosts(start, end, options["host"])
      self.stdout.write(
        f"{start:%Y-%m-%d %H:%M} window hosts_with_percore={len(hosts)}"
      )
      deferred += [
        (start, end, batch)
        for batch in _chunks(hosts, options["host_batch"])
        if not self._run_batch(start, end, batch, apply, options, totals)
      ]
    passes = 0
    while deferred:
      passes += 1
      sleep_s = options.get("lock_retry_sleep_seconds", 60.0)
      self.stdout.write(
        f"RETRY pass={passes} locked_batches={len(deferred)} in {sleep_s:g}s"
      )
      time.sleep(sleep_s)
      deferred = [
        w for w in deferred if not self._run_batch(*w, apply, options, totals)
      ]
    self.stdout.write(
      "TOTAL " + " ".join(f"{k}={v}" for k, v in totals.items())
    )

  def _run_batch(
    self,
    start: dt.datetime,
    end: dt.datetime,
    hosts: list[str],
    apply: bool,
    options: dict[str, Any],
    totals: dict[str, int],
  ) -> bool:
    """Repair one host batch in one window, or defer it when it is locked.

    A window whose locks are not granted within ``--lock-timeout-seconds``
    (another session holds the chunk, for example a compress, decompress or
    VACUUM) or that hits a deadlock is rolled back and reported ``LOCKED``;
    the caller retries it later. Any other error propagates.

    Args:
      start (dt.datetime): Window start (inclusive).
      end (dt.datetime): Window end (exclusive).
      hosts (list[str]): Host batch.
      apply (bool): Write when true, plan only when false.
      options (dict[str, Any]): Parsed command options.
      totals (dict[str, int]): Running totals, updated for a finished batch.

    Returns:
      bool: ``True`` when the batch finished, ``False`` when it was deferred.

    Raises:
      OperationalError: A database error that is not a lock wait or deadlock.

    Examples:
      >>> # done = Command()._run_batch(start, end, ["c001"], True, opts, t)
      >>> Command._run_batch  # doctest: +SKIP
    """
    try:
      plan = self._migrate(start, end, hosts, apply, options)
    except OperationalError as exc:
      code = _lock_sqlstate(exc)
      if code is None:
        raise OperationalError(*exc.args) from exc
      self.stdout.write(
        f"{start:%Y-%m-%d %H:%M} hosts={len(hosts)} LOCKED sqlstate={code}; "
        "rolled back, deferred, will retry"
      )
      return False
    totals["percore_rows"] += plan.percore_rows
    totals["groups"] += plan.groups
    totals["kept_existing"] += plan.kept_existing
    totals["new_rows"] += len(plan.new_rows)
    if plan.percore_rows:
      self.stdout.write(
        f"{start:%Y-%m-%d %H:%M} hosts={len(hosts)} "
        f"percore_rows={plan.percore_rows} groups={plan.groups} "
        f"kept_existing={plan.kept_existing} new_rows={len(plan.new_rows)}"
      )
    return True

  def _discover_days(self) -> list[dt.datetime]:
    """Return every UTC day that holds per-core rows (any non-GPU type).

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
      cursor.execute(_DISCOVER_DAYS_SQL, [list(KEEP_DEV_TYPES)])
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
        [start, end, list(KEEP_DEV_TYPES)],
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
    gpu = list(KEEP_DEV_TYPES)
    with transaction.atomic(), connection.cursor() as cursor:
      cursor.execute(_NO_TIMEOUT_SQL)
      cursor.execute(_DML_DECOMPRESS_SQL)
      lock_s = int(options.get("lock_timeout_seconds", 30))
      cursor.execute(f"SET LOCAL lock_timeout = '{lock_s}s'")
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
      cursor.execute(_WINDOW_CHUNKS_SQL, [end, start])
      deleted = 0
      for (chunk,) in cursor.fetchall():
        sql = _DELETE_SQL.format(chunk=chunk)
        cursor.execute(sql, [hosts, start, end, gpu])
        deleted += cursor.rowcount
      if deleted != plan.percore_rows:
        raise CommandError(
          f"{start:%Y-%m-%d %H:%M}: deleted {deleted} per-core rows but "
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
      gpu (list[str]): Keep-``dev`` types to exclude (GPUs and
        ``host_power_socket``).

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
