"""Management command: print PostgreSQL session counts from pg_stat_activity."""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
  help = "Print connection counts from pg_stat_activity (default database only)."

  def handle(self, *args, **options):
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
