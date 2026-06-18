"""Add job_data.metrics_distinct_time_count for metrics invalidation."""

from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0014_metrics_data_no_data_reason"),
  ]

  operations = [
      migrations.AddField(
          model_name="job_data",
          name="metrics_distinct_time_count",
          field=models.IntegerField(blank=True, null=True),
      ),
  ]
