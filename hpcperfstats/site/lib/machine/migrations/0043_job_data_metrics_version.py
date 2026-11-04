"""Metrics provenance stamps and the backfill-lane cursor row.

``job_data.metrics_version`` / ``metrics_coverage`` / ``metrics_skipped_since``
are nullable columns without defaults, so PostgreSQL adds them without a
table rewrite (bounded at startup). The ``(metrics_version, end_time)``
index backs the backfill lane's version-stale keyset and is created
CONCURRENTLY (``atomic = False``) so startup migrate does not block
``job_data`` writes. ``metrics_scheduler_state`` is a tiny key/value table.

``atomic = False`` commits each step on its own, so a run cancelled part way
(the index build hit the 120s session statement_timeout on hpcperfstats02)
leaves columns behind with the migration unrecorded. The schema steps are
therefore state-only plus a ``RunPython`` that creates only what is missing,
and the index build lifts the timeout and drops any INVALID leftover (as 0044).
"""

from django.db import migrations, models

_COLUMNS = ("metrics_version", "metrics_coverage", "metrics_skipped_since")


def _create_missing(apps, schema_editor):
  """Add the provenance columns and cursor table unless a prior run did."""
  introspection = schema_editor.connection.introspection
  job_data = apps.get_model("machine", "job_data")
  with schema_editor.connection.cursor() as cursor:
    present = {
      c.name for c in introspection.get_table_description(cursor, "job_data")
    }
  for name in _COLUMNS:
    if name not in present:
      schema_editor.add_field(job_data, job_data._meta.get_field(name))
  if "metrics_scheduler_state" not in introspection.table_names():
    schema_editor.create_model(
      apps.get_model("machine", "metrics_scheduler_state")
    )


def _drop_created(apps, schema_editor):
  """Reverse of ``_create_missing``: drop the cursor table and the columns."""
  job_data = apps.get_model("machine", "job_data")
  schema_editor.delete_model(
    apps.get_model("machine", "metrics_scheduler_state")
  )
  for name in _COLUMNS:
    schema_editor.remove_field(job_data, job_data._meta.get_field(name))


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
    ("machine", "0042_job_data_metrics_dirty_idx"),
  ]

  operations = [
    migrations.SeparateDatabaseAndState(
      state_operations=[
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
              models.CharField(
                max_length=64, primary_key=True, serialize=False
              ),
            ),
            ("value", models.JSONField(blank=True, null=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
          ],
          options={
            "db_table": "metrics_scheduler_state",
            "managed": True,
          },
        ),
      ],
    ),
    migrations.RunPython(_create_missing, _drop_created),
    migrations.SeparateDatabaseAndState(
      database_operations=[
        # A list runs one statement per execute, outside any transaction.
        migrations.RunSQL(
          sql=[
            "SET statement_timeout = 0",
            "DROP INDEX CONCURRENTLY IF EXISTS job_data_metrics_version_idx",
            "CREATE INDEX CONCURRENTLY job_data_metrics_version_idx"
            " ON job_data (metrics_version, end_time)",
            "RESET statement_timeout",
          ],
          reverse_sql=[
            "SET statement_timeout = 0",
            "DROP INDEX CONCURRENTLY IF EXISTS job_data_metrics_version_idx",
            "RESET statement_timeout",
          ],
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
