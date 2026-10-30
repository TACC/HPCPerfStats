"""Partial index on job_data.metrics_dirty_at for the dirty catch-up listing.

Only stamped rows are indexed (normally a handful), built CONCURRENTLY so the
startup migrate does not block job_data writes.
"""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
    ("machine", "0041_job_data_metrics_dirty_at"),
  ]

  operations = [
    migrations.SeparateDatabaseAndState(
      database_operations=[
        migrations.RunSQL(
          sql="""
          CREATE INDEX CONCURRENTLY IF NOT EXISTS job_data_metrics_dirty_idx
          ON job_data (metrics_dirty_at)
          WHERE metrics_dirty_at IS NOT NULL;
          """,
          reverse_sql="""
          DROP INDEX CONCURRENTLY IF EXISTS job_data_metrics_dirty_idx;
          """,
        ),
      ],
      state_operations=[
        migrations.AddIndex(
          model_name="job_data",
          index=models.Index(
            fields=["metrics_dirty_at"],
            name="job_data_metrics_dirty_idx",
            condition=Q(metrics_dirty_at__isnull=False),
          ),
        ),
      ],
    ),
  ]
