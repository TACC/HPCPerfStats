"""PostgreSQL STABLE helpers for metrics/update_metrics DB offload (#8, #10, #11)."""

from django.db import migrations


CREATE_JID_DISTINCT = """
CREATE OR REPLACE FUNCTION jid_scoped_distinct_host_time_count(
  p_jid text,
  p_start timestamptz,
  p_end timestamptz
) RETURNS bigint
LANGUAGE sql
STABLE
AS $$
  SELECT COALESCE(SUM(ph.cnt), 0)::bigint
  FROM (
    SELECT h.host, COUNT(DISTINCT h.time)::bigint AS cnt
    FROM host_data h
    WHERE h.jid = p_jid
      AND h.time >= p_start
      AND h.time <= p_end
    GROUP BY h.host
  ) ph;
$$;
"""

DROP_JID_DISTINCT = "DROP FUNCTION IF EXISTS jid_scoped_distinct_host_time_count(text, timestamptz, timestamptz);"

CREATE_STRIDED = """
CREATE OR REPLACE FUNCTION host_data_strided_bucket_max_times(
  p_hosts text[],
  p_start timestamptz,
  p_end timestamptz,
  p_n_buckets integer
) RETURNS SETOF timestamptz
LANGUAGE sql
STABLE
AS $$
  WITH params AS (
    SELECT GREATEST(2, COALESCE(p_n_buckets, 2)) AS nb,
           GREATEST(
             EXTRACT(EPOCH FROM (p_end - p_start))
               / GREATEST(COALESCE(p_n_buckets, 2) - 1, 1)::double precision,
             1e-9
           ) AS step_sec
  ),
  grouped AS (
    SELECT MAX(h.time) AS mx
    FROM host_data h
    CROSS JOIN params p
    WHERE h.host = ANY(p_hosts)
      AND h.time >= p_start
      AND h.time <= p_end
    GROUP BY (
      FLOOR(EXTRACT(EPOCH FROM (h.time - p_start)) / p.step_sec)
    )::bigint
  )
  SELECT mx FROM grouped WHERE mx IS NOT NULL ORDER BY mx;
$$;
"""

DROP_STRIDED = "DROP FUNCTION IF EXISTS host_data_strided_bucket_max_times(text[], timestamptz, timestamptz, integer);"

CREATE_SAMPLE_SUM = """
CREATE OR REPLACE FUNCTION host_data_sum_metric_per_sample(
  p_hosts text[],
  p_start timestamptz,
  p_end timestamptz,
  p_type text,
  p_events text[],
  p_val_col text
) RETURNS TABLE(host text, sample_time timestamptz, sum_val double precision)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
  IF p_val_col = 'arc' THEN
    RETURN QUERY
    SELECT h.host::text,
           h.time,
           SUM(h.arc)::double precision
    FROM host_data h
    WHERE h.host = ANY(p_hosts)
      AND h.time >= p_start
      AND h.time <= p_end
      AND h.type = p_type
      AND h.event = ANY(p_events)
    GROUP BY h.host, h.time;
  ELSIF p_val_col = 'value' THEN
    RETURN QUERY
    SELECT h.host::text,
           h.time,
           SUM(h.value)::double precision
    FROM host_data h
    WHERE h.host = ANY(p_hosts)
      AND h.time >= p_start
      AND h.time <= p_end
      AND h.type = p_type
      AND h.event = ANY(p_events)
    GROUP BY h.host, h.time;
  ELSE
    RETURN;
  END IF;
END;
$$;
"""

DROP_SAMPLE_SUM = "DROP FUNCTION IF EXISTS host_data_sum_metric_per_sample(text[], timestamptz, timestamptz, text, text[], text);"


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0033_test_login_user"),
    ]

    operations = [
        migrations.RunSQL(CREATE_JID_DISTINCT, DROP_JID_DISTINCT),
        migrations.RunSQL(CREATE_STRIDED, DROP_STRIDED),
        migrations.RunSQL(CREATE_SAMPLE_SUM, DROP_SAMPLE_SUM),
    ]
