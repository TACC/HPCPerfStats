"""Drop indexes that duplicate another index on the same columns.

``0001`` created ``host_data (host, time)`` and ``(jid, time)``; ``0021``
later added ``(host, time DESC)`` and ``(jid, time DESC)`` without removing
the originals. Every ``host_data`` read filters the leading column by
equality, and a btree scans either direction, so each pair serves the same
queries while ingest and the NULL ``dev`` backfill pay two random inserts per
row (~750 GB per large site). ``job_data_end_time_idx`` (raw SQL in ``0004``)
duplicates the ``end_time`` ``db_index`` and ``api_keys_username_idx``
duplicates the ``username`` ``db_index``.

``host_data`` is a hypertable, so its drops cannot be CONCURRENT and need an
ACCESS EXCLUSIVE lock. Each attempt waits at most ``LOCK_WAIT`` so queued
reads and ingest are never stuck behind it for long; a busy lock is retried
``LOCK_ATTEMPTS`` times, then the migration raises. Never soft-skip: Django
would record 0045 applied with the duplicates still present and no later
migrate would retry. Raising leaves 0045 unapplied, so the next migrate (web
restart or operator one-shot) retries it. The plain tables drop
CONCURRENTLY. ``IF EXISTS`` throughout: hpcperfstats03 already dropped the
``host_data`` pair by hand during its backfill (no lock taken then).
"""

from django.db import migrations

HOST_DATA_DUPLICATE_INDEXES = (
  "host_data_host_eda1c8_idx",
  "host_data_jid_9e84c4_idx",
)
# Worst case ~LOCK_ATTEMPTS * (LOCK_WAIT + RETRY_PAUSE_S) = ~2 min, then raise.
LOCK_ATTEMPTS = 12
LOCK_WAIT = "5s"
RETRY_PAUSE_S = 5

DROP_HOST_DATA_DUPLICATES = f"""
DO $$
DECLARE
  attempt int := 0;
BEGIN
  LOOP
    BEGIN
      DROP INDEX IF EXISTS {", ".join(f"public.{n}" for n in HOST_DATA_DUPLICATE_INDEXES)};
      RETURN;
    EXCEPTION WHEN lock_not_available THEN
      attempt := attempt + 1;
      IF attempt >= {LOCK_ATTEMPTS} THEN
        RAISE EXCEPTION
          '0045_drop_duplicate_indexes: host_data lock busy after % attempts; 0045 stays unapplied, migrate again when quiet',
          attempt;
      END IF;
      RAISE NOTICE
        '0045_drop_duplicate_indexes: host_data lock busy, retry %/{LOCK_ATTEMPTS}',
        attempt;
      PERFORM pg_sleep({RETRY_PAUSE_S});
    END;
  END LOOP;
END;
$$;
"""


class Migration(migrations.Migration):
  # CONCURRENTLY and the lock retry loop both need autocommit.
  atomic = False

  dependencies = [
    ("machine", "0044_job_affinity_host_reported_idx"),
  ]

  operations = [
    migrations.SeparateDatabaseAndState(
      database_operations=[
        migrations.RunSQL(
          sql=[
            "SET statement_timeout = 0",
            f"SET lock_timeout = '{LOCK_WAIT}'",
            DROP_HOST_DATA_DUPLICATES,
            "RESET lock_timeout",
            "DROP INDEX CONCURRENTLY IF EXISTS job_data_end_time_idx",
            "DROP INDEX CONCURRENTLY IF EXISTS api_keys_username_idx",
            "RESET statement_timeout",
          ],
          # Reverse rebuilds full host_data indexes: operator-only, hours on
          # large sites.
          reverse_sql=[
            "SET statement_timeout = 0",
            "CREATE INDEX IF NOT EXISTS host_data_host_eda1c8_idx"
            " ON host_data (host, time)",
            "CREATE INDEX IF NOT EXISTS host_data_jid_9e84c4_idx"
            " ON host_data (jid, time)",
            "CREATE INDEX IF NOT EXISTS job_data_end_time_idx"
            " ON job_data (end_time)",
            "CREATE INDEX IF NOT EXISTS api_keys_username_idx"
            " ON api_keys (username)",
            "RESET statement_timeout",
          ],
        ),
      ],
      state_operations=[
        migrations.RemoveIndex(
          model_name="host_data", name="host_data_host_eda1c8_idx"
        ),
        migrations.RemoveIndex(
          model_name="host_data", name="host_data_jid_9e84c4_idx"
        ),
        migrations.RemoveIndex(
          model_name="apikey", name="api_keys_username_idx"
        ),
      ],
    ),
  ]
