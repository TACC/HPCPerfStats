"""Metrics provenance stamps and the backfill-lane cursor row.

``job_data.metrics_version`` / ``metrics_coverage`` / ``metrics_skipped_since``
are nullable columns without defaults, so PostgreSQL adds them without a
table rewrite (bounded at startup). The ``(metrics_version, end_time)``
index backs the backfill lane's version-stale keyset and is created
CONCURRENTLY (``atomic = False``) so startup migrate does not block
``job_data`` writes. ``metrics_scheduler_state`` is a tiny key/value table.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
    ("machine", "0042_job_data_metrics_dirty_idx"),
  ]

  operations = [
    migrations.AddField(
      model_name="job_data",
      name="metrics_version",
      field=models.IntegerField(blank=True, null=True),
    ),
    migrations.AddField(
      model_name="job_data",
      name="metrics_coverage",
      field=models.FloatField(blank=True, null=True),
    ),
    migrations.AddField(
      model_name="job_data",
      name="metrics_skipped_since",
      field=models.DateTimeField(blank=True, null=True),
    ),
    migrations.CreateModel(
      name="metrics_scheduler_state",
      fields=[
        (
          "key",
          models.CharField(max_length=64, primary_key=True, serialize=False),
        ),
        ("value", models.JSONField(blank=True, null=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
      ],
      options={
        "db_table": "metrics_scheduler_state",
        "managed": True,
      },
    ),
    migrations.SeparateDatabaseAndState(
      database_operations=[
        migrations.RunSQL(
          sql="""
          CREATE INDEX CONCURRENTLY IF NOT EXISTS job_data_metrics_version_idx
          ON job_data (metrics_version, end_time);
          """,
          reverse_sql="""
          DROP INDEX CONCURRENTLY IF EXISTS job_data_metrics_version_idx;
          """,
        ),
      ],
      state_operations=[
        migrations.AddIndex(
          model_name="job_data",
          index=models.Index(
            fields=["metrics_version", "end_time"],
            name="job_data_metrics_version_idx",
          ),
        ),
      ],
    ),
  ]
