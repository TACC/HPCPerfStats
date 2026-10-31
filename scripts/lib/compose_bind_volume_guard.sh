#!/usr/bin/env bash
# Guard against named "bind" volumes silently becoming plain container storage.
# Source from scripts/rebuild_full_site.sh (not executed directly).
#
# Site volumes are declared as local volumes with driver_opts type=none,o=bind,
# device=/host/path. podman-compose only applies those options when it creates
# the volume; Podman itself recreates a missing named volume with NO options
# when it starts an existing container. Postgres then initdb's a fresh cluster
# under the Podman volume path instead of using the host directory.
#
# compose_bind_volumes_preflight      before up: create missing bind volumes with
#                                     their options; refuse volumes that exist
#                                     without them.
# compose_bind_volumes_verify_mounted after up: each running container must see
#                                     the host device:inode at every bind volume
#                                     destination; offending containers and the
#                                     pipeline are stopped.
#
# Requires PODMAN, PODMAN_COMPOSE and HPCPERFSTATS_COMPOSE_PROJECT (podman_runtime.sh).

# Print "volume_name<TAB>device" for each compose volume with o=bind.
compose_bind_volume_specs() {
  local py config
  for py in python3 /usr/bin/python3; do
    if "${py}" -c 'import yaml' >/dev/null 2>&1; then
      break
    fi
    py=
  done
  if [[ -z "${py}" ]]; then
    echo "bind volume guard: no python3 with PyYAML found (podman-compose needs it too)" >&2
    return 1
  fi
  config="$("${PODMAN_COMPOSE[@]}" config 2>/dev/null)" || {
    echo "bind volume guard: podman-compose config failed" >&2
    return 1
  }
  HPCPERFSTATS_COMPOSE_PROJECT="${HPCPERFSTATS_COMPOSE_PROJECT}" "${py}" -c '
import os
import sys

import yaml

cfg = yaml.safe_load(sys.stdin) or {}
project = os.environ["HPCPERFSTATS_COMPOSE_PROJECT"]
for key, vol in sorted((cfg.get("volumes") or {}).items()):
    vol = vol or {}
    opts = vol.get("driver_opts") or {}
    if "bind" not in str(opts.get("o", "")).split(","):
        continue
    print(vol.get("name") or project + "_" + key, opts.get("device", ""), sep="\t")
' <<<"${config}"
}

compose_bind_volumes_preflight() {
  local specs name device want have_device have_o bad=0
  specs="$(compose_bind_volume_specs)" || return 1
  while IFS=$'\t' read -r name device; do
    [[ -n "${name}" ]] || continue
    if [[ -z "${device}" || ! -d "${device}" ]]; then
      echo "bind volume guard: ${name}: device '${device}' is not a directory on this host" >&2
      bad=1
      continue
    fi
    want="$(realpath "${device}")"
    if ! "${PODMAN[@]}" volume exists "${name}"; then
      echo "bind volume guard: creating ${name} -> ${device}"
      "${PODMAN[@]}" volume create --driver local \
        --opt type=none --opt o=bind --opt "device=${device}" \
        --label "io.podman.compose.project=${HPCPERFSTATS_COMPOSE_PROJECT}" \
        --label "com.docker.compose.project=${HPCPERFSTATS_COMPOSE_PROJECT}" \
        "${name}" >/dev/null || bad=1
      continue
    fi
    IFS='|' read -r have_device have_o < <(
      "${PODMAN[@]}" volume inspect "${name}" --format '{{index .Options "device"}}|{{index .Options "o"}}'
    )
    if [[ ",${have_o}," != *,bind,* || "$(realpath -m "${have_device}")" != "${want}" ]]; then
      echo "bind volume guard: ${name} exists but is NOT bound to ${device} (device='${have_device}' o='${have_o}')" >&2
      echo "  Data written through it is in container storage: $("${PODMAN[@]}" volume inspect "${name}" --format '{{.Mountpoint}}')" >&2
      echo "  Copy anything needed out of that path, remove all ${HPCPERFSTATS_COMPOSE_PROJECT} containers," >&2
      echo "  then '${PODMAN[*]} volume rm ${name}' and rerun." >&2
      bad=1
    fi
  done <<<"${specs}"
  return "${bad}"
}

compose_bind_volumes_verify_mounted() {
  local specs name device ctr dest want got
  local -A device_for=()
  local -a offenders=()
  specs="$(compose_bind_volume_specs)" || return 1
  while IFS=$'\t' read -r name device; do
    [[ -n "${name}" ]] && device_for["${name}"]="${device}"
  done <<<"${specs}"

  while IFS= read -r ctr; do
    [[ -n "${ctr}" ]] || continue
    while IFS='|' read -r name dest; do
      [[ -n "${name}" && -n "${device_for[${name}]:-}" ]] || continue
      want="$(stat -c '%d:%i' "${device_for[${name}]}" 2>/dev/null || echo missing)"
      got="$("${PODMAN[@]}" exec -u 0 "${ctr}" stat -c '%d:%i' "${dest}" 2>/dev/null || echo unreadable)"
      if [[ "${got}" != "${want}" ]]; then
        echo "bind volume guard: ${ctr}:${dest} is NOT ${device_for[${name}]} (volume ${name}; container ${got}, host ${want})" >&2
        offenders+=("${ctr}")
      else
        echo "bind volume guard: ok ${ctr}:${dest} -> ${device_for[${name}]}"
      fi
    done < <("${PODMAN[@]}" inspect "${ctr}" \
      --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}|{{.Destination}}{{"\n"}}{{end}}{{end}}')
  done < <("${PODMAN[@]}" ps \
    --filter "label=io.podman.compose.project=${HPCPERFSTATS_COMPOSE_PROJECT}" --format '{{.Names}}')

  if [[ "${#offenders[@]}" -eq 0 ]]; then
    return 0
  fi
  # The pipeline writes to the db and archive; never leave it running beside a bad mount.
  offenders+=("${HPCPERFSTATS_COMPOSE_PROJECT}_pipeline_1")
  mapfile -t offenders < <(printf '%s\n' "${offenders[@]}" | sort -u)
  echo "bind volume guard: stopping ${offenders[*]}" >&2
  "${PODMAN[@]}" stop "${offenders[@]}" >/dev/null 2>&1 || true
  return 1
}
