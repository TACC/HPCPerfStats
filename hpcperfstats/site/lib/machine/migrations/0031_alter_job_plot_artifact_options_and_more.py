"""State-only: record managed=True for job_plot_artifact and public_metrics_artifact.

0017/0024 created these tables with options={"db_table": …} only, while models.py
declares managed = True. managed is a Django Meta option with no DDL — this
migration closes model/migration drift without touching the database.

Precedent: 0010_alter_apikey_options… for the same omission on apikey.
"""
from django.db import migrations


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0030_host_data_unique_include_dev"),
  ]

  operations = [
      migrations.AlterModelOptions(
          name="job_plot_artifact",
          options={"managed": True},
      ),
      migrations.AlterModelOptions(
          name="public_metrics_artifact",
          options={"managed": True},
      ),
  ]
