from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
  atomic = False

  dependencies = [
      ("machine", "0020_host_data_primary_key_contract"),
  ]

  operations = [
      migrations.SeparateDatabaseAndState(
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  SET statement_timeout = 0;
                  CREATE INDEX IF NOT EXISTS host_data_host_time_desc_idx
                  ON host_data (host, time DESC);
                  """,
                  reverse_sql="""
                  SET statement_timeout = 0;
                  DROP INDEX IF EXISTS host_data_host_time_desc_idx;
                  """,
              ),
          ],
          state_operations=[
              migrations.AddIndex(
                  model_name="host_data",
                  index=models.Index(
                      fields=["host", "-time"],
                      name="host_data_host_time_desc_idx",
                  ),
              ),
          ],
      ),
      migrations.SeparateDatabaseAndState(
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  SET statement_timeout = 0;
                  CREATE INDEX IF NOT EXISTS host_data_jid_time_desc_idx
                  ON host_data (jid, time DESC);
                  """,
                  reverse_sql="""
                  SET statement_timeout = 0;
                  DROP INDEX IF EXISTS host_data_jid_time_desc_idx;
                  """,
              ),
          ],
          state_operations=[
              migrations.AddIndex(
                  model_name="host_data",
                  index=models.Index(
                      fields=["jid", "-time"],
                      name="host_data_jid_time_desc_idx",
                  ),
              ),
          ],
      ),
      migrations.SeparateDatabaseAndState(
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  SET statement_timeout = 0;
                  CREATE INDEX IF NOT EXISTS metrics_data_stale_jid_idx
                  ON metrics_data (jid)
                  WHERE value IS NULL AND (no_data_reason IS NULL OR no_data_reason = '');
                  """,
                  reverse_sql="""
                  SET statement_timeout = 0;
                  DROP INDEX IF EXISTS metrics_data_stale_jid_idx;
                  """,
              ),
          ],
          state_operations=[
              migrations.AddIndex(
                  model_name="metrics_data",
                  index=models.Index(
                      fields=["jid"],
                      name="metrics_data_stale_jid_idx",
                      condition=Q(value__isnull=True)
                      & (Q(no_data_reason__isnull=True) | Q(no_data_reason="")),
                  ),
              ),
          ],
      ),
  ]
