#!/usr/bin/env bash
# Parallel host_data decompress for Stage 1 (OPERATOR_HOST_DATA_DEV_UNIQUENESS.md).
#
# Keeps up to N decompressions in flight. When any one finishes, rechecks
# compressed_chunks and immediately starts another (sliding pool), until none
# remain.
#
# Individual chunk failures are logged and skipped for the rest of the run.
# Aborts when no further chunk can be started while some remain compressed, or
# when compressed_chunks stops decreasing for STALL_LIMIT consecutive
# completions.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/decompress_host_data_chunks.sh           # concurrency 10
#   ./scripts/decompress_host_data_chunks.sh 20         # hpcperfstats02-class
#   ./scripts/decompress_host_data_chunks.sh 2          # hpcperfstats03 (tight disk)
#
# Watch free space (especially site 03):
#   docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml \
#     exec db df -h /var/lib/postgresql/data
set -euo pipefail

CONCURRENCY="${1:-10}"
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [concurrency]" >&2
  echo "  concurrency must be a positive integer (default 10)" >&2
  exit 2
fi

# Consecutive completions without a drop in compressed_chunks before giving up.
STALL_LIMIT="${HPCPERFSTATS_DECOMPRESS_STALL_LIMIT:-5}"

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml)

STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/decompress_host_data.XXXXXX")"
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

remaining() {
  local n
  n="$(psql_at "SELECT count(*) FILTER (WHERE is_compressed)
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'host_data';")"
  printf '%s' "${n//$'\r'/}"
}

# Print one next compressed chunk, excluding names already in-flight or skipped.
# Args: excluded chunk names (may be empty).
next_chunk() {
  local excl_sql="" c first=1
  if [[ "$#" -gt 0 ]]; then
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
  fi
  psql_at "SELECT format('%I.%I', chunk_schema, chunk_name)
           FROM timescaledb_information.chunks
           WHERE hypertable_name = 'host_data' AND is_compressed
           ${excl_sql}
           ORDER BY range_start
           LIMIT 1;" | tr -d '\r'
}

JOB_SEQ=0

start_one() {
  local chunk="$1"
  local job_id pid rcfile
  JOB_SEQ=$((JOB_SEQ + 1))
  job_id="$JOB_SEQ"
  rcfile="${STATUS_DIR}/${job_id}.rc"

  (
    echo "  start ${chunk}"
    if psql_cmd "SET statement_timeout = 0; SELECT decompress_chunk('${chunk}'::regclass, true);"; then
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

# Wait for any one in-flight job; sets DONE_CHUNK, DONE_RC.
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
        # Reap the child if wait -n has not already.
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
  local exclude chunk pid c
  while [[ "${#inflight[@]}" -lt "$CONCURRENCY" ]]; do
    exclude=()
    for pid in "${!inflight[@]}"; do
      exclude+=("${inflight[$pid]}")
    done
    for c in "${!skipped[@]}"; do
      exclude+=("$c")
    done

    if [[ "${#exclude[@]}" -gt 0 ]]; then
      chunk="$(next_chunk "${exclude[@]}")"
    else
      chunk="$(next_chunk)"
    fi
    chunk="${chunk//$'\n'/}"
    if [[ -z "$chunk" ]]; then
      return 0
    fi
    start_one "$chunk"
  done
}

declare -A inflight=()
declare -A pid_job=()
declare -A skipped=()
total_failed=0
total_ok=0
prev_left=""
stall=0

echo "parallel host_data decompress: concurrency=${CONCURRENCY} (sliding pool)"

while true; do
  left="$(remaining)"
  echo "compressed_chunks=${left} inflight=${#inflight[@]} skipped=${#skipped[@]} ok=${total_ok} failed=${total_failed}"

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
    echo "aborting: ${left} compressed chunk(s) remain but none could be started (all skipped or unavailable)" >&2
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

  # Immediately refill so a free slot starts the next chunk without waiting for a full batch.
  fill_slots

  left="$(remaining)"
  echo "compressed_chunks=${left} (after ${DONE_CHUNK})"

  if [[ "$left" =~ ^[0-9]+$ && -n "$prev_left" ]]; then
    if [[ "$left" -ge "$prev_left" ]]; then
      stall=$((stall + 1))
      echo "warning: no progress (${stall}/${STALL_LIMIT}) — still ${left} compressed chunk(s)" >&2
      if [[ "$stall" -ge "$STALL_LIMIT" ]]; then
        echo "aborting: compressed_chunks stuck at ${left}; draining ${#inflight[@]} in-flight job(s)" >&2
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
