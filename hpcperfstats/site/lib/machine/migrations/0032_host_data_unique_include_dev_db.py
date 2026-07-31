"""Phase 2: live UNIQUE (time, host, type, event, dev) + restore compress_after 8d.

Django state already records 5-column ``unique_together`` from Phase 1 ``0030``.
This migration applies the matching DB UNIQUE after operators decompress all
``host_data`` chunks (and, on hpcperfstats02-class sites, drop the aberrant
4-column PK outside migrate — see ``docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md``).

Soft-skips (NOTICE + success) when compressed chunks remain or a multi-column
PRIMARY KEY is still present so accidental startup migrate under
``restart: always`` does not crash-loop ``web``. Long UNIQUE builds should use
the operator one-shot migrate path with ``web`` stopped.
"""
from django.db import migrations


class Migration(migrations.Migration):
  # Non-atomic: SET statement_timeout + DO block; long UNIQUE on large sites.
  atomic = False

  dependencies = [
      ("machine", "0031_alter_job_plot_artifact_options_and_more"),
  ]

  operations = [
      migrations.RunSQL(
          sql="""
          SET statement_timeout = 0;

          DO $$
          DECLARE
            compressed_n bigint;
            multi_col_pk boolean;
            con record;
            has_five_col_unique boolean;
            has_compress_job boolean;
          BEGIN
            IF NOT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = 'public'
                AND table_name = 'host_data'
            ) THEN
              RETURN;
            END IF;

            SELECT count(*) FILTER (WHERE is_compressed)
              INTO compressed_n
              FROM timescaledb_information.chunks
             WHERE hypertable_name = 'host_data';

            IF COALESCE(compressed_n, 0) > 0 THEN
              RAISE NOTICE
                '0032_host_data_unique_include_dev_db: skipping — % compressed host_data chunk(s); decompress first (see docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md)',
                compressed_n;
              RETURN;
            END IF;

            SELECT EXISTS (
              SELECT 1
              FROM pg_constraint c
              WHERE c.conrelid = 'public.host_data'::regclass
                AND c.contype = 'p'
                AND coalesce(array_length(c.conkey, 1), 0) > 1
            ) INTO multi_col_pk;

            IF multi_col_pk THEN
              RAISE NOTICE
                '0032_host_data_unique_include_dev_db: skipping — multi-column PRIMARY KEY still on host_data; operator must normalize (hpcperfstats02 path in docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md)';
              RETURN;
            END IF;

            -- Prefer empty string over NULL so UNIQUE works for absent GPU ids.
            UPDATE host_data SET dev = '' WHERE dev IS NULL;

            FOR con IN
              SELECT c.oid, c.conname
              FROM pg_constraint c
              WHERE c.conrelid = 'public.host_data'::regclass
                AND c.contype = 'u'
                AND ARRAY(
                  SELECT a.attname::text
                  FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                  ORDER BY k.ordinality
                ) = ARRAY['time', 'host', 'type', 'event']::text[]
            LOOP
              EXECUTE format('ALTER TABLE public.host_data DROP CONSTRAINT %I', con.conname);
              RAISE NOTICE
                '0032_host_data_unique_include_dev_db: dropped 4-column UNIQUE %',
                con.conname;
            END LOOP;

            SELECT EXISTS (
              SELECT 1
              FROM pg_constraint c
              WHERE c.conrelid = 'public.host_data'::regclass
                AND c.contype = 'u'
                AND ARRAY(
                  SELECT a.attname::text
                  FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                  ORDER BY k.ordinality
                ) = ARRAY['time', 'host', 'type', 'event', 'dev']::text[]
            ) INTO has_five_col_unique;

            IF NOT has_five_col_unique THEN
              ALTER TABLE public.host_data
                ADD CONSTRAINT host_data_time_host_type_event_dev_uniq
                UNIQUE (time, host, type, event, dev);
              RAISE NOTICE
                '0032_host_data_unique_include_dev_db: added UNIQUE (time, host, type, event, dev)';
            END IF;

            SELECT EXISTS (
              SELECT 1
              FROM timescaledb_information.jobs j
              WHERE j.hypertable_name = 'host_data'
                AND (
                  j.proc_name LIKE '%compress%'
                  OR j.proc_name LIKE '%policy_compression%'
                )
            ) INTO has_compress_job;

            IF NOT has_compress_job THEN
              PERFORM add_compression_policy(
                'host_data',
                compress_after => INTERVAL '8d'
              );
              RAISE NOTICE
                '0032_host_data_unique_include_dev_db: restored compress_after 8d';
            END IF;
          END;
          $$;
          """,
          reverse_sql=migrations.RunSQL.noop,
      ),
  ]
