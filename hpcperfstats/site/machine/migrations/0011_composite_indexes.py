"""Add composite indexes for job_data and metrics_data matching query patterns.

- job_data: composite indexes for common filters combining end_time with
  username, queue, and state.
- metrics_data: composite index for (jid, metric) to speed joins and
  metric-based filters.

Hypertable and compression for host_data are already configured in 0001_initial.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0010_alter_apikey_options_alter_job_data_end_time_and_more"),
  ]

  operations = [
      # Composite indexes for job_data
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(
              fields=["end_time", "username"],
              name="job_data_end_time_username_idx",
          ),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(
              fields=["queue", "end_time"],
              name="job_data_queue_end_time_idx",
          ),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(
              fields=["end_time", "state"],
              name="job_data_end_time_state_idx",
          ),
      ),
      # Composite index for metrics_data
      migrations.AddIndex(
          model_name="metrics_data",
          index=models.Index(
              fields=["jid", "metric"],
              name="metrics_data_jid_metric_idx",
          ),
      ),
  ]

