#!/usr/bin/env bash
# Opt-in sync_timedb throughput benchmark workflow (compose + free-threaded 3.14t).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_HELPER="$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
if [[ ! -f "$COMPOSE_HELPER" ]]; then
  cat >&2 <<EOF
Missing compose helpers: ${COMPOSE_HELPER}

Handoff: copy tests/compose_test_cmd.sh from the HPCPerfStats checkout and rerun.
EOF
  exit 78
fi

# shellcheck source=compose_test_cmd.sh
. "$COMPOSE_HELPER"

usage() {
  cat <<'EOF'
Run opt-in sync_timedb benchmark pytest under the pipeline free-threaded ABI.

Usage:
  tests/run_sync_timedb_benchmark_workflow.sh [options] [-- pytest_extra_args...]

Options:
  --keep-env      Keep compose services/volumes after run
  --skip-build    Skip podman-compose build pipeline
  --screening     Run ingest-width screening (requires derived corpus under
                  test_runs/sync_timedb_bench/corpus_smoke by default)
  --knee          Run ingest-width knee confirmation (corpus_steady;
                  widths 48,64,80,96; 5 replicates by default)
  --e2            Run closed-book mid-size E2 timing against corpus_steady
  --knobs         Run supporting-knob sweeps at fixed width 48 (corpus_steady)
  --e6            Run E6 parse_feed A/B arm at width 48 (corpus_steady).
                  Set HPCPERFSTATS_E6_ARM=baseline|candidate (default baseline)
  --e7            Run E7 proc_merge/build_df A/B arm at width 48 (corpus_steady).
                  Set HPCPERFSTATS_E7_ARM=baseline|candidate (default baseline)
  --e8            Run E8 delta_s/collapse_s hold-seconds A/B (Horizon frame;
                  retain on per-hold seconds, not files/s)
  --contention    Run FT contention wave A/B arm at width 48 (corpus_steady).
                  Requires HPCPERFSTATS_CONTENTION_WAVE; set
                  HPCPERFSTATS_CONTENTION_ARM=baseline|candidate
  --loaded48      Run loaded-48 continuous-refill soak (mixed small/medium/large
                  corpus files; width 48). Set HPCPERFSTATS_LOADED48_HOURS
                  (default 6; use 0.1 for smoke).
  --host-insert   Run write-only host_data bulk_create vs COPY A/B (≥100k rows).
  --proc-insert   Run write-only proc_data bulk_create vs COPY A/B (≥100k rows).
  -h, --help      Show this help

Environment:
  HPCPERFSTATS_SYNC_TIMEDB_BENCH=1         Set by this script for long benchmark tests
  HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1     Set by --screening
  HPCPERFSTATS_SYNC_TIMEDB_KNEE=1          Set by --knee
  HPCPERFSTATS_SYNC_TIMEDB_E2=1            Set by --e2
  HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1         Set by --knobs
  HPCPERFSTATS_SYNC_TIMEDB_E6=1            Set by --e6
  HPCPERFSTATS_SYNC_TIMEDB_E7=1            Set by --e7
  HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1    Set by --contention
  HPCPERFSTATS_SYNC_TIMEDB_LOADED48=1      Set by --loaded48
  HPCPERFSTATS_SYNC_TIMEDB_HOST_INSERT=1   Set by --host-insert
  HPCPERFSTATS_SYNC_TIMEDB_PROC_INSERT=1   Set by --proc-insert
  HPCPERFSTATS_HOST_INSERT_ROWS            Optional row count (default 100000)
  HPCPERFSTATS_HOST_INSERT_REPLICATES      Optional replicates (default 5)
  HPCPERFSTATS_PROC_INSERT_ROWS            Optional row count (default 100000)
  HPCPERFSTATS_PROC_INSERT_REPLICATES      Optional replicates (default 5)
  HPCPERFSTATS_E6_ARM                      baseline|candidate (default baseline)
  HPCPERFSTATS_E7_ARM                      baseline|candidate (default baseline)
  HPCPERFSTATS_CONTENTION_ARM              baseline|candidate (default baseline)
  HPCPERFSTATS_CONTENTION_WAVE             Wave id (caches, park_resume, ...)
  HPCPERFSTATS_LOADED48_HOURS              Soak wall hours (default 6)
  HPCPERFSTATS_COMPOSE_NETWORK=1           Set by this script for django_db tests
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS   Optional CSV override (default 1..96 or knee)
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES  Optional replicate count
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS   Optional corpus path inside container/repo
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S  Optional per-replicate ingest timeout
  HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH     Optional fixed ingest width for --knobs
  HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH        Optional fixed ingest width for --e6
  HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH        Optional fixed ingest width for --e7
  HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH  Optional fixed width for --contention
  HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH  Optional fixed width for --loaded48

Runtime:
  Rootless Podman + podman-compose via tests/compose_test_cmd.sh (podman-runtime.mdc).

Inside the pipeline container this workflow runs:
  /opt/python3.14t/bin/python -m pytest tests/sync_timedb_benchmark -q --tb=short

Host-safe unit tests (no compose) run with:
  ../.venv/bin/python3 -m pytest tests/sync_timedb_benchmark -q --tb=short
EOF
}

KEEP_ENV=0
SKIP_BUILD=0
SCREENING=0
KNEE=0
E2=0
KNOBS=0
E6=0
E7=0
E8=0
CONTENTION=0
LOADED48=0
HOST_INSERT=0
PROC_INSERT=0
PYTEST_EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --)
      shift
      PYTEST_EXTRA=("$@")
      break
      ;;
    --keep-env)
      KEEP_ENV=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --screening)
      SCREENING=1
      shift
      ;;
    --knee)
      KNEE=1
      shift
      ;;
    --e2)
      E2=1
      shift
      ;;
    --knobs)
      KNOBS=1
      shift
      ;;
    --e6)
      E6=1
      shift
      ;;
    --e7)
      E7=1
      shift
      ;;
    --e8)
      E8=1
      shift
      ;;
    --contention)
      CONTENTION=1
      shift
      ;;
    --loaded48)
      LOADED48=1
      shift
      ;;
    --host-insert)
      HOST_INSERT=1
      shift
      ;;
    --proc-insert)
      PROC_INSERT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      PYTEST_EXTRA+=("$1")
      shift
      ;;
  esac
done

compose_prepare_bind_mount || exit 1
compose_run_inner_script_prepare_env
compose_web_repo_bind_mount_args || exit 1

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Building pipeline image for sync_timedb benchmark workflow..."
  compose_test build pipeline
fi

echo "Starting db/redis for sync_timedb benchmark workflow..."
compose_test up -d db redis
echo "Waiting for PostgreSQL readiness..."
ready=0
for _ in $(seq 1 60); do
  if compose_test exec -T db pg_isready -h localhost -U hpcperfstats; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" -ne 1 ]]; then
  echo "PostgreSQL did not become ready within 120s" >&2
  compose_test logs db >&2 || true
  exit 1
fi

ARGS_FILE=""
if ((${#PYTEST_EXTRA[@]} > 0)); then
  ARGS_FILE="$(mktemp "${HPCPERFSTATS_HOST_TMP:-/tmp}/pytest-extra-args.XXXXXX")"
  printf '%s\n' "${PYTEST_EXTRA[@]}" >"$ARGS_FILE"
fi

echo "Running sync_timedb benchmark pytest (3.14t pipeline ABI)..."
set +e
RUN_ARGS=(
  run --rm -T
  -e HPCPERFSTATS_SYNC_TIMEDB_BENCH=1
  -e HPCPERFSTATS_COMPOSE_NETWORK=1
  "${compose_run_inner_script_bind_mount_env[@]}"
  "${compose_web_repo_bind_mount_args[@]}"
)
if [[ "$KNEE" -eq 1 || "$KNOBS" -eq 1 || "$E6" -eq 1 || "$E7" -eq 1 \
   || "$E8" -eq 1 || "$CONTENTION" -eq 1 || "$LOADED48" -eq 1 \
   || "$HOST_INSERT" -eq 1 || "$PROC_INSERT" -eq 1 ]]; then
  # Ambient screening leftovers (e.g. WIDTHS=1,2,4,8 REPLICATES=2) must not
  # override knee/knobs/e6/e7/contention/loaded48 defaults. Opt-in with KNEE_ALLOW_SCREEN_ENV=1.
  if [[ "${HPCPERFSTATS_SYNC_TIMEDB_KNEE_ALLOW_SCREEN_ENV:-0}" != "1" ]]; then
    unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS
    unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES
  fi
fi
if [[ "$SCREENING" -eq 1 || "$KNEE" -eq 1 || "$KNOBS" -eq 1 \
   || "$E6" -eq 1 || "$E7" -eq 1 || "$E8" -eq 1 || "$CONTENTION" -eq 1 \
   || "$LOADED48" -eq 1 || "$HOST_INSERT" -eq 1 || "$PROC_INSERT" -eq 1 ]]; then
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S}")
fi
if [[ "$SCREENING" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1)
fi
if [[ "$KNEE" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_KNEE=1)
  # Knee reuses the width-screening pytest module with knee defaults.
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1)
fi
if [[ "$E2" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_E2=1)
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS}")
fi
if [[ "$KNOBS" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1)
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH=${HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH}")
fi
if [[ "$E6" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_E6=1)
  RUN_ARGS+=(-e "HPCPERFSTATS_E6_ARM=${HPCPERFSTATS_E6_ARM:-baseline}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH=${HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH}")
fi
if [[ "$E7" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_E7=1)
  RUN_ARGS+=(-e "HPCPERFSTATS_E7_ARM=${HPCPERFSTATS_E7_ARM:-baseline}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH=${HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH}")
fi
if [[ "$E8" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_E8=1)
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=${HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES}")
fi
if [[ "$CONTENTION" -eq 1 ]]; then
  if [[ -z "${HPCPERFSTATS_CONTENTION_WAVE:-}" ]]; then
    echo "HPCPERFSTATS_CONTENTION_WAVE is required with --contention" >&2
    exit 2
  fi
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1)
  RUN_ARGS+=(-e "HPCPERFSTATS_CONTENTION_WAVE=${HPCPERFSTATS_CONTENTION_WAVE}")
  RUN_ARGS+=(-e "HPCPERFSTATS_CONTENTION_ARM=${HPCPERFSTATS_CONTENTION_ARM:-baseline}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH=${HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH}")
fi
if [[ "$LOADED48" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_LOADED48=1)
  RUN_ARGS+=(-e "HPCPERFSTATS_LOADED48_HOURS=${HPCPERFSTATS_LOADED48_HOURS:-6}")
  [[ -n "${HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH=${HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH}")
  # Forward insert-path A/B arms into the soak (COPY vs bulk_create).
  [[ -n "${HPCPERFSTATS_HOST_INSERT_ARM:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_HOST_INSERT_ARM=${HPCPERFSTATS_HOST_INSERT_ARM}")
  [[ -n "${HPCPERFSTATS_PROC_INSERT_ARM:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_PROC_INSERT_ARM=${HPCPERFSTATS_PROC_INSERT_ARM}")
  [[ -n "${HPCPERFSTATS_SYNC_HOST_DATA_COPY:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_HOST_DATA_COPY=${HPCPERFSTATS_SYNC_HOST_DATA_COPY}")
  [[ -n "${HPCPERFSTATS_SYNC_PROC_DATA_COPY:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_SYNC_PROC_DATA_COPY=${HPCPERFSTATS_SYNC_PROC_DATA_COPY}")
fi
if [[ "$HOST_INSERT" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_HOST_INSERT=1)
  [[ -n "${HPCPERFSTATS_HOST_INSERT_ROWS:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_HOST_INSERT_ROWS=${HPCPERFSTATS_HOST_INSERT_ROWS}")
  [[ -n "${HPCPERFSTATS_HOST_INSERT_REPLICATES:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_HOST_INSERT_REPLICATES=${HPCPERFSTATS_HOST_INSERT_REPLICATES}")
fi
if [[ "$PROC_INSERT" -eq 1 ]]; then
  RUN_ARGS+=(-e HPCPERFSTATS_SYNC_TIMEDB_PROC_INSERT=1)
  [[ -n "${HPCPERFSTATS_PROC_INSERT_ROWS:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_PROC_INSERT_ROWS=${HPCPERFSTATS_PROC_INSERT_ROWS}")
  [[ -n "${HPCPERFSTATS_PROC_INSERT_REPLICATES:-}" ]] && \
    RUN_ARGS+=(-e "HPCPERFSTATS_PROC_INSERT_REPLICATES=${HPCPERFSTATS_PROC_INSERT_REPLICATES}")
fi
if [[ -n "$ARGS_FILE" ]]; then
  RUN_ARGS+=(-v "$ARGS_FILE:/tmp/hpcperfstats_pytest_extra_args:ro")
fi
RUN_ARGS+=(--entrypoint bash pipeline -s)

{
  cat tests/compose_inner_pip_install.sh
  echo
  tail -n +2 tests/run_sync_timedb_benchmark_inner.sh
} | compose_test "${RUN_ARGS[@]}"
status=$?
set -e

if [[ -n "$ARGS_FILE" ]]; then
  rm -f "$ARGS_FILE"
fi

if [[ "$KEEP_ENV" -eq 0 ]]; then
  podman_compose_teardown "${COMPOSE_TEST[@]}"
fi

if [[ "$status" -ne 0 ]]; then
  echo "sync_timedb benchmark workflow failed (exit=$status)" >&2
  exit "$status"
fi

echo "sync_timedb benchmark workflow complete."
