"""Align host_data DB constraints with Django model primary-key contract.

Django model `host_data` treats `time` as the primary key. Migration 0013 could
promote a composite primary key, which diverges from ORM identity semantics.
This migration restores `time` as PK and ensures composite uniqueness remains.
"""
from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0019_job_data_host_data_schema_json"),
  ]

  operations = [
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
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
               AND tc.table_name = kcu.table_name
              WHERE tc.table_schema = 'public'
                AND tc.table_name = 'host_data'
                AND tc.constraint_type = 'PRIMARY KEY'
                AND tc.constraint_name = 'host_data_pkey'
              GROUP BY tc.constraint_name
              HAVING COUNT(*) > 1
            ) THEN
              ALTER TABLE host_data DROP CONSTRAINT host_data_pkey;
            END IF;

            IF NOT EXISTS (
              SELECT 1
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
               AND tc.table_name = kcu.table_name
              WHERE tc.table_schema = 'public'
                AND tc.table_name = 'host_data'
                AND tc.constraint_type = 'PRIMARY KEY'
                AND tc.constraint_name = 'host_data_pkey'
                AND kcu.column_name = 'time'
              GROUP BY tc.constraint_name
              HAVING COUNT(*) = 1
            ) THEN
              ALTER TABLE host_data ADD CONSTRAINT host_data_pkey PRIMARY KEY (time);
            END IF;

            IF NOT EXISTS (
              SELECT 1
              FROM pg_constraint c
              JOIN pg_class t ON c.conrelid = t.oid
              JOIN pg_namespace n ON t.relnamespace = n.oid
              WHERE n.nspname = 'public'
                AND t.relname = 'host_data'
                AND c.conname = 'host_data_time_host_type_event_key'
            ) THEN
              ALTER TABLE host_data
              ADD CONSTRAINT host_data_time_host_type_event_key
              UNIQUE (time, host, type, event);
            END IF;
          END;
          $$;
          """,
          reverse_sql=migrations.RunSQL.noop,
      ),
  ]
