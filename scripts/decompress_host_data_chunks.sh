#!/usr/bin/env bash
# Parallel host_data decompress for Stage 1 (OPERATOR_HOST_DATA_DEV_UNIQUENESS.md).
#
# Selects up to N compressed chunks, decompresses each in its own psql session,
# waits for the batch, then repeats until compressed_chunks = 0.
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
while true; do
  left="$(remaining)"
  left="${left//$'\r'/}"
  echo "compressed_chunks=${left}"
  if [[ "$left" == "0" ]]; then
    echo "done"
    exit 0
  fi

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
    echo "done (no chunk names returned)"
    exit 0
  fi

  batch=$((batch + 1))
  echo "batch ${batch}: decompressing ${#filtered[@]} chunk(s) in parallel"
  pids=()
  for c in "${filtered[@]}"; do
    (
      echo "  start ${c}"
      # Second arg true => if_compressed (no-op if another session already finished it).
      psql_cmd "SET statement_timeout = 0; SELECT decompress_chunk('${c}'::regclass, true);"
      echo "  done  ${c}"
    ) &
    pids+=("$!")
  done

  fail=0
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      fail=1
    fi
  done
  if [[ "$fail" -ne 0 ]]; then
    echo "batch ${batch}: one or more decompressions failed" >&2
    exit 1
  fi
done
