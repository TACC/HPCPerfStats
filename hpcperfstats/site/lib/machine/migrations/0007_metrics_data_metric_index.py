# Add index on metrics_data.metric for distinct("metric") in home_options.
from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0006_add_query_indexes"),
  ]

  operations = [
      migrations.AddIndex(
          model_name="metrics_data",
          index=models.Index(fields=["metric"], name="metrics_data_metric_idx"),
      ),
  ]
