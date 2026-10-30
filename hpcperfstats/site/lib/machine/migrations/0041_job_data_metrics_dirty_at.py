"""Add job_data.metrics_dirty_at: host_data landed after metrics were built.

Nullable column without a default, so PostgreSQL adds it without a table
rewrite (bounded at startup).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
    ("machine", "0040_host_data_compression_schedule"),
  ]

  operations = [
    migrations.AddField(
      model_name="job_data",
      name="metrics_dirty_at",
      field=models.DateTimeField(blank=True, null=True),
    ),
  ]
