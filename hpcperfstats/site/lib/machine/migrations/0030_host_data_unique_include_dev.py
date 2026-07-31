"""Phase 1: record 5-col unique_together in Django state; remove compression policy.

Target uniqueness is ``(time, host, type, event, dev)``, but Timescale cannot
``ADD UNIQUE`` while any compressed chunk exists. This migration therefore:

* Updates Django state to the 5-column ``unique_together`` (no DB UNIQUE swap).
* Idempotently removes the ``host_data`` compression policy so operators can
  decompress chunks outside migrate (Phase 2 ``0032`` adds the DB UNIQUE and
  restores ``compress_after 8d``; operator owns any aberrant PK normalize).

Deliberately does **not** run an unbounded ``UPDATE host_data`` (that timed
out under ``statement_timeout`` and crash-looped ``web``) and does **not**
decompress chunks inside migrate.
"""
from django.db import migrations


class Migration(migrations.Migration):
  # Policy remove is a single catalog call; non-atomic keeps the DO block free
  # of wrapping transaction quirks on long-lived Timescale connections.
  atomic = False

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

                    -- Stop scheduled compression so operator decompress is not
                    -- racing a background job that re-compresses old chunks.
                    -- if_exists => true is a no-op when the policy is already gone
                    -- (fresh DBs, re-runs, or sites that never enabled it).
                    PERFORM remove_compression_policy('host_data', if_exists => true);
                  END;
                  $$;
                  """,
                  reverse_sql=migrations.RunSQL.noop,
              ),
          ],
      ),
  ]
