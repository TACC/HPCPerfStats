from django.db import migrations, models


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0026_job_host_list_gin_and_metrics_metric_value_idx"),
  ]

  operations = [
      migrations.AddField(
          model_name="job_data",
          name="telemetry_first_time",
          field=models.DateTimeField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="job_data",
          name="telemetry_last_time",
          field=models.DateTimeField(blank=True, null=True),
      ),
  ]
