"""Normalize host_data.dev NULL→'' and include ``dev`` in uniqueness.

Multi-GPU ingest must store one row per (time, host, type, event, dev).
PostgreSQL UNIQUE treats NULL as distinct, so NULL ``dev`` must become '' first.

On compressed Timescale hypertables the unique constraint rewrite is skipped
(same guard as 0020); Django state still records the new unique_together.
"""
from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0029_proc_data_host_proc_fields"),
  ]

  operations = [
      migrations.SeparateDatabaseAndState(
          state_operations=[
              migrations.AlterUniqueTogether(
                  name="host_data",
                  unique_together={("time", "host", "type", "event", "dev")},
              ),
          ],
          database_operations=[
              migrations.RunSQL(
                  sql="""
                  DO $$
                  BEGIN
                    IF NOT EXISTS (
                      SELECT 1
                      FROM information_schema.tables
                      WHERE table_schema = 'public'
                        AND table_name = 'host_data'
                    ) THEN
                      RETURN;
                    END IF;

                    -- Prefer empty string so UNIQUE(time,host,type,event,dev) is meaningful.
                    UPDATE host_data SET dev = '' WHERE dev IS NULL;

                    -- Avoid unsupported constraint rewrites on compressed hypertables.
                    IF EXISTS (
                      SELECT 1
                      FROM timescaledb_information.hypertables
                      WHERE hypertable_schema = 'public'
                        AND hypertable_name = 'host_data'
                        AND compression_enabled = true
                    ) THEN
                      RETURN;
                    END IF;

                    IF EXISTS (
                      SELECT 1
                      FROM pg_constraint c
                      JOIN pg_class t ON c.conrelid = t.oid
                      JOIN pg_namespace n ON t.relnamespace = n.oid
                      WHERE n.nspname = 'public'
                        AND t.relname = 'host_data'
                        AND c.conname = 'host_data_time_host_type_event_key'
                    ) THEN
                      ALTER TABLE host_data
                      DROP CONSTRAINT host_data_time_host_type_event_key;
                    END IF;

                    IF NOT EXISTS (
                      SELECT 1
                      FROM pg_constraint c
                      JOIN pg_class t ON c.conrelid = t.oid
                      JOIN pg_namespace n ON t.relnamespace = n.oid
                      WHERE n.nspname = 'public'
                        AND t.relname = 'host_data'
                        AND c.conname = 'host_data_time_host_type_event_dev_key'
                    ) THEN
                      ALTER TABLE host_data
                      ADD CONSTRAINT host_data_time_host_type_event_dev_key
                      UNIQUE (time, host, type, event, dev);
                    END IF;
                  END;
                  $$;
                  """,
                  reverse_sql=migrations.RunSQL.noop,
              ),
          ],
      ),
  ]
