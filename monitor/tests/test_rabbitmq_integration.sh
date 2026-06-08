#!/usr/bin/env bash
set -euo pipefail

MONITOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="${MONITOR_ROOT}/tests"
BOOTSTRAP="${TEST_ROOT}/scripts/bootstrap_local_rabbitmq.sh"
VALIDATOR="${TEST_ROOT}/rmq_integration_validate.py"

BUILD_DIR="${1:-${MONITOR_ROOT}/.build-static}"
DAEMON_BIN="${BUILD_DIR}/src/hpcperfstatsd"
REPO_ROOT="$(cd "${MONITOR_ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
VENV_PIP="${REPO_ROOT}/.venv/bin/pip"
REQ_FILE="${TEST_ROOT}/requirements-rabbitmq-integration.txt"

if [[ ! -x "${DAEMON_BIN}" ]]; then
  echo "error: daemon binary not found: ${DAEMON_BIN}" >&2
  exit 1
fi
if [[ ! -x "${BOOTSTRAP}" ]]; then
  echo "error: bootstrap script not executable: ${BOOTSTRAP}" >&2
  exit 1
fi
if [[ ! -x "${VENV_PY}" || ! -x "${VENV_PIP}" ]]; then
  echo "error: missing python venv tools at ${REPO_ROOT}/.venv" >&2
  exit 1
fi

find_free_port() {
  "${VENV_PY}" - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

query_queue_depth() {
  "${VENV_PY}" - "$RMQ_PORT" "$QUEUE_NAME" "$RMQ_USER" "$RMQ_PASSWORD" "$RMQ_VHOST" <<'PY'
import sys
import pika

port = int(sys.argv[1])
queue = sys.argv[2]
user = sys.argv[3]
password = sys.argv[4]
vhost = sys.argv[5]
creds = pika.PlainCredentials(user, password)
params = pika.ConnectionParameters(
    host="127.0.0.1",
    port=port,
    virtual_host=vhost,
    credentials=creds,
    connection_attempts=2,
    retry_delay=0.5,
    socket_timeout=3,
    stack_timeout=5,
)
conn = pika.BlockingConnection(params)
ch = conn.channel()
q = ch.queue_declare(queue=queue, durable=True, passive=False)
print(q.method.message_count)
conn.close()
PY
}

TMP_BASE="$(mktemp -d)"
RMQ_PORT="$(find_free_port)"
RMQ_DIST_PORT="$(find_free_port)"
QUEUE_NAME="hpcperfstats-integration-$$"
RMQ_USER="${RMQ_USER:-hpcperfstats}"
RMQ_PASSWORD="${RMQ_PASSWORD:-hpcperfstats}"
RMQ_VHOST="${RMQ_VHOST:-/}"
RMQ_NODE_NAME="rabbitmq_monitor_${$}"

CONF_FILE="${TMP_BASE}/hpcperfstats.conf"
JOBID_FILE="${TMP_BASE}/jobid"
DUMP_DIR="${TMP_BASE}/dump"
RMQ_LOG_DIR="${TMP_BASE}/rmq-logs"
RMQ_DATA_DIR="${TMP_BASE}/rmq-data"
RMQ_ETC_DIR="${TMP_BASE}/rmq-etc"
MONITOR_LOG="${TMP_BASE}/monitor.log"
RMQ_MESSAGES_JSON="${TMP_BASE}/messages.json"
MON_PID=""
TEST_OK=0
MONITOR_FAILED_NO_MESSAGES=0

run_monitor_until_queue_depth() {
  local daemon_path="$1"
  local min_messages="$2"
  local queue_wait_timeout="$3"
  local queue_wait_poll="$4"
  local queue_depth=0
  local elapsed=0

  echo "Launching monitor daemon for RabbitMQ publish: ${daemon_path}"
  "${daemon_path}" -s "127.0.0.1" -p "${RMQ_PORT}" -q "${QUEUE_NAME}" -c "${CONF_FILE}" -t "${DUMP_DIR}" >"${MONITOR_LOG}" 2>&1 &
  MON_PID="$!"

  sleep "${MONITOR_INITIAL_SLEEP_SECONDS:-3}"

  echo "Waiting for queue depth >= ${min_messages}"
  while (( elapsed < queue_wait_timeout )); do
    if ! kill -0 "${MON_PID}" 2>/dev/null; then
      echo "error: monitor exited before queue populated" >&2
      echo "--- monitor log ---" >&2
      sed -n '1,220p' "${MONITOR_LOG}" >&2 || true
      return 1
    fi
    queue_depth="$(query_queue_depth || echo 0)"
    echo "queue depth=${queue_depth} elapsed=${elapsed}s"
    if [[ "${queue_depth}" =~ ^[0-9]+$ ]] && (( queue_depth >= min_messages )); then
      return 0
    fi
    sleep "${queue_wait_poll}"
    elapsed=$((elapsed + queue_wait_poll))
  done

  echo "error: queue did not reach ${min_messages} message(s), depth=${queue_depth}" >&2
  echo "--- monitor log ---" >&2
  sed -n '1,220p' "${MONITOR_LOG}" >&2 || true
  MONITOR_FAILED_NO_MESSAGES=1
  return 1
}

cleanup() {
  if [[ -n "${MON_PID}" ]]; then
    kill "${MON_PID}" 2>/dev/null || true
    if ! timeout 10 bash -c "while kill -0 ${MON_PID} 2>/dev/null; do sleep 0.2; done"; then
      kill -9 "${MON_PID}" 2>/dev/null || true
    fi
    wait "${MON_PID}" 2>/dev/null || true
  fi
  INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.cache/hpcperfstats-rmq}" \
    ERLANG_HOME="${ERLANG_HOME:-}" \
    RABBITMQ_HOME="${RABBITMQ_HOME:-}" \
    RMQ_NODE_NAME="${RMQ_NODE_NAME}" \
    "${BOOTSTRAP}" stop || true
  if [[ "${TEST_OK}" -eq 1 || "${KEEP_RMQ_TEST_TMP:-0}" != "1" ]]; then
    rm -rf "${TMP_BASE}"
  else
    echo "preserving temp dir for debugging: ${TMP_BASE}" >&2
  fi
}
trap cleanup EXIT

mkdir -p "${DUMP_DIR}" "${RMQ_LOG_DIR}" "${RMQ_DATA_DIR}" "${RMQ_ETC_DIR}"
printf -- "-\n" >"${JOBID_FILE}"

cat >"${CONF_FILE}" <<EOF
server 127.0.0.1
port ${RMQ_PORT}
queue ${QUEUE_NAME}
user ${RMQ_USER}
password ${RMQ_PASSWORD}
jobid_file ${JOBID_FILE}
sample_freq 1
send_freq 1
buffer 64
EOF

if ! "${VENV_PY}" -c "import pika" >/dev/null 2>&1; then
  echo "Installing Python dependency pika into ${REPO_ROOT}/.venv"
  "${VENV_PIP}" install -r "${REQ_FILE}"
fi

echo "Starting local RabbitMQ bootstrap"
INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.cache/hpcperfstats-rmq}" \
  ERLANG_HOME="${ERLANG_HOME:-}" \
  RABBITMQ_HOME="${RABBITMQ_HOME:-}" \
  RMQ_PORT="${RMQ_PORT}" \
  RMQ_NODE_NAME="${RMQ_NODE_NAME}" \
  RMQ_USER="${RMQ_USER}" \
  RMQ_PASSWORD="${RMQ_PASSWORD}" \
  RMQ_VHOST="${RMQ_VHOST}" \
  RMQ_DIST_PORT="${RMQ_DIST_PORT}" \
  RMQ_LOG_DIR="${RMQ_LOG_DIR}" \
  RMQ_DATA_DIR="${RMQ_DATA_DIR}" \
  RMQ_ETC_DIR="${RMQ_ETC_DIR}" \
  ERLANG_ARCHIVE_SHA256="${ERLANG_ARCHIVE_SHA256:-}" \
  RABBITMQ_ARCHIVE_SHA256="${RABBITMQ_ARCHIVE_SHA256:-}" \
  SKIP_DOWNLOAD="${SKIP_DOWNLOAD:-0}" \
  "${BOOTSTRAP}" start

MIN_MESSAGES="${MIN_MESSAGES:-2}"
QUEUE_WAIT_TIMEOUT_SECONDS="${QUEUE_WAIT_TIMEOUT_SECONDS:-45}"
QUEUE_WAIT_POLL_SECONDS="${QUEUE_WAIT_POLL_SECONDS:-1}"
if ! run_monitor_until_queue_depth "${DAEMON_BIN}" "${MIN_MESSAGES}" "${QUEUE_WAIT_TIMEOUT_SECONDS}" "${QUEUE_WAIT_POLL_SECONDS}"; then
  if [[ "${MONITOR_FAILED_NO_MESSAGES}" -eq 1 ]] && grep -q "mad_rpc_open_port" "${MONITOR_LOG}" 2>/dev/null; then
    echo "Retrying integration with monitor rebuilt --disable-infiniband"
    kill "${MON_PID}" 2>/dev/null || true
    wait "${MON_PID}" 2>/dev/null || true
    MON_PID=""
    (
      cd "${MONITOR_ROOT}" &&
      SKIP_DEPS=1 ./scripts/build_static_bundle.sh --disable-infiniband
    )
    if [[ ! -x "${DAEMON_BIN}" ]]; then
      echo "error: daemon binary missing after --disable-infiniband rebuild" >&2
      exit 1
    fi
    MONITOR_FAILED_NO_MESSAGES=0
    : >"${MONITOR_LOG}"
    run_monitor_until_queue_depth "${DAEMON_BIN}" "${MIN_MESSAGES}" "${QUEUE_WAIT_TIMEOUT_SECONDS}" "${QUEUE_WAIT_POLL_SECONDS}"
  else
    exit 1
  fi
fi

kill "${MON_PID}" 2>/dev/null || true
if ! timeout 15 bash -c "while kill -0 ${MON_PID} 2>/dev/null; do sleep 0.2; done"; then
  kill -9 "${MON_PID}" 2>/dev/null || true
fi
wait "${MON_PID}" 2>/dev/null || true
MON_PID=""

echo "Validating RabbitMQ messages"
"${VENV_PY}" -c "import pika; print('pika import ok')" >/dev/null
timeout "${VALIDATOR_TIMEOUT_SECONDS:-120}" \
"${VENV_PY}" "${VALIDATOR}" \
  --host "127.0.0.1" \
  --port "${RMQ_PORT}" \
  --queue "${QUEUE_NAME}" \
  --user "${RMQ_USER}" \
  --password "${RMQ_PASSWORD}" \
  --vhost "${RMQ_VHOST}" \
  --min-messages "${MIN_MESSAGES}" \
  --timeout-seconds "${VALIDATE_TIMEOUT_SECONDS:-20}" \
  --out-json "${RMQ_MESSAGES_JSON}"

TEST_OK=1
echo "RabbitMQ integration test passed"
