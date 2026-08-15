#!/usr/bin/env bash
# Export cluster GPU power_usage samples as CSV, one day at a time.
#
# Builds a durable host+jid time-range lookup from job_data (unnest host_list),
# indexes it, then COPY-exports each calendar day of host_data power_usage
# without ORDER BY. jid is resolved via host+time against the lookup (not
# host_data.jid). statement_timeout is disabled for the long export.
#
# job_data.host_list holds short names while host_data.host holds FQDNs built
# with DEFAULT/host_name_ext, so both sides are compared on the first DNS label
# (split_part(host, '.', 1)). Comparing raw values matches nothing.
#
# Samples outside any job window are still exported, with an empty jid.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/export_gpu_watts_by_day.sh
#   ./scripts/export_gpu_watts_by_day.sh 7 gpu_watts_last_week.csv
#   ./scripts/export_gpu_watts_by_day.sh 14 /tmp/gpu_watts.csv
#
# Env:
#   HPCPERFSTATS_GPU_EXPORT_KEEP_STAGING=1   keep lookup table after run
#   HPCPERFSTATS_GPU_EXPORT_WORK_MEM=512MB   session work_mem for COPY
#   HPCPERFSTATS_GPU_EXPORT_SKIP_PREFLIGHT=1 skip the jid match preflight
set -euo pipefail

DAYS="${1:-7}"
OUTFILE="${2:-gpu_watts_last_week.csv}"
KEEP_STAGING="${HPCPERFSTATS_GPU_EXPORT_KEEP_STAGING:-0}"
WORK_MEM="${HPCPERFSTATS_GPU_EXPORT_WORK_MEM:-512MB}"
SKIP_PREFLIGHT="${HPCPERFSTATS_GPU_EXPORT_SKIP_PREFLIGHT:-0}"
PREFLIGHT_SAMPLE="${HPCPERFSTATS_GPU_EXPORT_PREFLIGHT_SAMPLE:-5000}"
STAGING_TABLE="tmp_export_gpu_job_lookup"

if ! [[ "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [days] [outfile]" >&2
  echo "  days must be a positive integer (default 7)" >&2
  exit 2
fi

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml)

psql_cmd() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats \
    -v ON_ERROR_STOP=1 -c "$1"
}

psql_at() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -Atc "$1"
}

# Run SQL and stream COPY stdout to a host file (append or truncate via redirect).
# Args: sql, redirect_mode (">" or ">>")
psql_copy_to() {
  local sql="$1"
  local mode="$2"
  if [[ "$mode" == ">" ]]; then
    "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats \
      -v ON_ERROR_STOP=1 -c "$sql" >"$OUTFILE"
  else
    "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats \
      -v ON_ERROR_STOP=1 -c "$sql" >>"$OUTFILE"
  fi
}

cleanup_staging() {
  if [[ "$KEEP_STAGING" == "1" ]]; then
    echo "keeping staging table ${STAGING_TABLE} (HPCPERFSTATS_GPU_EXPORT_KEEP_STAGING=1)"
    return 0
  fi
  echo "dropping staging table ${STAGING_TABLE}..."
  psql_cmd "DROP TABLE IF EXISTS ${STAGING_TABLE};" || true
}
trap cleanup_staging EXIT

echo "building job lookup ${STAGING_TABLE} (last ${DAYS} days of job_data)..."
psql_cmd "
SET statement_timeout = 0;
SET work_mem = '${WORK_MEM}';
DROP TABLE IF EXISTS ${STAGING_TABLE};
CREATE UNLOGGED TABLE ${STAGING_TABLE} AS
SELECT jd.jid,
       split_part(h, '.', 1) AS host_short,
       COALESCE(jd.telemetry_first_time, jd.start_time) AS t0,
       COALESCE(jd.telemetry_last_time,  jd.end_time)   AS t1
FROM job_data jd
CROSS JOIN LATERAL unnest(jd.host_list) AS h
WHERE jd.end_time >= CURRENT_DATE - ${DAYS}
  AND h IS NOT NULL
  AND btrim(h) <> '';
CREATE INDEX ON ${STAGING_TABLE} (host_short, t0, t1);
ANALYZE ${STAGING_TABLE};
"

lookup_rows="$(psql_at "SELECT count(*) FROM ${STAGING_TABLE};")"
echo "lookup rows: ${lookup_rows}"
if [[ "${lookup_rows:-0}" -eq 0 ]]; then
  echo "job lookup is empty: no job_data rows with end_time in the last ${DAYS} days," >&2
  echo "or every host_list is empty. Every exported jid would be blank." >&2
  exit 1
fi

# Day list oldest → newest so partial files are chronological.
# Inclusive calendar window ending today (DAYS=7 → today and prior 6 days).
DAY_LIST=()
while IFS= read -r day; do
  [[ -z "$day" ]] && continue
  DAY_LIST+=("$day")
done < <(
  psql_at "
SELECT to_char(d::date, 'YYYY-MM-DD')
FROM generate_series(
  CURRENT_DATE - (${DAYS} - 1),
  CURRENT_DATE,
  interval '1 day'
) AS d;
"
)

if [[ "${#DAY_LIST[@]}" -eq 0 ]]; then
  echo "no day windows to export" >&2
  exit 1
fi

wrote_header=0
for day in "${DAY_LIST[@]}"; do
  [[ -z "$day" ]] && continue
  day_esc="${day//\'/\'\'}"
  echo "exporting day ${day}..."

  if [[ "$wrote_header" -eq 0 ]]; then
    header_clause="HEADER true"
    redirect=">"
  else
    header_clause="HEADER false"
    redirect=">>"
  fi

  psql_copy_to "
SET statement_timeout = 0;
SET work_mem = '${WORK_MEM}';
COPY (
  SELECT hd.time,
         hd.host,
         COALESCE(hd.dev, '') AS device,
         COALESCE(j.jid, '')  AS jid,
         hd.value AS wattage
  FROM host_data hd
  LEFT JOIN LATERAL (
    SELECT g.jid
    FROM ${STAGING_TABLE} g
    WHERE g.host_short = split_part(hd.host, '.', 1)
      AND hd.time >= g.t0
      AND hd.time <= g.t1
    ORDER BY g.t1 DESC
    LIMIT 1
  ) j ON true
  WHERE hd.time >= TIMESTAMP '${day_esc}'
    AND hd.time <  TIMESTAMP '${day_esc}' + INTERVAL '1 day'
    AND hd.type IN ('nvidia_gpu', 'amd_gpu', 'intel_gpu')
    AND hd.event = 'power_usage'
) TO STDOUT WITH (FORMAT csv, ${header_clause});
" "$redirect"

  wrote_header=1
  bytes="$(wc -c <"$OUTFILE" | tr -d ' ')"
  echo "  done day ${day}; outfile bytes so far: ${bytes}"
done

echo "wrote ${OUTFILE}"
echo "columns: time,host,device,jid,wattage"
