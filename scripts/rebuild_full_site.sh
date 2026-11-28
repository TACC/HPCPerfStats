#!/usr/bin/env bash
# Full-stack image rebuild: podman build web, proxy, db (hpcperfstats-db:guard1);
# compose down + up -d --no-build. db_pg18 (profile pg18-migrate, legacy PG15 sites)
# is restarted only when it was running before the rebuild.
# CPython bake uses --enable-optimizations only (no custom Clang host profiles).
# Pipeline cgroup memory.high is commented out (do not throttle below memory.max).
# Never use podman-compose up --build here.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_full_site.sh
#   ./scripts/rebuild_full_site.sh --dry-run
#   ./scripts/rebuild_full_site.sh --build-only
#   ./scripts/rebuild_full_site.sh --no-start
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "rebuild_full_site.sh: run ./scripts/rebuild_full_site.sh — do not source (.) this file; exit would close your shell" >&2
  # shellcheck disable=SC2317  # exit runs only when executed instead of sourced
  return 2 2>/dev/null || exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/podman_runtime.sh
source "${SCRIPT_DIR}/lib/podman_runtime.sh"
# shellcheck source=lib/compose_pipeline_memory_high.sh
source "${SCRIPT_DIR}/lib/compose_pipeline_memory_high.sh"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
# shellcheck source=lib/pipeline_supervisor_shutdown.sh
source "${SCRIPT_DIR}/lib/pipeline_supervisor_shutdown.sh"
# shellcheck source=lib/compose_bind_volume_guard.sh
source "${SCRIPT_DIR}/lib/compose_bind_volume_guard.sh"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0

PG18_SERVICE=db_pg18
DB_IMAGE=hpcperfstats-db:guard1
# Tag of db images built before the guard existed; it must not stay startable.
UNGUARDED_DB_IMAGE=localhost/hpcperfstats-db:latest
LEGACY_OVERLAY=docker-compose.pg15-primary.yaml
DELETED_OVERLAY=docker-compose.pg18-primary.yaml
DB_LOCK_FILE=.hpc-server.lock
PG18_PRIMARY_MARKER=hpcperfstats-primary
PG15_RETIRED_FILE=hpcperfstats-retired
PG18_READY_TIMEOUT="${HPCPERFSTATS_PG18_READY_TIMEOUT:-3600}"
# Host paths of the PG15 (postgres_data) and PG18 (postgres_data_pg18) binds; set by preflight.
PG15_BIND_DEVICE=
PG18_BIND_DEVICE=
PG18_WAS_RUNNING=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_full_site.sh [options]

Build default-stack images and the db image hpcperfstats-db:guard1, then tear
down the compose project (graceful stop, preserve volumes) and start the default
stack:

  podman-compose --profile pg18-migrate down --remove-orphans
  podman-compose up -d --no-build

db_pg18 (profile pg18-migrate, defined only by the legacy docker-compose.pg15-primary.yaml
include) is started again only when it was running before down; the PG18 copy
container is never started.

Preflight (before any build or stop; also under --dry-run) refuses, leaving the
stack running, when docker-compose.yaml still names docker-compose.pg18-primary.yaml
or defines db_pg18 itself, when the db data binds do not match the include line,
or when the merged compose wiring mounts PG15 data into a PG18 image (or back).
After down, every container on the host that mounts the PG15 or PG18 bind is
stopped (podman stop -t 1800) and both server lock files must be free before up.

Down uses each service stop_grace_period from compose (do not pass down -t; a global
timeout overrides longer db/rabbitmq/pg18 grace and risks SIGKILL mid-shutdown).
--profile pg18-migrate keeps db_pg18 in that graceful stop instead of orphan cleanup.

Bind volume guard (scripts/lib/compose_bind_volume_guard.sh): between down and up,
missing o=bind volumes are created with their device: options and existing ones
without them abort the rebuild (stack left down). After up, each container's bind
mounts are checked by device:inode against the host path; on mismatch the
offending containers and the pipeline are stopped and the script fails.

Images bake ThinLTO + CPython --enable-optimizations (upstream mini profile path).
There is no custom Clang host-profile bake path and no legacy profile-phase flags.

Options:
  --dry-run        Print planned steps only
  --build-only     Build images only; do not up -d
  --no-start       Build images, skip up -d
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile-phase | --pgo-*)
      echo "rebuild_full_site.sh: custom Clang host profiling was removed; unsupported option: $1" >&2
      echo "rebuild_full_site.sh: images always bake with CPython --enable-optimizations only" >&2
      usage >&2
      exit 2
      ;;
    --full-*)
      if [[ "${1#--full-}" == pgo ]]; then
        echo "rebuild_full_site.sh: custom Clang host profiling was removed; unsupported option: $1" >&2
        echo "rebuild_full_site.sh: images always bake with CPython --enable-optimizations only" >&2
        usage >&2
        exit 2
      fi
      echo "rebuild_full_site.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      shift
      ;;
    --no-start)
      NO_START=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "rebuild_full_site.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] (not executed) $*"
    return 0
  fi
  "$@"
}

preflight_refuse() {
  echo "rebuild_full_site.sh: refusing: $*" >&2
  echo "rebuild_full_site.sh: nothing was built or stopped; the stack is left as it was." >&2
  exit 1
}

# Print yes or no for a host path under a bind; print unreadable when the
# nearest existing parent cannot be listed (absence cannot be told apart).
preflight_probe() {
  # shellcheck disable=SC2016  # $1 expands in the probe shell
  compose_host_fs sh -c '
    p=$1
    d=$p
    while [ "$d" != / ]; do
      d=$(dirname "$d")
      [ -e "$d" ] && break
    done
    if [ -e "$p" ]; then
      if [ -r "$p" ]; then echo yes; else echo unreadable; fi
    elif [ -r "$d" ] && [ -x "$d" ]; then
      echo no
    else
      echo unreadable
    fi' sh "$1" || echo unreadable
}

# True when the path exists; refuses when it cannot be read.
preflight_exists() {
  local state
  state="$(preflight_probe "$1")"
  [[ "${state}" != unreadable ]] \
    || preflight_refuse "cannot read ${1} (unreadable counts as refuse)"
  [[ "${state}" == yes ]]
}

# Print the trimmed PG_VERSION under a data dir, or nothing when it is absent.
preflight_pg_version() {
  local file="$1/PG_VERSION" version
  preflight_exists "${file}" || return 0
  version="$(compose_host_fs cat "${file}")" \
    || preflight_refuse "cannot read ${file} (unreadable counts as refuse)"
  echo "${version//[[:space:]]/}"
}

# Read docker-compose.yaml alone: the merged config always has db_pg18 on legacy
# sites, so only the site file shows a stale db_pg18 stanza. Prints
# "<legacy include 0|1> <site defines db_pg18 0|1>".
preflight_site_file_facts() {
  local py
  py="$(compose_yaml_python)" || return 1
  LEGACY_OVERLAY="${LEGACY_OVERLAY}" "${py}" -c '
import os
import sys

import yaml


class Loader(yaml.SafeLoader):
    pass


def untagged(loader, suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


Loader.add_multi_constructor("!", untagged)
with open(sys.argv[1], encoding="utf-8") as fh:
    cfg = yaml.load(fh, Loader=Loader) or {}
paths = []
for entry in cfg.get("include") or []:
    if isinstance(entry, dict):
        entry = entry.get("path") or []
    paths.extend(entry if isinstance(entry, list) else [entry])
overlay = os.environ["LEGACY_OVERLAY"]
legacy = any(os.path.basename(str(p)) == overlay for p in paths)
print(int(legacy), int("db_pg18" in (cfg.get("services") or {})))
' "${REPO_ROOT}/docker-compose.yaml"
}

# Read the merged --profile pg18-migrate config on stdin. Prints
# "device <volume> <path>" for the o=bind postgres_data / postgres_data_pg18
# volumes and one "violation <text>" line per wiring that can put a server on
# the other major version's data or leave PG15 db without its mounts.
preflight_merged_wiring() {
  local py
  py="$(compose_yaml_python)" || return 1
  "${py}" -c '
import re
import sys

import yaml

cfg = yaml.safe_load(sys.stdin) or {}
for key in ("postgres_data", "postgres_data_pg18"):
    vol = (cfg.get("volumes") or {}).get(key) or {}
    opts = vol.get("driver_opts") or {}
    if "bind" in str(opts.get("o", "")).split(",") and opts.get("device"):
        print("device", key, opts["device"])


def mounts(svc):
    for vol in svc.get("volumes") or []:
        if isinstance(vol, dict):
            src, tgt = str(vol.get("source", "")), str(vol.get("target", ""))
        else:
            parts = str(vol).split(":")
            src, tgt = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        yield src, tgt.rstrip("/") or "/"


pg18_image = re.compile(r"(^|/)hpcperfstats-db([:@]|$)")
pg15_image = re.compile(r"(^|/)timescale/timescaledb:[^/]*-pg15$")
required_pg15 = {
    "/run/postgresql": "the postgres_runtime socket",
    "/etc/postgresql/pg_hba.conf": "the pg_hba.conf",
    "/usr/local/bin/hpc-pg15-guard.sh": "the db-docker-pg15-guard.sh",
}
for name, svc in sorted((cfg.get("services") or {}).items()):
    image = str((svc or {}).get("image", ""))
    svc_mounts = list(mounts(svc or {}))
    if pg18_image.search(image):
        for src, tgt in svc_mounts:
            if src == "postgres_data" or tgt == "/var/lib/postgresql/data":
                print("violation", f"{name} ({image}) mounts {src or tgt} at {tgt}"
                      " (PG15 data in a PG18 image)")
    if pg15_image.search(image):
        for src, tgt in svc_mounts:
            if src == "postgres_data_pg18":
                print("violation", f"{name} ({image}) mounts postgres_data_pg18"
                      " (PG18 data in the PG15 image)")
        if name == "db":
            targets = {tgt for _, tgt in svc_mounts}
            for tgt, what in required_pg15.items():
                if tgt not in targets:
                    print("violation", f"PG15 db lacks {what} mount at {tgt}")
'
}

preflight() {
  local legacy site_db_pg18 config wiring kind key value pg15_version
  local -a violations=()
  podman_runtime_require
  if [[ ! -f "${REPO_ROOT}/docker-compose.defaults.yaml" ]]; then
    echo "rebuild_full_site.sh: docker-compose.defaults.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "rebuild_full_site.sh: missing docker-compose.yaml (cp docker-compose.yaml.example docker-compose.yaml)" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
  # Before config: podman-compose crashes on an include file that no longer exists.
  if grep -v '^[[:space:]]*#' docker-compose.yaml | grep -Fq "${DELETED_OVERLAY}"; then
    preflight_refuse "docker-compose.yaml names the deleted ${DELETED_OVERLAY};" \
      "delete that include line (PG18 is the defaults db;" \
      "legacy PG15 sites include ${LEGACY_OVERLAY})"
  fi
  read -r legacy site_db_pg18 < <(preflight_site_file_facts || echo error) || true
  [[ "${legacy}" =~ ^[01]$ ]] || preflight_refuse "cannot parse docker-compose.yaml with PyYAML"
  if [[ "${site_db_pg18}" == 1 ]]; then
    preflight_refuse "docker-compose.yaml defines a db_pg18: service; delete that stanza" \
      "(db_pg18 comes from ${LEGACY_OVERLAY} on legacy PG15 sites only)"
  fi
  if ! "${PODMAN_COMPOSE[@]}" config >/dev/null 2>&1; then
    echo "rebuild_full_site.sh: compose parse failed (often invalid docker-compose.yaml)." >&2
    echo "rebuild_full_site.sh: paste whole optional blocks from docker-compose.yaml.example — do not uncomment lines inside a block partially." >&2
    "${PODMAN_COMPOSE[@]}" config 2>&1 | tail -25 >&2 || true
    exit 1
  fi
  config="$("${PODMAN_COMPOSE[@]}" --profile pg18-migrate config 2>/dev/null)" \
    || preflight_refuse "podman-compose --profile pg18-migrate config failed"
  wiring="$(preflight_merged_wiring <<<"${config}")" \
    || preflight_refuse "cannot parse the merged --profile pg18-migrate config"
  while read -r kind key value; do
    case "${kind}" in
      device)
        [[ "${key}" == postgres_data ]] && PG15_BIND_DEVICE="${value}"
        [[ "${key}" == postgres_data_pg18 ]] && PG18_BIND_DEVICE="${value}"
        ;;
      violation) violations+=("${key} ${value}") ;;
    esac
  done <<<"${wiring}"
  if [[ "${#violations[@]}" -gt 0 ]]; then
    printf 'rebuild_full_site.sh: merged compose wiring: %s\n' "${violations[@]}" >&2
    preflight_refuse "the merged --profile pg18-migrate config wires a db service to the" \
      "wrong data or drops a PG15 db mount (see above)"
  fi
  if [[ -z "${PG18_BIND_DEVICE}" ]]; then
    preflight_refuse "postgres_data_pg18 is not an o=bind volume with a device in" \
      "docker-compose.yaml (copy its block from docker-compose.yaml.example)"
  fi
  pg15_version=
  if [[ -n "${PG15_BIND_DEVICE}" ]]; then
    pg15_version="$(preflight_pg_version "${PG15_BIND_DEVICE}")" || exit 1
  fi
  if [[ "${legacy}" == 1 ]]; then
    if [[ "${pg15_version}" != 15 ]]; then
      preflight_refuse "docker-compose.yaml includes ${LEGACY_OVERLAY}, but postgres_data is" \
        "not an o=bind volume whose device holds PG_VERSION 15" \
        "(device '${PG15_BIND_DEVICE}', PG_VERSION '${pg15_version}')"
    fi
    return 0
  fi
  if preflight_exists "${PG18_BIND_DEVICE}/18/docker/PG_VERSION" \
    && ! preflight_exists "${PG18_BIND_DEVICE}/${PG18_PRIMARY_MARKER}"; then
    preflight_refuse "${PG18_BIND_DEVICE}/18/docker holds a PG18 cluster without the" \
      "${PG18_PRIMARY_MARKER} marker and docker-compose.yaml does not include" \
      "${LEGACY_OVERLAY}: an unpromoted PG18 copy must not start as db" \
      "(restore the include line until the cutover)"
  fi
  if [[ "${pg15_version}" == 15 ]] \
    && ! preflight_exists "${PG15_BIND_DEVICE}/${PG15_RETIRED_FILE}"; then
    preflight_refuse "postgres_data (${PG15_BIND_DEVICE}) holds the live PG15 cluster (no" \
      "${PG15_RETIRED_FILE}) and docker-compose.yaml does not include ${LEGACY_OVERLAY};" \
      "add it to include: after docker-compose.defaults.yaml"
  fi
}

run_stack_up_and_verify() {
  local rc=0
  PHASE=up
  compose_up_project_stack || return 1
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    verify_default_stack_running || rc=1
    LAST_STEP="postgres socket check (web/pipeline see db socket)"
    if [[ "${rc}" -eq 0 ]]; then
      verify_postgres_socket_shared || rc=1
    fi
    # Runs even when a service failed to start: a running db on the wrong mount must stop.
    LAST_STEP="bind volume guard (post-up mount check)"
    compose_bind_volumes_verify_mounted || rc=1
    [[ "${rc}" -eq 0 ]] || return 1
  fi
  restart_db_pg18_if_it_was_running || return 1
  UP_COMPLETED=1
}

compose_build_web_image() {
  local git_commit
  LAST_STEP="podman build web"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  git_commit="${HPCPERFSTATS_GIT_COMMIT:-unknown}"
  if [[ "${git_commit}" == "unknown" ]] \
    && command -v git >/dev/null 2>&1 \
    && git -C "${REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    git_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  fi
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/pipeline_web.Dockerfile" \
    -t hpcperfstats \
    --target hpcperfstats-full \
    --build-arg "HPCPERFSTATS_GIT_COMMIT=${git_commit}" \
    "${REPO_ROOT}"
}

compose_build_proxy_image() {
  LAST_STEP="podman build proxy"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/proxy.Dockerfile" \
    -t hpcperfstats-proxy \
    "${REPO_ROOT}"
}

compose_build_db_image() {
  local old_id users
  LAST_STEP="podman build db (${DB_IMAGE})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/db.Dockerfile" \
    -t "${DB_IMAGE}" \
    "${REPO_ROOT}/services-conf" || return 1
  LAST_STEP="retire ${UNGUARDED_DB_IMAGE}"
  if ! "${PODMAN[@]}" image exists "${UNGUARDED_DB_IMAGE}" 2>/dev/null; then
    return 0
  fi
  # Old runbook commands and stale site stubs name :latest: remove it, or point it
  # at the guarded image while a container still holds the old one.
  old_id="$("${PODMAN[@]}" image inspect --format '{{.Id}}' "${UNGUARDED_DB_IMAGE}")" \
    || return 1
  users="$("${PODMAN[@]}" ps -a --no-trunc --format '{{.ImageID}}')" || return 1
  if grep -q "${old_id#sha256:}" <<<"${users}"; then
    run_cmd "${PODMAN[@]}" tag "${DB_IMAGE}" "${UNGUARDED_DB_IMAGE}"
  else
    run_cmd "${PODMAN[@]}" rmi "${UNGUARDED_DB_IMAGE}"
  fi
}

compose_build_all_images() {
  compose_build_web_image || return 1
  compose_build_proxy_image || return 1
  compose_build_db_image || return 1
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] (not executed) compose_ensure_local_stack_image_tags"
    return 0
  fi
  compose_ensure_local_stack_image_tags
}

compose_down_project() {
  if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' \
    "$(compose_service_container_name "${PG18_SERVICE}")" 2>/dev/null)" == true ]]; then
    PG18_WAS_RUNNING=1
  fi
  LAST_STEP="pipeline supervisor shutdown"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  pipeline_supervisor_shutdown
  LAST_STEP="podman-compose down --remove-orphans"
  echo "rebuild_full_site.sh: ${LAST_STEP} (volumes preserved; no -v; stop_grace_period from compose) ..."
  # db_pg18 sits behind profile pg18-migrate: without it, down stops db_pg18 as an
  # orphan at podman's default stop timeout and SIGKILLs its shutdown checkpoint.
  run_cmd "${PODMAN_COMPOSE[@]}" --profile pg18-migrate down --remove-orphans
}

# Two servers on one data dir corrupt it, and PostgreSQL's postmaster.pid check
# cannot see a server in another container. After down, stop every container on
# this host (any project) whose mounts resolve to the PG15 or PG18 bind, then
# require both server lock files to be free.
db_bind_containers_stop() {
  local ctr kind name src dev resolved lock
  local -a binds=() hits=()
  for dev in "${PG15_BIND_DEVICE}" "${PG18_BIND_DEVICE}"; do
    [[ -n "${dev}" ]] && binds+=("$(realpath -m "${dev}")")
  done
  while IFS= read -r ctr; do
    [[ -n "${ctr}" ]] || continue
    while IFS='|' read -r kind name src; do
      resolved="${src}"
      if [[ "${kind}" == volume ]]; then
        resolved="$("${PODMAN[@]}" volume inspect --format '{{index .Options "device"}}' \
          "${name}" 2>/dev/null)"
        [[ -n "${resolved}" ]] || resolved="${src}"
      fi
      [[ -n "${resolved}" ]] || continue
      resolved="$(realpath -m "${resolved}")"
      for dev in "${binds[@]}"; do
        if [[ "${resolved}" == "${dev}" || "${resolved}" == "${dev}/"* ]]; then
          hits+=("${ctr}")
        fi
      done
    done < <("${PODMAN[@]}" inspect "${ctr}" \
      --format '{{range .Mounts}}{{.Type}}|{{.Name}}|{{.Source}}{{"\n"}}{{end}}')
  done < <("${PODMAN[@]}" ps -a --format '{{.Names}}')
  if [[ "${#hits[@]}" -gt 0 ]]; then
    mapfile -t hits < <(printf '%s\n' "${hits[@]}" | sort -u)
  fi
  for ctr in "${hits[@]}"; do
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${ctr}")" == true ]]; then
      echo "rebuild_full_site.sh: ${ctr} mounts a db data bind; podman stop -t 1800 ${ctr}"
      "${PODMAN[@]}" stop -t 1800 "${ctr}" >/dev/null || true
    fi
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${ctr}")" != false ]]; then
      echo "rebuild_full_site.sh: ${ctr} mounts a db data bind and is still running" >&2
      return 1
    fi
  done
  for dev in "${binds[@]}"; do
    lock="${dev}/${DB_LOCK_FILE}"
    [[ "$(preflight_probe "${lock}")" == no ]] && continue
    if ! compose_host_fs flock -n "${lock}" true; then
      echo "rebuild_full_site.sh: ${lock} is held (or unreadable): a server outside any" \
        "listed container still has this data dir open" >&2
      return 1
    fi
  done
}

compose_up_project_stack() {
  compose_down_project || return 1
  LAST_STEP="db bind container scan (stop -t 1800, lock files free)"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] (not executed) db_bind_containers_stop"
  elif ! db_bind_containers_stop; then
    echo "rebuild_full_site.sh: refusing to start; stack left down (see db bind scan above)" >&2
    return 1
  fi
  LAST_STEP="bind volume guard (pre-up volume options)"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] (not executed) compose_bind_volumes_preflight"
  elif ! compose_bind_volumes_preflight; then
    echo "rebuild_full_site.sh: refusing to start; stack left down (see bind volume guard above)" >&2
    return 1
  fi
  LAST_STEP="podman-compose up -d --no-build (default stack)"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" up -d --no-build || return 1
}

compose_service_container_name() {
  printf '%s_%s_1' "${HPCPERFSTATS_COMPOSE_PROJECT}" "$1"
}

wait_for_compose_service_running() {
  local svc="$1"
  local timeout="${2:-${HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT:-300}}"
  local name elapsed=0
  name="$(compose_service_container_name "${svc}")"
  while [[ "${elapsed}" -lt "${timeout}" ]]; do
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${name}" 2>/dev/null)" == "true" ]]; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "rebuild_full_site.sh: timed out waiting for ${name} to run (${timeout}s)" >&2
  "${PODMAN[@]}" logs --tail 50 "${name}" 2>&1 || true
  return 1
}

verify_default_stack_running() {
  local svc
  local -a required=(web pipeline valkey proxy db rabbitmq)
  for svc in "${required[@]}"; do
    if ! compose_service_container_exists "${svc}"; then
      echo "rebuild_full_site.sh: required service container missing: ${svc}" >&2
      return 1
    fi
    wait_for_compose_service_running "${svc}" || return 1
  done
}

# web/pipeline connect to db over the Unix socket on the shared postgres_runtime
# tmpfs ([DEFAULT] host = /run/postgresql). "Running" is not enough: a container
# whose ini points at the socket but that does not share db's socket volume
# waits forever in its startup script and still shows as running.
verify_postgres_socket_shared() {
  local sock=/run/postgresql/.s.PGSQL.5432
  local timeout="${HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT:-300}"
  local svc name elapsed
  for svc in web pipeline; do
    name="$(compose_service_container_name "${svc}")"
    elapsed=0
    until "${PODMAN[@]}" exec "${name}" test -S "${sock}" 2>/dev/null; do
      if [[ "${elapsed}" -ge "${timeout}" ]]; then
        echo "rebuild_full_site.sh: ${svc} cannot see the db socket ${sock} after ${timeout}s;" \
          "db, web and pipeline must all mount postgres_runtime (recreate them together)" >&2
        "${PODMAN[@]}" logs --tail 20 "${name}" 2>&1 || true
        return 1
      fi
      sleep 2
      elapsed=$((elapsed + 2))
    done
  done
}

# Restart db_pg18 only when it ran before down and the site still defines it
# (legacy include). The PG18 copy container is never started here.
restart_db_pg18_if_it_was_running() {
  local name elapsed=0 io_method services
  [[ "${PG18_WAS_RUNNING}" -eq 1 ]] || return 0
  LAST_STEP="restart ${PG18_SERVICE} (was running before down)"
  services="$("${PODMAN_COMPOSE[@]}" --profile pg18-migrate config --services 2>/dev/null)" \
    || return 1
  if ! grep -qx "${PG18_SERVICE}" <<<"${services}"; then
    echo "rebuild_full_site.sh: ${PG18_SERVICE} was running but is no longer defined; not restarted"
    return 0
  fi
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" --profile pg18-migrate up -d --no-build "${PG18_SERVICE}" \
    || return 1
  [[ "${DRY_RUN}" -eq 0 ]] || return 0
  name="$(compose_service_container_name "${PG18_SERVICE}")"
  until "${PODMAN[@]}" exec "${name}" \
    pg_isready -U hpcperfstats -d postgres -h 127.0.0.1 -p 5432 >/dev/null 2>&1; do
    if [[ "${elapsed}" -ge "${PG18_READY_TIMEOUT}" ]]; then
      echo "rebuild_full_site.sh: ${name} not ready after ${PG18_READY_TIMEOUT}s" >&2
      "${PODMAN[@]}" logs --tail 50 "${name}" 2>&1 || true
      return 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  # shellcheck disable=SC2016  # expands inside the container
  io_method="$("${PODMAN[@]}" exec "${name}" sh -c 'PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -h 127.0.0.1 -U "${POSTGRES_USER}" -d postgres -Atc "SHOW io_method"')" || return 1
  echo "rebuild_full_site.sh: ${name} ready; io_method=${io_method}"
}

PHASE=preflight
UP_COMPLETED=0
LAST_STEP=

on_exit() {
  local ec=$?
  if [[ "${UP_COMPLETED}" -eq 0 && "${BUILD_ONLY}" -eq 0 && "${NO_START}" -eq 0 && "${DRY_RUN}" -eq 0 ]]; then
    echo "rebuild_full_site.sh: exited (code ${ec}) before podman-compose up finished (phase=${PHASE} last_step=${LAST_STEP:-unknown})." >&2
    if [[ "${PHASE}" == preflight ]]; then
      echo "rebuild_full_site.sh: preflight refused; nothing was built or stopped." >&2
    fi
    if [[ "${PHASE}" == build ]]; then
      echo "rebuild_full_site.sh: scroll above for the failing build step" \
        "(often proxy or db after hpcperfstats:latest tags)." >&2
      echo "rebuild_full_site.sh: retry in isolation: podman build -f ${REPO_ROOT}/services-conf/proxy.Dockerfile -t hpcperfstats-proxy ${REPO_ROOT}" >&2
    fi
    echo "rebuild_full_site.sh: collectstatic in Dockerfile output alone is NOT a successful full rebuild." >&2
  fi
}

print_stack_summary() {
  local net_id svc name
  echo ""
  echo "=== Stack summary: project=${HPCPERFSTATS_COMPOSE_PROJECT} network=${HPCPERFSTATS_NETWORK_NAME} ==="
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] skipped stack summary"
    return 0
  fi
  net_id="$("${PODMAN[@]}" network inspect "${HPCPERFSTATS_NETWORK_NAME}" --format '{{.Id}}' 2>/dev/null || echo 'MISSING')"
  echo "Network ${HPCPERFSTATS_NETWORK_NAME} id=${net_id}"
  echo ""
  echo "--- podman-compose ps -a ---"
  "${PODMAN_COMPOSE[@]}" ps -a 2>/dev/null || true
  echo ""
  echo "--- Containers (name / container ID / image ID / status) ---"
  "${PODMAN[@]}" ps -a \
    --filter "label=io.podman.compose.project=${HPCPERFSTATS_COMPOSE_PROJECT}" \
    --format 'table {{.Names}}\t{{.ID}}\t{{.ImageID}}\t{{.Status}}' 2>/dev/null || true
  echo ""
  echo "--- Per-service inspect (container + image) ---"
  for svc in web pipeline valkey proxy db rabbitmq "${PG18_SERVICE}"; do
    name="${HPCPERFSTATS_COMPOSE_PROJECT}_${svc}_1"
    if "${PODMAN[@]}" container exists "${name}" 2>/dev/null; then
      "${PODMAN[@]}" inspect "${name}" --format "${svc}: container={{.Id}} image={{.Image}}" 2>/dev/null || true
    else
      echo "${svc}: (no container ${name})"
    fi
  done
}

run_compose_up_memory_high_and_summary() {
  echo "=== rebuild_full_site.sh: starting podman-compose down + up (no --build) ==="
  run_stack_up_and_verify || exit 1

  # memory.high left at the cgroup default (max). Do not call apply_pipeline_memory_high.
  # PHASE=memory_high
  # if [[ "${DRY_RUN}" -eq 0 ]]; then
  #   apply_pipeline_memory_high || exit 1
  # fi

  PHASE=summary
  print_stack_summary
}

main() {
  trap on_exit EXIT
  preflight

  PHASE=build
  echo "rebuild_full_site.sh: project=${HPCPERFSTATS_COMPOSE_PROJECT} DRY_RUN=${DRY_RUN} BUILD_ONLY=${BUILD_ONLY} NO_START=${NO_START}"
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "NOTE: compose down + up runs only when all three flags above are 0." >&2
  fi
  compose_build_all_images

  if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "rebuild_full_site.sh: FINISHED BUILD ONLY — did not run podman-compose up (use without --build-only to recreate containers)." >&2
    UP_COMPLETED=1
    return 0
  fi

  if [[ "${NO_START}" -eq 1 ]]; then
    echo "rebuild_full_site.sh: FINISHED WITHOUT UP — did not run podman-compose up (use without --no-start to recreate containers)." >&2
    UP_COMPLETED=1
    return 0
  fi

  run_compose_up_memory_high_and_summary
  echo "Full-site rebuild complete (build + up)."
}

main "$@"
