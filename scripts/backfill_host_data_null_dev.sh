#!/usr/bin/env bash
# Parallel host_data.dev NULL → '' backfill before Stage 2 UNIQUE (0032).
# See docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md.
#
# Keeps up to N chunk UPDATEs in flight. When any one finishes, picks the next
# uncompressed chunk that still has NULL dev and starts another (sliding pool).
#
# Prefer Stage 1 done (compressed_chunks = 0). Compressed chunks are skipped;
# if NULLs remain only under compressed chunks, the script exits non-zero.
# Prefer pipeline/web stopped so ingest is not fighting the rewrite.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/backfill_host_data_null_dev.sh           # concurrency 8
#   ./scripts/backfill_host_data_null_dev.sh 16
#   ./scripts/backfill_host_data_null_dev.sh 4
#
# Progress:
#   docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml \
#     exec db psql -h localhost -U hpcperfstats -c \
#     "SELECT count(*) AS null_dev_rows FROM host_data WHERE dev IS NULL;"
set -euo pipefail

CONCURRENCY="${1:-8}"
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [concurrency]" >&2
  echo "  concurrency must be a positive integer (default 8)" >&2
  exit 2
fi

STALL_LIMIT="${HPCPERFSTATS_NULL_DEV_STALL_LIMIT:-5}"

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml)

STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/backfill_host_data_null_dev.XXXXXX")"
cleanup() {
  rm -rf "${STATUS_DIR}"
}
trap cleanup EXIT

psql_at() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -Atc "$1"
}

psql_cmd() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 -c "$1"
}

remaining_nulls() {
  local n
  n="$(psql_at "SELECT count(*) FROM host_data WHERE dev IS NULL;")"
  printf '%s' "${n//$'\r'/}"
}

compressed_n() {
  local n
  n="$(psql_at "SELECT count(*) FILTER (WHERE is_compressed)
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'host_data';")"
  printf '%s' "${n//$'\r'/}"
}

# One line: chunk|range_start|range_end  (uncompressed chunks with NULL dev only).
# Args: excluded chunk names (may be empty).
next_chunk_row() {
  local excl_sql="" c first=1
  if [[ "$#" -gt 0 ]]; then
    excl_sql="AND format('%I.%I', c.chunk_schema, c.chunk_name) NOT IN ("
    for c in "$@"; do
      [[ -z "$c" ]] && continue
      c="${c//\'/\'\'}"
      if [[ "$first" -eq 1 ]]; then
        first=0
      else
        excl_sql+=", "
      fi
      excl_sql+="'${c}'"
    done
    excl_sql+=")"
    if [[ "$first" -eq 1 ]]; then
      excl_sql=""
    fi
  fi
  psql_at "SELECT format(
              '%s|%s|%s',
              format('%I.%I', c.chunk_schema, c.chunk_name),
              c.range_start,
              c.range_end
            )
           FROM timescaledb_information.chunks c
           WHERE c.hypertable_name = 'host_data'
             AND NOT c.is_compressed
             ${excl_sql}
             AND EXISTS (
               SELECT 1
               FROM host_data h
               WHERE h.time >= c.range_start
                 AND h.time < c.range_end
                 AND h.dev IS NULL
               LIMIT 1
             )
           ORDER BY c.range_start
           LIMIT 1;" | tr -d '\r'
}

JOB_SEQ=0

start_one() {
  local row="$1"
  local chunk t0 t1 job_id pid rcfile
  IFS='|' read -r chunk t0 t1 <<<"${row}"
  if [[ -z "$chunk" || -z "$t0" || -z "$t1" ]]; then
    echo "  FAIL  malformed chunk row: ${row}" >&2
    return 1
  fi

  JOB_SEQ=$((JOB_SEQ + 1))
  job_id="$JOB_SEQ"
  rcfile="${STATUS_DIR}/${job_id}.rc"

  (
    echo "  start ${chunk}  ${t0} .. ${t1}"
    # Quote timestamps for SQL; chunk regclass not needed — time-range UPDATE.
    t0_sql="${t0//\'/\'\'}"
    t1_sql="${t1//\'/\'\'}"
    if psql_cmd "SET statement_timeout = 0;
UPDATE host_data
   SET dev = ''
 WHERE time >= '${t0_sql}'::timestamptz
   AND time <  '${t1_sql}'::timestamptz
   AND dev IS NULL;"; then
      echo "  done  ${chunk}"
      printf '0\n' > "${rcfile}"
      exit 0
    fi
    echo "  FAIL  ${chunk}" >&2
    printf '1\n' > "${rcfile}"
    exit 1
  ) &
  pid=$!
  inflight["$pid"]="$chunk"
  pid_job["$pid"]="$job_id"
  echo "  launched job=${job_id} pid=${pid} inflight=${#inflight[@]}/${CONCURRENCY}"
}

wait_one() {
  local pid job_id rcfile found=0
  DONE_CHUNK=""
  DONE_RC=1

  while [[ "$found" -eq 0 ]]; do
    for pid in "${!inflight[@]}"; do
      job_id="${pid_job[$pid]}"
      rcfile="${STATUS_DIR}/${job_id}.rc"
      if [[ -f "$rcfile" ]]; then
        DONE_RC="$(tr -d '[:space:]' < "$rcfile")"
        DONE_CHUNK="${inflight[$pid]}"
        unset "inflight[$pid]"
        unset "pid_job[$pid]"
        rm -f "$rcfile"
        wait "$pid" 2>/dev/null || true
        found=1
        break
      fi
    done
    if [[ "$found" -eq 0 ]]; then
      wait -n || true
    fi
  done
}

fill_slots() {
  local exclude row chunk pid c
  while [[ "${#inflight[@]}" -lt "$CONCURRENCY" ]]; do
    exclude=()
    for pid in "${!inflight[@]}"; do
      exclude+=("${inflight[$pid]}")
    done
    for c in "${!skipped[@]}"; do
      exclude+=("$c")
    done

    if [[ "${#exclude[@]}" -gt 0 ]]; then
      row="$(next_chunk_row "${exclude[@]}")"
    else
      row="$(next_chunk_row)"
    fi
    row="${row//$'\n'/}"
    if [[ -z "$row" ]]; then
      return 0
    fi
    IFS='|' read -r chunk _t0 _t1 <<<"${row}"
    if [[ -z "$chunk" ]]; then
      return 0
    fi
    start_one "$row" || {
      skipped["$chunk"]=1
      total_failed=$((total_failed + 1))
    }
  done
}

declare -A inflight=()
declare -A pid_job=()
declare -A skipped=()
total_failed=0
total_ok=0
prev_left=""
stall=0

echo "parallel host_data.dev NULL→'' backfill: concurrency=${CONCURRENCY} (sliding pool)"

comp="$(compressed_n)"
if [[ "$comp" =~ ^[0-9]+$ && "$comp" -gt 0 ]]; then
  echo "warning: ${comp} compressed host_data chunk(s) — those ranges are skipped; decompress first for a complete backfill" >&2
fi

while true; do
  left="$(remaining_nulls)"
  echo "null_dev_rows=${left} inflight=${#inflight[@]} skipped=${#skipped[@]} ok=${total_ok} failed=${total_failed}"

  if [[ "$left" == "0" && "${#inflight[@]}" -eq 0 ]]; then
    echo "done (ok=${total_ok} skipped_failures=${total_failed})"
    exit 0
  fi

  fill_slots

  if [[ "${#inflight[@]}" -eq 0 ]]; then
    if [[ "$left" == "0" ]]; then
      echo "done (ok=${total_ok} skipped_failures=${total_failed})"
      exit 0
    fi
    comp="$(compressed_n)"
    if [[ "$comp" =~ ^[0-9]+$ && "$comp" -gt 0 ]]; then
      echo "aborting: ${left} null_dev row(s) remain and ${comp} compressed chunk(s) were skipped — decompress then re-run" >&2
    else
      echo "aborting: ${left} null_dev row(s) remain but no uncompressed chunk with NULLs could be started" >&2
    fi
    exit 1
  fi

  wait_one
  if [[ "${DONE_RC}" -eq 0 ]]; then
    total_ok=$((total_ok + 1))
  else
    total_failed=$((total_failed + 1))
    skipped["$DONE_CHUNK"]=1
    echo "warning: skipping failed chunk for rest of run: ${DONE_CHUNK}" >&2
  fi

  fill_slots

  left="$(remaining_nulls)"
  echo "null_dev_rows=${left} (after ${DONE_CHUNK})"

  if [[ "$left" =~ ^[0-9]+$ && -n "$prev_left" ]]; then
    if [[ "$left" -ge "$prev_left" ]]; then
      stall=$((stall + 1))
      echo "warning: no progress (${stall}/${STALL_LIMIT}) — still ${left} null_dev row(s)" >&2
      if [[ "$stall" -ge "$STALL_LIMIT" ]]; then
        echo "aborting: null_dev_rows stuck at ${left}; draining ${#inflight[@]} in-flight job(s)" >&2
        while [[ "${#inflight[@]}" -gt 0 ]]; do
          wait_one
        done
        exit 1
      fi
    else
      stall=0
    fi
  fi
  prev_left="$left"
done
