#!/usr/bin/env bash
set -euo pipefail

# Rootless RabbitMQ bootstrap/start helper for monitor integration tests.
#
# Supports either:
#   1) Preinstalled locations via ERLANG_HOME + RABBITMQ_HOME
#   2) Downloaded archives under INSTALL_ROOT (default cache path)
#
# Required env:
#   RMQ_PORT
#   RMQ_NODE_NAME
#   RMQ_USER
#   RMQ_PASSWORD
#   RMQ_VHOST
#   RMQ_DIST_PORT
#   RMQ_LOG_DIR
#   RMQ_DATA_DIR
#   RMQ_ETC_DIR
#
# Optional env:
#   INSTALL_ROOT (default: $HOME/.cache/hpcperfstats-rmq)
#   ERLANG_HOME, RABBITMQ_HOME
#   ERLANG_VERSION (default: 26.2.5.9)
#   RABBITMQ_VERSION (default: 3.13.7)
#   ERLANG_ARCHIVE_SHA256
#   RABBITMQ_ARCHIVE_SHA256
#   SKIP_DOWNLOAD=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ACTION="${1:-}"
if [[ -z "${ACTION}" ]]; then
  echo "usage: $0 <start|stop>" >&2
  exit 2
fi

INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.cache/hpcperfstats-rmq}"
ERLANG_VERSION="${ERLANG_VERSION:-26.2.5.9}"
RABBITMQ_VERSION="${RABBITMQ_VERSION:-3.13.7}"
SKIP_DOWNLOAD="${SKIP_DOWNLOAD:-0}"

ERLANG_TGZ="${INSTALL_ROOT}/otp-${ERLANG_VERSION}.tar.gz"
RABBITMQ_TGZ="${INSTALL_ROOT}/rabbitmq-server-generic-unix-${RABBITMQ_VERSION}.tar.xz"
ERLANG_SRC_DIR="${INSTALL_ROOT}/otp_src_${ERLANG_VERSION}"
ERLANG_PREFIX="${INSTALL_ROOT}/erlang-${ERLANG_VERSION}"
RABBITMQ_PREFIX="${INSTALL_ROOT}/rabbitmq_server-${RABBITMQ_VERSION}"
RABBITMQ_EXTRACT_DIR="${INSTALL_ROOT}/rabbitmq_server-${RABBITMQ_VERSION}"

ERLANG_URL="https://github.com/erlang/otp/releases/download/OTP-${ERLANG_VERSION}/otp_src_${ERLANG_VERSION}.tar.gz"
RABBITMQ_URL="https://github.com/rabbitmq/rabbitmq-server/releases/download/v${RABBITMQ_VERSION}/rabbitmq-server-generic-unix-${RABBITMQ_VERSION}.tar.xz"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "error: env ${name} is required" >&2
    exit 1
  fi
}

rmq_cookie() {
  if [[ -n "${RMQ_ERLANG_COOKIE:-}" ]]; then
    printf "%s" "${RMQ_ERLANG_COOKIE}"
    return
  fi
  printf "hpcperfstats_monitor_cookie"
}

dump_logs() {
  if [[ -d "${RMQ_LOG_DIR:-}" ]]; then
    for f in "${RMQ_LOG_DIR}"/*; do
      [[ -f "${f}" ]] || continue
      echo "--- ${f} ---" >&2
      sed -n '1,200p' "${f}" >&2 || true
    done
  fi
}

run_rmqctl() {
  if ! timeout 30 "${RABBITMQ_HOME}/sbin/rabbitmqctl" "$@"; then
    dump_logs
    return 1
  fi
}

await_startup_with_retry() {
  local i
  for i in $(seq 1 60); do
    if timeout 5 "${RABBITMQ_HOME}/sbin/rabbitmqctl" await_startup >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  timeout 30 "${RABBITMQ_HOME}/sbin/rabbitmqctl" await_startup
}

sha256_verify_or_die() {
  local file="$1"
  local expected="$2"
  if [[ -z "${expected}" ]]; then
    return 0
  fi
  local got
  got="$(sha256sum "${file}" | awk '{print $1}')"
  if [[ "${got}" != "${expected}" ]]; then
    echo "error: sha256 mismatch for ${file}" >&2
    echo "expected: ${expected}" >&2
    echo "got:      ${got}" >&2
    exit 1
  fi
}

download_if_missing() {
  local url="$1"
  local out="$2"
  if [[ -f "${out}" ]]; then
    return 0
  fi
  if [[ "${SKIP_DOWNLOAD}" == "1" ]]; then
    echo "error: missing ${out} and SKIP_DOWNLOAD=1" >&2
    exit 1
  fi
  curl -fsSL "${url}" -o "${out}"
}

build_erlang_if_needed() {
  if [[ -x "${ERLANG_PREFIX}/bin/erl" ]]; then
    ERLANG_HOME="${ERLANG_PREFIX}"
    return 0
  fi

  mkdir -p "${INSTALL_ROOT}"
  download_if_missing "${ERLANG_URL}" "${ERLANG_TGZ}"
  sha256_verify_or_die "${ERLANG_TGZ}" "${ERLANG_ARCHIVE_SHA256:-}"

  rm -rf "${ERLANG_SRC_DIR}"
  mkdir -p "${ERLANG_SRC_DIR}"
  tar -xzf "${ERLANG_TGZ}" -C "${ERLANG_SRC_DIR}" --strip-components=1

  (
    cd "${ERLANG_SRC_DIR}" &&
      CC="${ERLANG_CC:-gcc}" CXX="${ERLANG_CXX:-g++}" ./configure --prefix="${ERLANG_PREFIX}" &&
      CC="${ERLANG_CC:-gcc}" CXX="${ERLANG_CXX:-g++}" make -j"$(getconf _NPROCESSORS_ONLN)" &&
      CC="${ERLANG_CC:-gcc}" CXX="${ERLANG_CXX:-g++}" make install
  )
  ERLANG_HOME="${ERLANG_PREFIX}"
}

install_rabbitmq_if_needed() {
  if [[ -x "${RABBITMQ_PREFIX}/sbin/rabbitmq-server" ]]; then
    RABBITMQ_HOME="${RABBITMQ_PREFIX}"
    return 0
  fi

  mkdir -p "${INSTALL_ROOT}"
  download_if_missing "${RABBITMQ_URL}" "${RABBITMQ_TGZ}"
  sha256_verify_or_die "${RABBITMQ_TGZ}" "${RABBITMQ_ARCHIVE_SHA256:-}"

  rm -rf "${RABBITMQ_EXTRACT_DIR}"
  mkdir -p "${INSTALL_ROOT}"
  tar -xJf "${RABBITMQ_TGZ}" -C "${INSTALL_ROOT}"
  RABBITMQ_HOME="${RABBITMQ_PREFIX}"
}

resolve_homes() {
  if [[ -n "${ERLANG_HOME:-}" ]]; then
    if [[ ! -x "${ERLANG_HOME}/bin/erl" ]]; then
      echo "error: ERLANG_HOME does not contain bin/erl: ${ERLANG_HOME}" >&2
      exit 1
    fi
  else
    build_erlang_if_needed
  fi

  if [[ -n "${RABBITMQ_HOME:-}" ]]; then
    if [[ ! -x "${RABBITMQ_HOME}/sbin/rabbitmq-server" ]]; then
      echo "error: RABBITMQ_HOME does not contain sbin/rabbitmq-server: ${RABBITMQ_HOME}" >&2
      exit 1
    fi
  else
    install_rabbitmq_if_needed
  fi
}

start_rabbitmq() {
  require_env RMQ_PORT
  require_env RMQ_NODE_NAME
  require_env RMQ_USER
  require_env RMQ_PASSWORD
  require_env RMQ_VHOST
  require_env RMQ_DIST_PORT
  require_env RMQ_LOG_DIR
  require_env RMQ_DATA_DIR
  require_env RMQ_ETC_DIR

  resolve_homes

  mkdir -p "${RMQ_LOG_DIR}" "${RMQ_DATA_DIR}" "${RMQ_ETC_DIR}"
  local conf="${RMQ_ETC_DIR}/rabbitmq.conf"
  cat >"${conf}" <<EOF
listeners.tcp.default = 127.0.0.1:${RMQ_PORT}
loopback_users.guest = false
EOF

  export RABBITMQ_HOME
  export RABBITMQ_CONFIG_FILE="${RMQ_ETC_DIR}/rabbitmq"
  export RABBITMQ_LOG_BASE="${RMQ_LOG_DIR}"
  export RABBITMQ_MNESIA_BASE="${RMQ_DATA_DIR}"
  export RABBITMQ_NODENAME="${RMQ_NODE_NAME}"
  export RABBITMQ_DIST_PORT="${RMQ_DIST_PORT}"
  export RABBITMQ_ERLANG_COOKIE="$(rmq_cookie)"
  export RABBITMQ_PID_FILE="${RMQ_DATA_DIR}/rabbitmq.pid"
  export PATH="${ERLANG_HOME}/bin:${RABBITMQ_HOME}/sbin:${PATH}"

  nohup "${RABBITMQ_HOME}/sbin/rabbitmq-server" \
    >"${RMQ_LOG_DIR}/rabbitmq-server.stdout.log" \
    2>"${RMQ_LOG_DIR}/rabbitmq-server.stderr.log" < /dev/null &
  if ! await_startup_with_retry; then
    dump_logs
    return 1
  fi

  if ! run_rmqctl list_vhosts -q | awk '{print $1}' | grep -qx "${RMQ_VHOST}"; then
    run_rmqctl add_vhost "${RMQ_VHOST}"
  fi

  if ! run_rmqctl list_users -q | awk '{print $1}' | grep -qx "${RMQ_USER}"; then
    run_rmqctl add_user "${RMQ_USER}" "${RMQ_PASSWORD}"
  else
    run_rmqctl change_password "${RMQ_USER}" "${RMQ_PASSWORD}"
  fi
  run_rmqctl set_permissions -p "${RMQ_VHOST}" "${RMQ_USER}" ".*" ".*" ".*"
  run_rmqctl set_user_tags "${RMQ_USER}" management
}

stop_rabbitmq() {
  if [[ -z "${RABBITMQ_HOME:-}" ]]; then
    if [[ -x "${RABBITMQ_PREFIX}/sbin/rabbitmqctl" ]]; then
      RABBITMQ_HOME="${RABBITMQ_PREFIX}"
    else
      return 0
    fi
  fi
  export RABBITMQ_HOME
  export RABBITMQ_NODENAME="${RMQ_NODE_NAME:-rabbit@localhost}"
  export RABBITMQ_ERLANG_COOKIE="$(rmq_cookie)"
  export PATH="${ERLANG_HOME:-${ERLANG_PREFIX}}/bin:${RABBITMQ_HOME}/sbin:${PATH}"
  timeout 30 "${RABBITMQ_HOME}/sbin/rabbitmqctl" shutdown || true
}

case "${ACTION}" in
start)
  start_rabbitmq
  ;;
stop)
  stop_rabbitmq
  ;;
*)
  echo "usage: $0 <start|stop>" >&2
  exit 2
  ;;
esac
