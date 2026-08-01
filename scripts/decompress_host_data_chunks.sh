#!/usr/bin/env bash
# Parallel host_data decompress for Stage 1 (OPERATOR_HOST_DATA_DEV_UNIQUENESS.md).
#
# Selects up to N compressed chunks, decompresses each in its own psql session,
# waits for the batch, then repeats until compressed_chunks = 0.
#
# Individual chunk failures are logged and skipped. The run aborts only when a
# whole batch fails or when compressed_chunks stops decreasing.
#
# Usage (from checkout with docker-compose.yaml):
#   ./scripts/decompress_host_data_chunks.sh           # concurrency 10
#   ./scripts/decompress_host_data_chunks.sh 20         # hpcperfstats02-class
#   ./scripts/decompress_host_data_chunks.sh 2          # hpcperfstats03 (tight disk)
#
# Watch free space between batches (especially site 03):
#   docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml \
#     exec db df -h /var/lib/postgresql/data
set -euo pipefail

CONCURRENCY="${1:-10}"
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [concurrency]" >&2
  echo "  concurrency must be a positive integer (default 10)" >&2
  exit 2
fi

# Consecutive batches without a drop in compressed_chunks before giving up.
STALL_LIMIT="${HPCPERFSTATS_DECOMPRESS_STALL_LIMIT:-2}"

COMPOSE=(docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml)

psql_at() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -Atc "$1"
}

psql_cmd() {
  "${COMPOSE[@]}" exec -T db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 -c "$1"
}

remaining() {
  psql_at "SELECT count(*) FILTER (WHERE is_compressed)
           FROM timescaledb_information.chunks
           WHERE hypertable_name = 'host_data';"
}

echo "parallel host_data decompress: concurrency=${CONCURRENCY}"
batch=0
total_failed=0
prev_left=""
stall=0

while true; do
  left="$(remaining)"
  left="${left//$'\r'/}"
  echo "compressed_chunks=${left}"
  if [[ "$left" == "0" ]]; then
    echo "done (skipped chunk failures: ${total_failed})"
    exit 0
  fi

  if [[ "$left" =~ ^[0-9]+$ && -n "$prev_left" ]]; then
    if [[ "$left" -ge "$prev_left" ]]; then
      stall=$((stall + 1))
      echo "warning: no progress (${stall}/${STALL_LIMIT}) — still ${left} compressed chunk(s)" >&2
      if [[ "$stall" -ge "$STALL_LIMIT" ]]; then
        echo "aborting: compressed_chunks stuck at ${left}; inspect the psql errors above" >&2
        exit 1
      fi
    else
      stall=0
    fi
  fi
  prev_left="$left"

  mapfile -t chunks < <(
    psql_at "SELECT format('%I.%I', chunk_schema, chunk_name)
             FROM timescaledb_information.chunks
             WHERE hypertable_name = 'host_data' AND is_compressed
             ORDER BY range_start
             LIMIT ${CONCURRENCY};"
  )

  # Drop blank lines from psql -At
  filtered=()
  for c in "${chunks[@]+"${chunks[@]}"}"; do
    c="${c//$'\r'/}"
    [[ -n "$c" ]] && filtered+=("$c")
  done
  if [[ ${#filtered[@]} -eq 0 ]]; then
    echo "done (no chunk names returned; skipped chunk failures: ${total_failed})"
    exit 0
  fi

  batch=$((batch + 1))
  echo "batch ${batch}: decompressing ${#filtered[@]} chunk(s) in parallel"
  pids=()
  names=()
  for c in "${filtered[@]}"; do
    (
      echo "  start ${c}"
      # Second arg true => if_not_compressed (notice, not error, when already done).
      psql_cmd "SET statement_timeout = 0; SELECT decompress_chunk('${c}'::regclass, true);"
      echo "  done  ${c}"
    ) &
    pids+=("$!")
    names+=("$c")
  done

  failed=0
  failed_names=()
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      failed=$((failed + 1))
      failed_names+=("${names[$i]}")
    fi
  done

  if [[ "$failed" -gt 0 ]]; then
    total_failed=$((total_failed + failed))
    echo "batch ${batch}: ${failed}/${#filtered[@]} chunk(s) failed: ${failed_names[*]}" >&2
    if [[ "$failed" -eq "${#filtered[@]}" ]]; then
      echo "aborting: every chunk in batch ${batch} failed; inspect the psql errors above" >&2
      exit 1
    fi
    echo "batch ${batch}: continuing with remaining chunks" >&2
  fi
done
