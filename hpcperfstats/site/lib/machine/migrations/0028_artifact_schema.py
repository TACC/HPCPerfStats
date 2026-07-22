from django.db import migrations, models


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0027_job_data_telemetry_bounds"),
  ]

  operations = [
      migrations.AddField(
          model_name="job_plot_artifact",
          name="artifact_schema",
          field=models.IntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="job_detail_artifact",
          name="artifact_schema",
          field=models.IntegerField(blank=True, null=True),
      ),
  ]
