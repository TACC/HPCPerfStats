"""Pin the host_data Timescale compression policy to a daily 01:00 schedule.

The policy defaults to a floating 12 hour interval, so its multi-hour chunk
rewrite landed mid-day and took WAL and I/O from ingest. Pin it to a daily
fixed schedule at 01:00 in the INI ``timezone`` (DST-safe) with no
``max_runtime``: a run that overruns finishes its chunk instead of rolling
back.

Bounded catalog change only (``alter_job``): no chunk is compressed or touched,
and it soft-skips when the extension or the policy is absent. Shared by
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
  AND proc_name IN %s
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


def _policy_job_id(cursor: Any) -> int | None:
  """
  Return the host_data compression policy job id, or None when absent.

  Args:
    cursor (Any): Open database cursor.

  Returns:
    int | None: Policy job id; None without Timescale or the policy.

  Examples:
    >>> _policy_job_id(None)  # doctest: +SKIP
  """
  cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
  if cursor.fetchone() is None:
    return None
  cursor.execute(FIND_POLICY_JOB, [COMPRESSION_PROCS])
  row = cursor.fetchone()
  return None if row is None else int(row[0])


def pin_compression_schedule(apps: Any, schema_editor: Any) -> None:
  """
  Pin the host_data compression policy to a daily 01:00 local fixed schedule.

  Args:
    apps (Any): Historical app registry (unused).
    schema_editor (Any): Schema editor holding the migration connection.

  Returns:
    None

  Examples:
    >>> pin_compression_schedule(None, None)  # doctest: +SKIP
  """
  del apps
  conn = schema_editor.connection
  if conn.vendor != "postgresql":
    return
  tz = str(cfg.get_timezone())
  with conn.cursor() as cursor:
    job_id = _policy_job_id(cursor)
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
