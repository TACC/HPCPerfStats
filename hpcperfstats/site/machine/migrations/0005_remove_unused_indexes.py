# Remove indexes that are unused or redundant.
# - job_data submit_time/start_time: no filters or order_by use them; only end_time is used.
# - host_data idx_host: redundant with composite index (host, time) which covers host-only lookups.
from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0004_job_data_date_indexes"),
  ]

  operations = [
      migrations.RunSQL(
          "DROP INDEX IF EXISTS job_data_submit_time_idx;",
          "CREATE INDEX IF NOT EXISTS job_data_submit_time_idx ON job_data (submit_time);",
      ),
      migrations.RunSQL(
          "DROP INDEX IF EXISTS job_data_start_time_idx;",
          "CREATE INDEX IF NOT EXISTS job_data_start_time_idx ON job_data (start_time);",
      ),
      migrations.RunSQL(
          "DROP INDEX IF EXISTS idx_host;",
          "CREATE INDEX idx_host ON host_data (host);",
      ),
  ]
