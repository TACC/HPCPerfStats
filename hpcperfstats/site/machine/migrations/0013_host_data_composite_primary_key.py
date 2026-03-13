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
          -- Drop the existing unique constraint on (time, host, type, event) if it exists
          ALTER TABLE host_data
          DROP CONSTRAINT IF EXISTS host_data_time_host_type_event_key;

          -- Add composite primary key on (time, host, type, event)
          ALTER TABLE host_data
          ADD CONSTRAINT host_data_pkey
          PRIMARY KEY (time, host, type, event);
          """,
          reverse_sql="""
          -- Reverse: drop the composite primary key and restore unique constraint
          ALTER TABLE host_data
          DROP CONSTRAINT IF EXISTS host_data_pkey;

          ALTER TABLE host_data
          ADD CONSTRAINT host_data_time_host_type_event_key
          UNIQUE (time, host, type, event);
          """,
      ),
  ]

