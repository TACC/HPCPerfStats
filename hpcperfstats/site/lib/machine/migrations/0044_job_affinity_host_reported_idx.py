"""Index ``job_affinity (host, reported_at)`` for the live co-tenancy check.

``job_host_data_scope.job_exclusive_hosts`` asks which other jobs reported
``%affinity`` on a job's hosts since the job started. The composite primary
key leads with ``jid``, so without this index that lookup scans the table on
every ``jid_table`` build. ``job_affinity`` holds one row per (jid, host); the
index is created CONCURRENTLY (``atomic = False``) so startup migrate stays
bounded and does not block listend's affinity upserts.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
    ("machine", "0043_job_data_metrics_version"),
  ]

  operations = [
    migrations.SeparateDatabaseAndState(
      database_operations=[
        migrations.RunSQL(
          sql="""
          CREATE INDEX CONCURRENTLY IF NOT EXISTS job_affinity_host_reported_idx
          ON job_affinity (host, reported_at);
          """,
          reverse_sql="""
          DROP INDEX CONCURRENTLY IF EXISTS job_affinity_host_reported_idx;
          """,
        ),
      ],
      state_operations=[
        migrations.AddIndex(
          model_name="job_affinity",
          index=models.Index(
            fields=["host", "reported_at"],
            name="job_affinity_host_reported_idx",
          ),
        ),
      ],
    ),
  ]
