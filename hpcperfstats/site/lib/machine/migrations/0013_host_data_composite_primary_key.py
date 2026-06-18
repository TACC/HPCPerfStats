"""Promote (time, host, type, event) to the primary key for host_data.

Historically, host_data used a unique constraint on (time, host, type, event)
and the primary key on time was dropped when converting to a TimescaleDB
hypertable. This migration makes the logical key the actual primary key at
the database level.

Note: Django's ORM will still treat `time` as the model primary key field;
this migration only aligns the underlying database constraints with the
logical key used by sync_timedb and analysis code.
"""

from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0012_update_host_data_compression_policy"),
  ]

  operations = [
      migrations.RunSQL(
          sql="""
          DO $$
          BEGIN
            -- If the host_data table does not exist, nothing to do.
            IF NOT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = 'public'
                AND table_name = 'host_data'
            ) THEN
              RETURN;
            END IF;

            -- If host_data is a TimescaleDB hypertable with compression enabled,
            -- skip altering constraints because TimescaleDB does not support this
            -- operation while compression is enabled.
            IF EXISTS (
              SELECT 1
              FROM timescaledb_information.hypertables
              WHERE hypertable_schema = 'public'
                AND hypertable_name = 'host_data'
                AND compression_enabled = true
            ) THEN
              RETURN;
            END IF;

            -- Drop the existing unique constraint on (time, host, type, event) if it exists
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

            -- Add composite primary key on (time, host, type, event) if not already present
            IF NOT EXISTS (
              SELECT 1
              FROM pg_constraint c
              JOIN pg_class t ON c.conrelid = t.oid
              JOIN pg_namespace n ON t.relnamespace = n.oid
              WHERE n.nspname = 'public'
                AND t.relname = 'host_data'
                AND c.conname = 'host_data_pkey'
            ) THEN
              ALTER TABLE host_data
              ADD CONSTRAINT host_data_pkey
              PRIMARY KEY (time, host, type, event);
            END IF;
          END;
          $$;
          """,
          reverse_sql="""
          DO $$
          BEGIN
            -- If the host_data table does not exist, nothing to do.
            IF NOT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = 'public'
                AND table_name = 'host_data'
            ) THEN
              RETURN;
            END IF;

            -- If host_data is a TimescaleDB hypertable with compression enabled,
            -- skip altering constraints to avoid unsupported operations.
            IF EXISTS (
              SELECT 1
              FROM timescaledb_information.hypertables
              WHERE hypertable_schema = 'public'
                AND hypertable_name = 'host_data'
                AND compression_enabled = true
            ) THEN
              RETURN;
            END IF;

            -- Drop the composite primary key if it exists
            IF EXISTS (
              SELECT 1
              FROM pg_constraint c
              JOIN pg_class t ON c.conrelid = t.oid
              JOIN pg_namespace n ON t.relnamespace = n.oid
              WHERE n.nspname = 'public'
                AND t.relname = 'host_data'
                AND c.conname = 'host_data_pkey'
            ) THEN
              ALTER TABLE host_data
              DROP CONSTRAINT host_data_pkey;
            END IF;

            -- Restore the unique constraint on (time, host, type, event) if missing
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
      ),
  ]

