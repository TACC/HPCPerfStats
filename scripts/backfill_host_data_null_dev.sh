#!/usr/bin/env bash
# Parallel host_data.dev NULL → '' backfill before Stage 2 UNIQUE (0032).
# See docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md.
#
# Sliding pool of N concurrent chunk UPDATEs (N from CLI). Progress is remaining
# uncompressed chunks from the Timescale catalog (no host_data NULL row probes).
# Each worker: UPDATE by explicit time range (never LIMIT/OFFSET paging).
# After each successful UPDATE: kick off VACUUM (ANALYZE, PARALLEL 8) in the
# background (does not block starting the next UPDATE), then refill slots.
#
# Prefer Stage 1 done (compressed_chunks = 0). Compressed chunks are not in the
# worklist. Prefer pipeline/web stopped so ingest is not fighting the rewrite.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/backfill_host_data_null_dev.sh           # concurrency 30
#   ./scripts/backfill_host_data_null_dev.sh 16
#   ./scripts/backfill_host_data_null_dev.sh 8
#
# Optional post-run verify: see OPERATOR_HOST_DATA_DEV_UNIQUENESS.md (null_dev_rows).
set -euo pipefail

CONCURRENCY="${1:-30}"
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [concurrency]" >&2
  echo "  concurrency must be a positive integer (default 30)" >&2
  exit 2
fi

STALL_LIMIT="${HPCPERFSTATS_NULL_DEV_STALL_LIMIT:-5}"
VACUUM_EVERY="${HPCPERFSTATS_NULL_DEV_VACUUM_EVERY:-1}"
VACUUM_PARALLEL="${HPCPERFSTATS_NULL_DEV_VACUUM_PARALLEL:-8}"
if ! [[ "$VACUUM_PARALLEL" =~ ^[1-9][0-9]*$ ]]; then
  echo "HPCPERFSTATS_NULL_DEV_VACUUM_PARALLEL must be a positive integer" >&2
  exit 2
fi

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml)

declare -A vacuum_inflight=()

drain_vacuums() {
  local pid n="${#vacuum_inflight[@]}"
  if [[ "$n" -eq 0 ]]; then
    return 0
  fi
  echo "draining ${n} vacuum job(s)..."
  for pid in "${!vacuum_inflight[@]}"; do
    wait "$pid" 2>/dev/null || true
    unset "vacuum_inflight[$pid]"
  done
}

STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/backfill_host_data_null_dev.XXXXXX")"
cleanup() {
  drain_vacuums 2>/dev/null || true
  rm -rf "${STATUS_DIR}"
}
trap cleanup EXIT

psql_at() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -Atc "$1"
}

psql_cmd() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 -c "$1"
}

# VACUUM cannot run inside a transaction block. SET and VACUUM use separate -c.
# PARALLEL uses maintenance workers; raise session caps so PARALLEL N is honored.
psql_vacuum_chunk() {
  local chunk="$1"
  local parallel="${2:-${VACUUM_PARALLEL}}"
  if ! [[ "$parallel" =~ ^[1-9][0-9]*$ ]]; then
    parallel=8
  fi
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 \
    -c "SET statement_timeout = 0; SET max_parallel_maintenance_workers = ${parallel}; SET max_parallel_workers = ${parallel}" \
    -c "VACUUM (ANALYZE, PARALLEL ${parallel}) ${chunk};"
}

build_excl_sql() {
  local c first=1
  excl_sql=""
  if [[ "$#" -eq 0 ]]; then
    return 0
  fi
  excl_sql="AND format('%I.%I', chunk_schema, chunk_name) NOT IN ("
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
}

remaining_chunks() {
  local n
  build_excl_sql "$@"
  n="$(psql_at "SELECT count(*)
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'host_data'
                  AND NOT is_compressed
                  ${excl_sql};")"
  printf '%s' "${n//$'\r'/}"
}

compressed_n() {
  local n
  n="$(psql_at "SELECT count(*) FILTER (WHERE is_compressed)
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'host_data';")"
  printf '%s' "${n//$'\r'/}"
}

# Explicit time ranges from chunk catalog — never OFFSET-paged row batches.
next_chunk_row() {
  build_excl_sql "$@"
  psql_at "SELECT format(
              '%s|%s|%s',
              format('%I.%I', chunk_schema, chunk_name),
              range_start,
              range_end
            )
           FROM timescaledb_information.chunks
           WHERE hypertable_name = 'host_data'
             AND NOT is_compressed
             ${excl_sql}
           ORDER BY range_start
           LIMIT 1;" | tr -d '\r'
}

exclude_list() {
  local pid c
  exclude=()
  for c in "${!completed[@]}"; do
    exclude+=("$c")
  done
  for c in "${!skipped[@]}"; do
    exclude+=("$c")
  done
  for pid in "${!inflight[@]}"; do
    exclude+=("${inflight[$pid]}")
  done
}

vacuum_finished_chunk() {
  local chunk="$1"
  local pid
  if [[ -z "$chunk" ]]; then
    return 0
  fi
  # Async: do not block the UPDATE sliding pool. This chunk is already in
  # completed[], so no other worker will UPDATE it while VACUUM runs.
  (
    echo "  vacuum start ${chunk} (PARALLEL ${VACUUM_PARALLEL})"
    if psql_vacuum_chunk "$chunk" "$VACUUM_PARALLEL"; then
      echo "  vacuum done  ${chunk}"
      exit 0
    fi
    echo "  warning: VACUUM failed for ${chunk} (continuing)" >&2
    exit 1
  ) &
  pid=$!
  vacuum_inflight["$pid"]="$chunk"
  echo "  launched vacuum pid=${pid} inflight_vacuums=${#vacuum_inflight[@]}"
}

reap_finished_vacuums() {
  local pid
  for pid in "${!vacuum_inflight[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      unset "vacuum_inflight[$pid]"
    fi
  done
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
    echo "  start ${chunk}  ${t0} .. ${t1}  (time-range UPDATE)"
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
  local row chunk
  while [[ "${#inflight[@]}" -lt "$CONCURRENCY" ]]; do
    exclude_list

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
declare -A completed=()
exclude=()
excl_sql=""
total_failed=0
total_ok=0
prev_left=""
stall=0
vacuum_since=0

echo "parallel host_data.dev NULL→'' backfill: concurrency=${CONCURRENCY} (time-range chunks; async VACUUM every ${VACUUM_EVERY}, PARALLEL ${VACUUM_PARALLEL})"

comp="$(compressed_n)"
if [[ "$comp" =~ ^[0-9]+$ && "$comp" -gt 0 ]]; then
  echo "warning: ${comp} compressed host_data chunk(s) — not in worklist; decompress first for a complete backfill" >&2
fi

while true; do
  reap_finished_vacuums
  exclude_list
  if [[ "${#exclude[@]}" -gt 0 ]]; then
    left="$(remaining_chunks "${exclude[@]}")"
  else
    left="$(remaining_chunks)"
  fi
  echo "remaining_chunks=${left} inflight=${#inflight[@]}/${CONCURRENCY} vacuums=${#vacuum_inflight[@]} completed=${#completed[@]} skipped=${#skipped[@]} ok=${total_ok} failed=${total_failed}"

  if [[ "$left" == "0" && "${#inflight[@]}" -eq 0 ]]; then
    drain_vacuums
    echo "done (ok=${total_ok} skipped_failures=${total_failed})"
    exit 0
  fi

  fill_slots

  if [[ "${#inflight[@]}" -eq 0 ]]; then
    if [[ "$left" == "0" ]]; then
      drain_vacuums
      echo "done (ok=${total_ok} skipped_failures=${total_failed})"
      exit 0
    fi
    echo "aborting: ${left} uncompressed chunk(s) remain but none could be started (all skipped or unavailable)" >&2
    drain_vacuums
    exit 1
  fi

  wait_one
  if [[ "${DONE_RC}" -eq 0 ]]; then
    total_ok=$((total_ok + 1))
    completed["$DONE_CHUNK"]=1
    vacuum_since=$((vacuum_since + 1))
    if [[ "$VACUUM_EVERY" =~ ^[1-9][0-9]*$ && "$vacuum_since" -ge "$VACUUM_EVERY" ]]; then
      vacuum_finished_chunk "$DONE_CHUNK"
      vacuum_since=0
    fi
  else
    total_failed=$((total_failed + 1))
    skipped["$DONE_CHUNK"]=1
    echo "warning: skipping failed chunk for rest of run: ${DONE_CHUNK}" >&2
  fi

  fill_slots

  exclude_list
  if [[ "${#exclude[@]}" -gt 0 ]]; then
    left="$(remaining_chunks "${exclude[@]}")"
  else
    left="$(remaining_chunks)"
  fi
  echo "remaining_chunks=${left} (after ${DONE_CHUNK})"

  if [[ "$left" =~ ^[0-9]+$ && -n "$prev_left" ]]; then
    if [[ "$left" -ge "$prev_left" ]]; then
      stall=$((stall + 1))
      echo "warning: no progress (${stall}/${STALL_LIMIT}) — still ${left} chunk(s) remaining" >&2
      if [[ "$stall" -ge "$STALL_LIMIT" ]]; then
        echo "aborting: remaining_chunks stuck at ${left}; draining ${#inflight[@]} in-flight UPDATE(s)" >&2
        while [[ "${#inflight[@]}" -gt 0 ]]; do
          wait_one
        done
        drain_vacuums
        exit 1
      fi
    else
      stall=0
    fi
  fi
  prev_left="$left"
done
