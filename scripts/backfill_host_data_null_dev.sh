#!/usr/bin/env bash
# Parallel host_data.dev NULL → '' backfill before Stage 2 UNIQUE (0032).
# See docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md.
#
# Best practices baked in:
#   - Chunk by explicit time ranges from Timescale chunk catalog (never LIMIT/OFFSET
#     row paging). Each worker: UPDATE … WHERE time >= … AND time < … AND dev IS NULL.
#   - VACUUM (ANALYZE) the finished chunk, then immediately start another chunk (no sleep).
#   - Adaptive worker count (default): start low, ramp toward max while monitoring
#     replication lag, WAL on-disk vs max_wal_size, PGDATA free space, and chunk
#     UPDATE latency vs EWMA baseline. Back off before I/O saturation.
#
# Prefer Stage 1 done (compressed_chunks = 0). Compressed chunks are not in the
# worklist. Prefer pipeline/web stopped so ingest is not fighting the rewrite.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/backfill_host_data_null_dev.sh           # adaptive, max 30
#   ./scripts/backfill_host_data_null_dev.sh 16         # adaptive, max 16
#   HPCPERFSTATS_NULL_DEV_FIXED_CONCURRENCY=1 ./scripts/backfill_host_data_null_dev.sh 8
#
# Optional post-run verify: see OPERATOR_HOST_DATA_DEV_UNIQUENESS.md (null_dev_rows).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backfill_host_data_null_dev_throttle.sh
source "${SCRIPT_DIR}/lib/backfill_host_data_null_dev_throttle.sh"

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml)

now_ms() {
  python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(($(date +%s) * 1000))"
}

psql_at() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -Atc "$1"
}

psql_cmd() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 -c "$1"
}

# Build AND … NOT IN (…) for chunk names. Sets excl_sql (may be empty).
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

# lag_sec|wal_bytes|max_wal_bytes  (−1 for unknown WAL metrics)
sample_db_pressure_metrics() {
  local row
  row="$(psql_at "
    SELECT format(
      '%s|%s|%s',
      COALESCE(
        (SELECT EXTRACT(EPOCH FROM max(GREATEST(
            COALESCE(replay_lag, INTERVAL '0'),
            COALESCE(write_lag, INTERVAL '0'),
            COALESCE(flush_lag, INTERVAL '0')
          )))::bigint
         FROM pg_stat_replication),
        0
      ),
      COALESCE((SELECT sum(size)::bigint FROM pg_ls_waldir()), -1),
      COALESCE(pg_size_bytes(current_setting('max_wal_size')), -1)
    );" 2>/dev/null || true)"
  row="${row//$'\r'/}"
  row="${row//$'\n'/}"
  if [[ -z "$row" || "$row" != *"|"* ]]; then
    printf '%s\n' '0|-1|-1'
    return 0
  fi
  printf '%s\n' "$row"
}

sample_disk_avail_bytes() {
  local avail
  avail="$("${COMPOSE[@]}" exec -T db df -B1 --output=avail /var/lib/postgresql/data 2>/dev/null | tail -n 1 | tr -d '[:space:]\r' || true)"
  if [[ "$avail" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$avail"
  else
    # Unknown avail: print sentinel via format+arg (leading '-' is not a printf option).
    printf '%s\n' '-1'
  fi
}

vacuum_finished_chunk() {
  local chunk="$1"
  if [[ -z "$chunk" ]]; then
    return 0
  fi
  echo "  vacuum ${chunk}"
  if ! psql_cmd "VACUUM (ANALYZE) ${chunk};"; then
    echo "  warning: VACUUM failed for ${chunk} (continuing)" >&2
    return 0
  fi
}

maybe_adapt_concurrency() {
  local duration_ms="${1:-0}"
  local lag_sec wal_bytes max_wal disk_avail pressure new_target reason limit_ms

  if [[ "$FIXED_CONCURRENCY" -eq 1 ]]; then
    TARGET_CONCURRENCY="$MAX_CONCURRENCY"
    return 0
  fi

  IFS='|' read -r lag_sec wal_bytes max_wal <<<"$(sample_db_pressure_metrics)"
  disk_avail="$(sample_disk_avail_bytes)"
  pressure="$(null_dev_eval_pressure \
    "${lag_sec:-0}" "$LAG_LIMIT_SEC" \
    "${wal_bytes:--1}" "${max_wal:--1}" "$WAL_FRAC" \
    "${disk_avail:--1}" "$DISK_MIN_BYTES")"

  reason="hold"
  if [[ "$pressure" == "1" ]]; then
    reason="pressure(lag=${lag_sec:-?}s wal=${wal_bytes:-?}/${max_wal:-?} disk_avail=${disk_avail:-?})"
    healthy_streak=0
  elif [[ "$duration_ms" =~ ^[1-9][0-9]*$ && "$baseline_ms" =~ ^[1-9][0-9]*$ ]]; then
    limit_ms="$(awk -v b="$baseline_ms" -v r="$LATENCY_RATIO" 'BEGIN { printf "%d", b * r }')"
    if [[ "$duration_ms" -gt "$limit_ms" ]]; then
      reason="latency(${duration_ms}ms > ${limit_ms}ms=${LATENCY_RATIO}x baseline ${baseline_ms}ms)"
      healthy_streak=0
      pressure=1
    else
      healthy_streak=$((healthy_streak + 1))
      reason="healthy_streak=${healthy_streak}"
    fi
  else
    healthy_streak=$((healthy_streak + 1))
    reason="warming baseline"
  fi

  if [[ "$duration_ms" =~ ^[1-9][0-9]*$ && "$pressure" != "1" ]]; then
    baseline_ms="$(null_dev_update_baseline "$baseline_ms" "$duration_ms" "$BASELINE_ALPHA")"
  fi

  new_target="$(null_dev_adjust_concurrency \
    "$TARGET_CONCURRENCY" "$MAX_CONCURRENCY" "$MIN_CONCURRENCY" \
    "$pressure" "$healthy_streak" \
    "$duration_ms" "$baseline_ms" \
    "$LATENCY_RATIO" "$HEALTHY_NEEDED")"

  if [[ "$new_target" != "$TARGET_CONCURRENCY" ]]; then
    echo "  adapt workers ${TARGET_CONCURRENCY} → ${new_target} (${reason}; baseline_ms=${baseline_ms:-0})"
    TARGET_CONCURRENCY="$new_target"
    if [[ "$pressure" != "1" && "$new_target" -gt "${BEST_CONCURRENCY:-0}" ]]; then
      BEST_CONCURRENCY="$new_target"
    fi
    healthy_streak=0
  else
    echo "  adapt hold workers=${TARGET_CONCURRENCY} (${reason}; baseline_ms=${baseline_ms:-0} lag=${lag_sec:-?}s)"
  fi
}

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
    t_start="$(now_ms)"
    if psql_cmd "SET statement_timeout = 0;
UPDATE host_data
   SET dev = ''
 WHERE time >= '${t0_sql}'::timestamptz
   AND time <  '${t1_sql}'::timestamptz
   AND dev IS NULL;"; then
      t_end="$(now_ms)"
      dur=$((t_end - t_start))
      [[ "$dur" -lt 0 ]] && dur=0
      echo "  done  ${chunk}  ${dur}ms"
      printf '0 %s\n' "$dur" > "${rcfile}"
      exit 0
    fi
    echo "  FAIL  ${chunk}" >&2
    printf '1 0\n' > "${rcfile}"
    exit 1
  ) &
  pid=$!
  inflight["$pid"]="$chunk"
  pid_job["$pid"]="$job_id"
  echo "  launched job=${job_id} pid=${pid} inflight=${#inflight[@]}/${TARGET_CONCURRENCY} (max=${MAX_CONCURRENCY})"
}

wait_one() {
  local pid job_id rcfile found=0 line
  DONE_CHUNK=""
  DONE_RC=1
  DONE_MS=0

  while [[ "$found" -eq 0 ]]; do
    for pid in "${!inflight[@]}"; do
      job_id="${pid_job[$pid]}"
      rcfile="${STATUS_DIR}/${job_id}.rc"
      if [[ -f "$rcfile" ]]; then
        line="$(tr -d '\r' < "$rcfile")"
        DONE_RC="$(awk '{print $1}' <<<"$line")"
        DONE_MS="$(awk '{print $2}' <<<"$line")"
        [[ -z "$DONE_MS" ]] && DONE_MS=0
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
  while [[ "${#inflight[@]}" -lt "$TARGET_CONCURRENCY" ]]; do
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

backfill_host_data_null_dev_main() {
  MAX_CONCURRENCY="${1:-30}"
  if ! [[ "$MAX_CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 [max_concurrency]" >&2
    echo "  max_concurrency must be a positive integer (default 30); adaptive ramps up to this cap" >&2
    exit 2
  fi

  MIN_CONCURRENCY="${HPCPERFSTATS_NULL_DEV_MIN_CONCURRENCY:-1}"
  if ! [[ "$MIN_CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
    echo "HPCPERFSTATS_NULL_DEV_MIN_CONCURRENCY must be a positive integer" >&2
    exit 2
  fi
  if [[ "$MIN_CONCURRENCY" -gt "$MAX_CONCURRENCY" ]]; then
    MIN_CONCURRENCY="$MAX_CONCURRENCY"
  fi

  FIXED_CONCURRENCY=0
  if [[ "${HPCPERFSTATS_NULL_DEV_FIXED_CONCURRENCY:-0}" =~ ^(1|true|yes)$ ]]; then
    FIXED_CONCURRENCY=1
  fi

  STALL_LIMIT="${HPCPERFSTATS_NULL_DEV_STALL_LIMIT:-5}"
  VACUUM_EVERY="${HPCPERFSTATS_NULL_DEV_VACUUM_EVERY:-1}"
  LAG_LIMIT_SEC="${HPCPERFSTATS_NULL_DEV_LAG_LIMIT_SEC:-30}"
  WAL_FRAC="${HPCPERFSTATS_NULL_DEV_WAL_FRAC:-0.70}"
  DISK_MIN_BYTES="${HPCPERFSTATS_NULL_DEV_DISK_MIN_BYTES:-10737418240}" # 10 GiB
  LATENCY_RATIO="${HPCPERFSTATS_NULL_DEV_LATENCY_RATIO:-2.0}"
  HEALTHY_NEEDED="${HPCPERFSTATS_NULL_DEV_HEALTHY_NEEDED:-3}"
  BASELINE_ALPHA="${HPCPERFSTATS_NULL_DEV_BASELINE_ALPHA:-0.2}"

  STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/backfill_host_data_null_dev.XXXXXX")"
  cleanup() {
    rm -rf "${STATUS_DIR}"
  }
  trap cleanup EXIT

  declare -gA inflight=()
  declare -gA pid_job=()
  declare -gA skipped=()
  declare -gA completed=()
  exclude=()
  excl_sql=""
  total_failed=0
  total_ok=0
  prev_left=""
  stall=0
  vacuum_since=0
  healthy_streak=0
  baseline_ms=0
  JOB_SEQ=0
  BEST_CONCURRENCY="$MIN_CONCURRENCY"
  TARGET_CONCURRENCY="$MIN_CONCURRENCY"
  if [[ "$FIXED_CONCURRENCY" -eq 1 ]]; then
    TARGET_CONCURRENCY="$MAX_CONCURRENCY"
  fi

  if [[ "$FIXED_CONCURRENCY" -eq 1 ]]; then
    echo "parallel host_data.dev NULL→'' backfill: fixed concurrency=${TARGET_CONCURRENCY} (time-range chunks; VACUUM every ${VACUUM_EVERY})"
  else
    echo "parallel host_data.dev NULL→'' backfill: adaptive workers ${MIN_CONCURRENCY}..${MAX_CONCURRENCY} (time-range chunks; VACUUM every ${VACUUM_EVERY}; lag/WAL/disk/latency throttle)"
  fi

  local left comp
  comp="$(compressed_n)"
  if [[ "$comp" =~ ^[0-9]+$ && "$comp" -gt 0 ]]; then
    echo "warning: ${comp} compressed host_data chunk(s) — not in worklist; decompress first for a complete backfill" >&2
  fi

  while true; do
    exclude_list
    if [[ "${#exclude[@]}" -gt 0 ]]; then
      left="$(remaining_chunks "${exclude[@]}")"
    else
      left="$(remaining_chunks)"
    fi
    echo "remaining_chunks=${left} inflight=${#inflight[@]}/${TARGET_CONCURRENCY} completed=${#completed[@]} skipped=${#skipped[@]} ok=${total_ok} failed=${total_failed} best_workers=${BEST_CONCURRENCY}"

    if [[ "$left" == "0" && "${#inflight[@]}" -eq 0 ]]; then
      echo "done (ok=${total_ok} skipped_failures=${total_failed} best_workers=${BEST_CONCURRENCY})"
      exit 0
    fi

    fill_slots

    if [[ "${#inflight[@]}" -eq 0 ]]; then
      if [[ "$left" == "0" ]]; then
        echo "done (ok=${total_ok} skipped_failures=${total_failed} best_workers=${BEST_CONCURRENCY})"
        exit 0
      fi
      echo "aborting: ${left} uncompressed chunk(s) remain but none could be started (all skipped or unavailable)" >&2
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
      maybe_adapt_concurrency "${DONE_MS:-0}"
    else
      total_failed=$((total_failed + 1))
      skipped["$DONE_CHUNK"]=1
      echo "warning: skipping failed chunk for rest of run: ${DONE_CHUNK}" >&2
      healthy_streak=0
      maybe_adapt_concurrency 0
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
          echo "aborting: remaining_chunks stuck at ${left}; draining ${#inflight[@]} in-flight job(s)" >&2
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
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  backfill_host_data_null_dev_main "$@"
fi
