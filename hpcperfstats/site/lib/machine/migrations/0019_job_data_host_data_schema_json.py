from django.db import migrations, models


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0018_statsro_connect_current_database"),
  ]

  operations = [
      migrations.AddField(
          model_name="job_data",
          name="host_data_schema_json",
          field=models.JSONField(blank=True, null=True),
      ),
  ]
