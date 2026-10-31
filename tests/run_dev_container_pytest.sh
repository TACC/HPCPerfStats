#!/usr/bin/env bash
# Run pytest inside the baked pipeline/web image against the long-running
# hpcperfstats-dev stack, with the live checkout bind-mounted.
#
# Why: DB-backed and Codon pyext tests need the compose network (host ``db``),
# the image interpreters (/opt/python3.14 GIL, /opt/python3.14t free-threaded)
# and the baked Codon .so files. Rebuilding the image through
# tests/run_db_pytest_workflow.sh takes over an hour; this runner reuses
# localhost/hpcperfstats:latest plus a small derived image that only adds
# pytest to both interpreters.
#
# Usage (from HPCPerfStats/):
#   tests/run_dev_container_pytest.sh [--ft] [--shell] [--mount SRC:DST[:ro]]...
#                                     [--exec] <pytest args... | command...>
#     --ft     run under /opt/python3.14t/bin/python (free-threaded; Codon pyext)
#     --shell  open an interactive bash instead of pytest (debugging)
#     --mount  extra bind mount (repeatable; e.g. a raw stats corpus directory)
#     --exec   run the remaining arguments as a command instead of pytest
#              (``$PY`` expands to the selected interpreter)
#
# Requires the hpcperfstats-dev db (and redis for cache tests) containers to be
# running: ``podman start hpcperfstats-dev_db_1 hpcperfstats-dev_redis_1``.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-dev
# shellcheck source=../scripts/lib/podman_runtime.sh
. "${ROOT_DIR}/scripts/lib/podman_runtime.sh"
podman_runtime_require

BASE_IMAGE="${HPCPERFSTATS_DEV_BASE_IMAGE:-localhost/hpcperfstats:latest}"
DEVTEST_IMAGE="${HPCPERFSTATS_DEVTEST_IMAGE:-localhost/hpcperfstats-devtest:latest}"
NETWORK="${HPCPERFSTATS_NETWORK_NAME}"
DB_CONTAINER="${HPCPERFSTATS_COMPOSE_PROJECT}_db_1"

PY=/usr/local/bin/python3
MODE=pytest
PYTEST_ARGS=()
EXTRA_MOUNTS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ft) PY=/opt/python3.14t/bin/python; shift ;;
    --shell) MODE=shell; shift ;;
    --exec) MODE=exec; shift ;;
    --mount)
      [[ $# -ge 2 ]] || { echo "--mount needs SRC:DST" >&2; exit 2; }
      EXTRA_MOUNTS+=(-v "$2")
      shift 2
      ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

db_state="$("${PODMAN[@]}" inspect --format '{{.State.Status}}' "$DB_CONTAINER" 2>/dev/null || true)"
if [[ "$db_state" != "running" ]]; then
  echo "run_dev_container_pytest: ${DB_CONTAINER} is not running (state=${db_state:-missing});" >&2
  echo "start it with: podman start ${DB_CONTAINER} ${HPCPERFSTATS_COMPOSE_PROJECT}_redis_1" >&2
  exit 69
fi

base_id="$("${PODMAN[@]}" image inspect --format '{{.Id}}' "$BASE_IMAGE")"
devtest_base="$("${PODMAN[@]}" image inspect --format '{{index .Labels "hpcperfstats.devtest.base"}}' "$DEVTEST_IMAGE" 2>/dev/null || true)"
if [[ "$devtest_base" != "$base_id" ]]; then
  echo "Building ${DEVTEST_IMAGE} (pytest for GIL + FT interpreters) from ${BASE_IMAGE}..." >&2
  "${PODMAN[@]}" build -q -t "$DEVTEST_IMAGE" \
    --label "hpcperfstats.devtest.base=${base_id}" \
    -f - "$ROOT_DIR/tests" <<EOF >&2
FROM ${BASE_IMAGE}
RUN /usr/local/bin/python3 -m pip install -q "pytest>=9.0" "pytest-django>=4.12.0" \
  && /opt/python3.14t/bin/python -m pip install -q "pytest>=9.0" "pytest-django>=4.12.0"
EOF
fi

mkdir -p "${HPCPERFSTATS_HOST_CACHE}/dev-container-pycache"

run_args=(
  run --rm
  --network "$NETWORK"
  -v "${ROOT_DIR}:/home/hpcperfstats:rw"
  -v "${HPCPERFSTATS_HOST_CACHE}/dev-container-pycache:/tmp/pycache:rw"
  -v "${HPCPERFSTATS_COMPOSE_PROJECT}_redis_runtime:/run/redis:rw"
  -e HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini
  -e HPCPERFSTATS_COMPOSE_NETWORK=1
  -e DOCKER_PYTEST_BIND_MOUNT=1
  -e PYTHONPATH=/home/hpcperfstats:/opt/hpcperfstats/codon_parse:/opt/hpcperfstats/codon_metrics
  -e PYTHONPYCACHEPREFIX=/tmp/pycache
  -e OMP_NUM_THREADS=1
  -w /home/hpcperfstats
  --entrypoint bash
)
# Pass-through knobs for metrics/golden tooling (only when set on the host).
for var in HPCPERFSTATS_METRICS_GOLDEN_DIR HPCPERFSTATS_METRICS_GOLDEN_REQUIRED \
  HPCPERFSTATS_GOLDEN_COMMIT \
  HPCPERFSTATS_METRICS_TELEMETRY HPCPERFSTATS_METRICS_PHASE_PROFILE \
  HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS HPCPERFSTATS_CODON_PYEXT_DIR \
  HPCPERFSTATS_CODON_METRICS_REQUIRED HPCPERFSTATS_CODON_METRICS_DIR; do
  if [[ -n "${!var+x}" ]]; then
    run_args+=(-e "${var}=${!var}")
  fi
done
# Metrics Codon pyext: a host-side build directory (CODON_METRICS_BUILD_DIR,
# e.g. test_runs/codon_metrics_out from a codon-build container run) is
# mounted at the baked path so tests see the image layout before a rebuild.
if [[ -n "${HPCPERFSTATS_CODON_METRICS_BUILD_DIR:-}" && -d "${HPCPERFSTATS_CODON_METRICS_BUILD_DIR}" ]]; then
  run_args+=(-v "${HPCPERFSTATS_CODON_METRICS_BUILD_DIR}:/opt/hpcperfstats/codon_metrics:ro")
fi
# Golden corpus: default to the dev-host location when it exists so a capture
# never writes into the ephemeral container filesystem (fixtures were lost once
# when the variable was unset on the host).
DEFAULT_GOLDEN_DIR=/data/user/sharrell/hpcperfstats-metrics-golden
if [[ -z "${HPCPERFSTATS_METRICS_GOLDEN_DIR:-}" && -d "${DEFAULT_GOLDEN_DIR}" ]]; then
  HPCPERFSTATS_METRICS_GOLDEN_DIR="${DEFAULT_GOLDEN_DIR}"
fi
if [[ -n "${HPCPERFSTATS_METRICS_GOLDEN_DIR:-}" && -d "${HPCPERFSTATS_METRICS_GOLDEN_DIR}" ]]; then
  run_args+=(-e "HPCPERFSTATS_METRICS_GOLDEN_DIR=${HPCPERFSTATS_METRICS_GOLDEN_DIR}")
  run_args+=(-v "${HPCPERFSTATS_METRICS_GOLDEN_DIR}:${HPCPERFSTATS_METRICS_GOLDEN_DIR}:rw")
fi
if [[ ${#EXTRA_MOUNTS[@]} -gt 0 ]]; then
  run_args+=("${EXTRA_MOUNTS[@]}")
fi
if [[ -t 0 && -t 1 ]]; then
  run_args+=(-it)
fi

if [[ "$MODE" == "shell" ]]; then
  exec "${PODMAN[@]}" "${run_args[@]}" "$DEVTEST_IMAGE"
fi
if [[ "$MODE" == "exec" ]]; then
  exec "${PODMAN[@]}" "${run_args[@]}" -e "PY=$PY" "$DEVTEST_IMAGE" \
    -c "${PYTEST_ARGS[*]}"
fi
exec "${PODMAN[@]}" "${run_args[@]}" "$DEVTEST_IMAGE" \
  -c "exec \"$PY\" -m pytest ${PYTEST_ARGS[*]@Q}"
