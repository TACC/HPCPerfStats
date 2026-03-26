"""Add metrics_data.no_data_reason for explicit no-data vs incomplete nulls."""

from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0013_host_data_composite_primary_key"),
  ]

  operations = [
      migrations.AddField(
          model_name="metrics_data",
          name="no_data_reason",
          field=models.CharField(blank=True, max_length=512, null=True),
      ),
  ]
