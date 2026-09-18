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
  -h, --help      Show this help

Environment:
  HPCPERFSTATS_SYNC_TIMEDB_BENCH=1         Set by this script for long benchmark tests
  HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1     Set by --screening
  HPCPERFSTATS_SYNC_TIMEDB_KNEE=1          Set by --knee
  HPCPERFSTATS_SYNC_TIMEDB_E2=1            Set by --e2
  HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1         Set by --knobs
  HPCPERFSTATS_COMPOSE_NETWORK=1           Set by this script for django_db tests
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS   Optional CSV override (default 1..96 or knee)
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES  Optional replicate count
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS   Optional corpus path inside container/repo
  HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S  Optional per-replicate ingest timeout
  HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH     Optional fixed ingest width for --knobs

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
if [[ "$KNEE" -eq 1 || "$KNOBS" -eq 1 ]]; then
  # Ambient screening leftovers (e.g. WIDTHS=1,2,4,8 REPLICATES=2) must not
  # override knee/knobs defaults. Opt-in with KNEE_ALLOW_SCREEN_ENV=1.
  if [[ "${HPCPERFSTATS_SYNC_TIMEDB_KNEE_ALLOW_SCREEN_ENV:-0}" != "1" ]]; then
    unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS
    unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES
  fi
fi
if [[ "$SCREENING" -eq 1 || "$KNEE" -eq 1 || "$KNOBS" -eq 1 ]]; then
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
