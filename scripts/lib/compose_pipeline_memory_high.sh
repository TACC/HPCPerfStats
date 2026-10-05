#!/usr/bin/env bash
# Pipeline cgroup v2 memory.high = 2/3 of live memory.max (integer bytes).
# Requires PODMAN, HPCPERFSTATS_COMPOSE_PROJECT (from podman_runtime.sh).

if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "compose_pipeline_memory_high.sh requires bash" >&2
  return 2 2>/dev/null || exit 2
fi

pipeline_container_name() {
  echo "${HPCPERFSTATS_COMPOSE_PROJECT}_pipeline_1"
}

compute_pipeline_memory_high_bytes() {
  local max_bytes="${1:?max bytes required}"
  echo $((max_bytes * 2 / 3))
}

_read_pipeline_cgroup_file() {
  local file_name="$1"
  local name
  name="$(pipeline_container_name)"
  "${PODMAN[@]}" exec -u 0 "${name}" cat "/sys/fs/cgroup/${file_name}" 2>/dev/null \
    | tr -d '[:space:]'
}

read_pipeline_memory_max_bytes() {
  local raw
  raw="$(_read_pipeline_cgroup_file memory.max)"
  if [[ -z "${raw}" || "${raw}" == "max" ]]; then
    echo "compose_pipeline_memory_high: memory.max is unlimited or unreadable (${raw:-empty})" >&2
    return 1
  fi
  if [[ ! "${raw}" =~ ^[0-9]+$ ]]; then
    echo "compose_pipeline_memory_high: unexpected memory.max value: ${raw}" >&2
    return 1
  fi
  echo "${raw}"
}

_format_bytes_mib() {
  local bytes="$1"
  echo $((bytes / 1048576))
}

wait_for_pipeline_container_running() {
  local timeout="${1:-${PIPELINE_MEMORY_HIGH_WAIT_TIMEOUT:-300}}"
  local name elapsed=0
  name="$(pipeline_container_name)"
  while [[ "${elapsed}" -lt "${timeout}" ]]; do
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${name}" 2>/dev/null)" == "true" ]]; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "compose_pipeline_memory_high: timed out waiting for ${name} to run (${timeout}s)" >&2
  return 1
}

apply_pipeline_memory_high() {
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "[dry-run] would set pipeline cgroup memory.high to 2/3 of memory.max"
    return 0
  fi

  wait_for_pipeline_container_running || return 1

  local max_bytes high_bytes name read_back max_mib high_mib
  max_bytes="$(read_pipeline_memory_max_bytes)" || return 1
  high_bytes="$(compute_pipeline_memory_high_bytes "${max_bytes}")"
  name="$(pipeline_container_name)"
  max_mib="$(_format_bytes_mib "${max_bytes}")"
  high_mib="$(_format_bytes_mib "${high_bytes}")"

  echo "Setting ${name} memory.high=${high_bytes} (${high_mib} MiB) from memory.max=${max_bytes} (${max_mib} MiB) ..."
  if ! "${PODMAN[@]}" exec -u 0 "${name}" sh -c "echo '${high_bytes}' > /sys/fs/cgroup/memory.high"; then
    echo "compose_pipeline_memory_high: failed to write memory.high" >&2
    return 1
  fi

  read_back="$(_read_pipeline_cgroup_file memory.high)"
  if [[ "${read_back}" != "${high_bytes}" ]]; then
    echo "compose_pipeline_memory_high: memory.high read-back mismatch (expected ${high_bytes}, got ${read_back:-empty})" >&2
    return 1
  fi
  echo "Verified pipeline memory.high=${read_back} (${high_mib} MiB)"
  return 0
}
