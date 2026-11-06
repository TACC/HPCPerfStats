"""Pin the host_data Timescale compression policy to a daily 01:00 schedule.

The policy defaults to a floating 12 hour interval, so its multi-hour chunk
rewrite landed mid-day and took WAL and I/O from ingest. Pin it to a daily
fixed schedule at 01:00 in the INI ``timezone`` (DST-safe) with no
``max_runtime``: a run that overruns finishes its chunk instead of rolling
back.

Bounded catalog change only (``alter_job``): no chunk is compressed or touched,
and it skips only without Timescale. With Timescale but no policy the pin
raises, so the migration stays unapplied instead of being recorded with the
schedule unpinned (hpcperfstats03: 0032 had skipped, so 0040 found no policy
and silently did nothing). Shared by
migration ``0040`` and the ``0032`` Phase 2 policy restore so a re-added policy
gets the same schedule.

Attributes:
  COMPRESSION_PROCS: Timescale job procedure names that compress ``host_data``.
  FIND_POLICY_JOB: SQL selecting the ``host_data`` compression policy job id.
  PIN_DAILY_SCHEDULE: ``alter_job`` SQL for the daily 01:00 local schedule.
  RESTORE_FLOATING_SCHEDULE: ``alter_job`` SQL restoring the 12 hour interval.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg

COMPRESSION_PROCS = ("policy_compression", "policy_columnstore")

FIND_POLICY_JOB = """
SELECT job_id
FROM timescaledb_information.jobs
WHERE hypertable_name = 'host_data'
  AND proc_name::text = ANY(%s::text[])
ORDER BY job_id
LIMIT 1
"""

# initial_start: next 01:00 local time; fixed_schedule keeps that time of day.
PIN_DAILY_SCHEDULE = """
SELECT alter_job(
  %s,
  scheduled => true,
  schedule_interval => INTERVAL '1 day',
  fixed_schedule => true,
  initial_start => (
    date_trunc('day', now() AT TIME ZONE %s) + INTERVAL '1 day 1 hour'
  ) AT TIME ZONE %s,
  timezone => %s,
  max_runtime => INTERVAL '0'
)
"""

RESTORE_FLOATING_SCHEDULE = """
SELECT alter_job(
  %s,
  schedule_interval => INTERVAL '12 hours',
  fixed_schedule => false
)
"""


def _policy_job_id(cursor: Any, *, required: bool = False) -> int | None:
  """
  Return the host_data compression policy job id.

  Args:
    cursor (Any): Open database cursor.
    required (bool): Raise instead of returning None when Timescale is
      installed but the policy is missing (the forward pin needs it).

  Returns:
    int | None: Policy job id; None without Timescale, or without the policy
      when ``required`` is False.

  Raises:
    RuntimeError: ``required`` and Timescale has no host_data policy job.

  Examples:
    >>> _policy_job_id(None, required=True)  # doctest: +SKIP
  """
  cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
  if cursor.fetchone() is None:
    return None
  cursor.execute(FIND_POLICY_JOB, [list(COMPRESSION_PROCS)])
  row = cursor.fetchone()
  if row is None and required:
    raise RuntimeError(
      "host_data compression policy missing; 0032 did not really run, so "
      "this migration stays unapplied (docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md)"
    )
  return None if row is None else int(row[0])


def pin_compression_schedule(apps: Any, schema_editor: Any) -> None:
  """
  Pin the host_data compression policy to a daily 01:00 local fixed schedule.

  Args:
    apps (Any): Historical app registry (unused).
    schema_editor (Any): Schema editor holding the migration connection.

  Returns:
    None

  Raises:
    RuntimeError: Timescale is installed but host_data has no compression
      policy, so there is nothing to pin and the migration must not be
      recorded as applied.

  Examples:
    >>> pin_compression_schedule(None, None)  # doctest: +SKIP
  """
  del apps
  conn = schema_editor.connection
  if conn.vendor != "postgresql":
    return
  tz = str(cfg.get_timezone())
  with conn.cursor() as cursor:
    job_id = _policy_job_id(cursor, required=True)
    if job_id is not None:
      cursor.execute(PIN_DAILY_SCHEDULE, [job_id, tz, tz, tz])


def restore_compression_schedule(apps: Any, schema_editor: Any) -> None:
  """
  Restore the floating 12 hour compression schedule (reverse of the pin).

  Args:
    apps (Any): Historical app registry (unused).
    schema_editor (Any): Schema editor holding the migration connection.

  Returns:
    None

  Examples:
    >>> restore_compression_schedule(None, None)  # doctest: +SKIP
  """
  del apps
  conn = schema_editor.connection
  if conn.vendor != "postgresql":
    return
  with conn.cursor() as cursor:
    job_id = _policy_job_id(cursor)
    if job_id is not None:
      cursor.execute(RESTORE_FLOATING_SCHEDULE, [job_id])
