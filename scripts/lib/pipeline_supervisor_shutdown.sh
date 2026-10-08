#!/usr/bin/env bash
# Ask the pipeline supervisord to stop before compose down/stop.
# Socket present: supervisorctl shutdown. Exit 0, or output containing "Shut down"
# (the socket often closes as supervisord exits), is success. Any other failure
# aborts the rebuild before compose down.
# Socket missing (image not yet recreated): SIGTERM the supervisord pid, not PID 1.
# Requires PODMAN, HPCPERFSTATS_COMPOSE_PROJECT, and DRY_RUN from the caller.

pipeline_supervisor_shutdown() {
  local timeout_s="${PIPELINE_SUPERVISOR_SHUTDOWN_TIMEOUT:-480}"
  local name running sup_pid elapsed
  name="${HPCPERFSTATS_COMPOSE_PROJECT:?}_pipeline_1"
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "[dry-run] would shut down supervisord in ${name} (timeout ${timeout_s}s)"
    return 0
  fi
  running="$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${name}" 2>/dev/null || true)"
  if [[ "${running}" != "true" ]]; then
    echo "pipeline_supervisor_shutdown: ${name} is not running; skip"
    return 0
  fi
  if "${PODMAN[@]}" exec "${name}" test -S /tmp/supervisor.sock; then
    local rc=0
    local out
    out="$(timeout "${timeout_s}" "${PODMAN[@]}" exec "${name}" \
      supervisorctl -c /home/hpcperfstats/services-conf/supervisord.conf shutdown 2>&1)" || rc=$?
    printf '%s\n' "${out}"
    if [[ "${rc}" -eq 0 || "${out}" == *"Shut down"* ]]; then
      return 0
    fi
    echo "pipeline_supervisor_shutdown: supervisorctl shutdown failed in ${name} (exit ${rc})" >&2
    return 1
  fi
  echo "pipeline_supervisor_shutdown: ${name} has no /tmp/supervisor.sock; SIGTERM supervisord" >&2
  sup_pid="$("${PODMAN[@]}" exec "${name}" ps -eo pid=,args= | awk '/\/usr\/bin\/supervisord/ { print $1; exit }')"
  if [[ -z "${sup_pid}" || "${sup_pid}" == "1" ]]; then
    echo "pipeline_supervisor_shutdown: supervisord pid not found; skip" >&2
    return 0
  fi
  "${PODMAN[@]}" exec "${name}" kill -TERM "${sup_pid}" || true
  elapsed=0
  while [[ "${elapsed}" -lt "${timeout_s}" ]]; do
    if ! "${PODMAN[@]}" exec "${name}" kill -0 "${sup_pid}" 2>/dev/null; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "pipeline_supervisor_shutdown: supervisord pid ${sup_pid} still alive after ${timeout_s}s" >&2
  return 0
}
