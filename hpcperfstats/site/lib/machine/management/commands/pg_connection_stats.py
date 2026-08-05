"""
Management command: print PostgreSQL session counts from pg_stat_activity.
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
  """Print pg_stat_activity session counts for the default DB.

  Subclasses Django ``BaseCommand``, which is the framework entry for
  ``manage.py`` commands (argument parsing, ``handle`` dispatch, stdout/stderr).
  This subclass only implements ``handle`` to query PostgreSQL session counts.

  Invoke: ``manage.py pg_connection_stats``.
  """

  help = "Print connection counts from pg_stat_activity (default database only)."

  def handle(self, *args: Any, **options: Any) -> None:
    """Run the command body (override of ``BaseCommand.handle``).

    ``BaseCommand.handle`` is the hook Django calls after parsing options;
    subclasses must implement it to perform the command work.

    Args:
      *args (Any): Unused; Django may pass positional leftovers.
      **options (Any): Standard ``BaseCommand`` options (``verbosity``,
        ``settings``, ``pythonpath``, ``traceback``, ``no_color``,
        ``force_color``, ``skip_checks``). This override does not read them.

    Returns:
      None

    Examples:
      >>> # manage.py pg_connection_stats
      >>> Command().handle(verbosity=1)  # doctest: +SKIP
    """
    if connection.vendor != "postgresql":
      self.stderr.write("This command requires PostgreSQL (default database).")
      return
    with connection.cursor() as cursor:
      cursor.execute(
          """
          SELECT
            count(*) AS total,
            count(*) FILTER (WHERE state = 'active') AS active,
            count(*) FILTER (WHERE state = 'idle') AS idle,
            count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_tx
          FROM pg_stat_activity
          WHERE datname = current_database()
          """
      )
      row = cursor.fetchone()
    if not row:
      self.stdout.write("No rows returned.")
      return
    total, active, idle, idle_in_tx = row
    self.stdout.write(
        "pg_stat_activity for current_database(): "
        "total=%s active=%s idle=%s idle_in_transaction=%s"
        % (total, active, idle, idle_in_tx))
