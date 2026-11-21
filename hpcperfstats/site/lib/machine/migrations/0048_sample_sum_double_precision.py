"""Sum ``host_data`` real columns in double precision in the per-sample SQL.

``host_data.value`` / ``arc`` are PostgreSQL ``real``; ``SUM(real)`` returns
``real`` and accumulates in float4 (about 7 significant digits), so the 0034
``host_data_sum_metric_per_sample`` lost precision before its outer cast.
This redefinition casts each row to double precision before ``SUM`` and
skips monitor value sentinels (``value < 0`` is never data), keeping the
0034 signature and return type. ``CREATE OR REPLACE`` on a SQL function is
a catalog update only (bounded at startup); reverse restores the 0034 body.

Attributes:
  CREATE_SAMPLE_SUM_DOUBLE: ``CREATE OR REPLACE`` of the function with
    double-precision sums.
"""

import importlib

from django.db import migrations

CREATE_SAMPLE_SUM_DOUBLE = """
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
           SUM(h.arc::double precision)
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
           SUM(h.value::double precision)
    FROM host_data h
    WHERE h.host = ANY(p_hosts)
      AND h.time >= p_start
      AND h.time <= p_end
      AND h.type = p_type
      AND h.event = ANY(p_events)
      AND h.value >= 0
    GROUP BY h.host, h.time;
  ELSE
    RETURN;
  END IF;
END;
$$;
"""


class Migration(migrations.Migration):
  """
  Redefine ``host_data_sum_metric_per_sample`` with double-precision sums.

  Subclasses Django ``migrations.Migration`` (an ordered list of schema
  operations with dependencies); this one runs one ``RunSQL`` whose reverse
  is the 0034 definition.
  """

  dependencies = [
    ("machine", "0047_host_sentinel_summary_no_data_codes"),
  ]

  operations = [
    migrations.RunSQL(
      CREATE_SAMPLE_SUM_DOUBLE,
      importlib.import_module(
        "hpcperfstats.site.lib.machine.migrations."
        "0034_metrics_host_data_sql_functions"
      ).CREATE_SAMPLE_SUM,
    ),
  ]
