# Generated for DB speed: index job_data on date columns used by filters/order_by.
"""Add indexes on job_data submit_time, start_time, end_time for faster filters and ordering."""
from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0003_create_table_index"),
  ]

  operations = [
      migrations.RunSQL(
          "CREATE INDEX IF NOT EXISTS job_data_submit_time_idx ON job_data (submit_time);",
          migrations.RunSQL.noop,
      ),
      migrations.RunSQL(
          "CREATE INDEX IF NOT EXISTS job_data_start_time_idx ON job_data (start_time);",
          migrations.RunSQL.noop,
      ),
      migrations.RunSQL(
          "CREATE INDEX IF NOT EXISTS job_data_end_time_idx ON job_data (end_time);",
          migrations.RunSQL.noop,
      ),
  ]
