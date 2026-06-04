"""GIN index for job_data.host_list containment; composite (metric, value) for extended search."""

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
      ("machine", "0025_public_metrics_artifact_rebuild_required"),
  ]

  operations = [
      migrations.SeparateDatabaseAndState(
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  SET statement_timeout = 0;
                  CREATE INDEX IF NOT EXISTS job_data_host_list_gin_idx
                  ON job_data USING GIN (host_list);
                  """,
                  reverse_sql="""
                  SET statement_timeout = 0;
                  DROP INDEX IF EXISTS job_data_host_list_gin_idx;
                  """,
              ),
          ],
          state_operations=[
              migrations.AddIndex(
                  model_name="job_data",
                  index=GinIndex(
                      fields=["host_list"],
                      name="job_data_host_list_gin_idx",
                  ),
              ),
          ],
      ),
      migrations.SeparateDatabaseAndState(
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  SET statement_timeout = 0;
                  CREATE INDEX IF NOT EXISTS metrics_data_metric_value_idx
                  ON metrics_data (metric, value);
                  """,
                  reverse_sql="""
                  SET statement_timeout = 0;
                  DROP INDEX IF EXISTS metrics_data_metric_value_idx;
                  """,
              ),
          ],
          state_operations=[
              migrations.AddIndex(
                  model_name="metrics_data",
                  index=models.Index(
                      fields=["metric", "value"],
                      name="metrics_data_metric_value_idx",
                  ),
              ),
          ],
      ),
  ]
