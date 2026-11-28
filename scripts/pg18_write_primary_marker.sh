#!/usr/bin/env bash
# PG18 cutover gate for legacy sites (PG15 `db` + `db_pg18` copy target).
# Runbook: docs/how-to/operate/migrate-to-postgresql-18.md.
#
# Modes (one at a time; --dry-run combines with any and writes nothing):
#   (default)         write <pg18 bind>/hpcperfstats-primary and
#                     <pg15 bind>/hpcperfstats-retired, remove the
#                     <pg18 bind>/hpcperfstats-migrate-target sentinel.
#   --rollback        undo that before the pipeline ever ran on PG18: remove the
#                     marker, the retired file and the four stamps, restore the
#                     sentinel.
#   --stamp-readonly  PG15 `db` and `db_pg18` running, PG15 read-only: write the
#                     pg15_readonly_ok stamp with the PG15 write counters.
#   --check-writes    web/proxy/pipeline stopped, PG15 `db` and `db_pg18`
#                     running: write pg15_writes_ok when the PG15 write counters
#                     still equal the pg15_readonly_ok value.
#
# The default mode refuses unless every one of these holds:
#   - web, proxy, pipeline exist, are stopped, and stopped no later than
#     relational_copy_ok (a zero FinishedAt refuses);
#   - PG15 `db` exists, is stopped, and its last start predates pg15_readonly_ok,
#     so read-only was in force for its whole last run;
#   - `db_pg18` is stopped and neither bind's .hpc-server.lock is held;
#   - all four stamps exist, name themselves, carry the system identifiers read
#     from the stopped clusters' global/pg_control and one PG18 database OID
#     whose base/<oid> exists;
#   - pg15_readonly_ok is older than each other stamp, and pg15_writes_ok
#     recorded the same PG15 write counters as pg15_readonly_ok.
# --rollback refuses unless `db` and `web` are stopped, the pipeline container
# exists, is stopped, and last started before the marker's mtime.
# Every refusal is listed, nothing is changed (except that --check-writes first
# drops an earlier pg15_writes_ok), and the exit status is 1.
#
# Host file access goes through `podman unshare` when Podman is rootless.
# Container start/finish times are rounded up to whole seconds before being
# compared with the whole-second stamp times.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
TAG="pg18_write_primary_marker"
STAMPS=(pg15_readonly_ok verify_ok.json relational_copy_ok pg15_writes_ok)
CTR_ROOT=/var/lib/postgresql
PSQL_ARGS=(psql -h localhost -U hpcperfstats -d hpcperfstats -XtA -F '|' -c)
PG15_SQL="SELECT s.setting, coalesce(s.sourcefile, ''), \
(SELECT system_identifier FROM pg_control_system()), \
(SELECT sum(tup_inserted + tup_updated + tup_deleted) FROM pg_stat_database) \
FROM pg_settings s WHERE s.name = 'default_transaction_read_only'"
PG18_SQL="SELECT (SELECT system_identifier FROM pg_control_system()), \
(SELECT oid FROM pg_database WHERE datname = 'hpcperfstats')"
INSPECT_FMT='{{.State.Running}}|{{.State.StartedAt.IsZero}}|{{.State.StartedAt.Unix}}|'
INSPECT_FMT+='{{.State.StartedAt.Nanosecond}}|{{.State.FinishedAt.IsZero}}|'
INSPECT_FMT+='{{.State.FinishedAt.Unix}}|{{.State.FinishedAt.Nanosecond}}'

usage() {
  printf 'Usage: %s [--dry-run] [--rollback | --stamp-readonly | --check-writes]\n' \
    "scripts/pg18_write_primary_marker.sh"
  printf '  (no mode)         write the PG18 primary marker and PG15 retired file\n'
  printf '  --rollback        undo the marker before the pipeline ran on PG18\n'
  printf '  --stamp-readonly  record pg15_readonly_ok (PG15 read-only + counters)\n'
  printf '  --check-writes    record pg15_writes_ok (PG15 counters unchanged)\n'
  printf '  --dry-run         run every check, report what would change, write nothing\n'
  printf 'Project: HPCPERFSTATS_COMPOSE_PROJECT (default hpcperfstats).\n'
}

usage_error() {
  printf '%s: %s\n' "${TAG}" "$1" >&2
  usage >&2
  exit 2
}

mode=mark
dry_run=0
while (($#)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --rollback | --stamp-readonly | --check-writes)
      [[ "${mode}" == mark ]] || usage_error "pick one mode"
      mode="${1#--}"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) usage_error "unknown argument: $1" ;;
  esac
  shift
done

# shellcheck source=lib/podman_runtime.sh
source "${SCRIPT_DIR}/lib/podman_runtime.sh"
# shellcheck source=lib/compose_bind_volume_guard.sh
source "${SCRIPT_DIR}/lib/compose_bind_volume_guard.sh"
podman_runtime_require
cd "${REPO_ROOT}"

project="${HPCPERFSTATS_COMPOSE_PROJECT}"
CTR_DB="${project}_db_1"
CTR_PG18="${project}_db_pg18_1"
CTR_WEB="${project}_web_1"
CTR_PROXY="${project}_proxy_1"
CTR_PIPELINE="${project}_pipeline_1"

REFUSALS=()
refuse() {
  REFUSALS+=("$*")
}

report_refusals() {
  local reason
  ((${#REFUSALS[@]})) || return 0
  for reason in "${REFUSALS[@]}"; do
    printf '%s: refusing: %s\n' "${TAG}" "${reason}" >&2
  done
  printf '%s: nothing was changed\n' "${TAG}" >&2
  exit 1
}

ROOTLESS=false
host_run() {
  if [[ "${ROOTLESS}" == true ]]; then
    "${PODMAN[@]}" unshare "$@"
  else
    "$@"
  fi
}

is_int() {
  [[ "$1" =~ ^-?[0-9]+$ ]]
}

declare -A C_EXISTS=() C_RUNNING=() C_START=() C_FINISH=()
# Ceil of a podman time to whole seconds; empty for Go's zero time.
ceil_seconds() {
  local zero=$1 unix=$2 ns=$3
  if [[ "${zero}" == true ]]; then
    printf ''
  else
    printf '%s' "$((unix + (ns > 0 ? 1 : 0)))"
  fi
}

load_container() {
  local name=$1 out running szero sunix sns fzero funix fns
  if ! out="$("${PODMAN[@]}" inspect --format "${INSPECT_FMT}" "${name}" 2>/dev/null)"; then
    C_EXISTS["${name}"]=0
    return 0
  fi
  IFS='|' read -r running szero sunix sns fzero funix fns < <(printf '%s\n' "${out}")
  for value in "${sunix}" "${sns}" "${funix}" "${fns}"; do
    is_int "${value}" || {
      refuse "${name}: unreadable podman inspect output '${out}'"
      C_EXISTS["${name}"]=0
      return 0
    }
  done
  C_EXISTS["${name}"]=1
  C_RUNNING["${name}"]="${running}"
  C_START["${name}"]="$(ceil_seconds "${szero}" "${sunix}" "${sns}")"
  C_FINISH["${name}"]="$(ceil_seconds "${fzero}" "${funix}" "${fns}")"
}

# require_stopped NAME MUST_EXIST: 0 when it exists and is stopped.
require_stopped() {
  local name=$1 must_exist=$2
  load_container "${name}"
  if [[ "${C_EXISTS[${name}]}" != 1 ]]; then
    [[ "${must_exist}" == 1 ]] && refuse "${name} does not exist"
    return 1
  fi
  if [[ "${C_RUNNING[${name}]}" == true ]]; then
    refuse "${name} is running (stop it first)"
  fi
}

require_running() {
  local name=$1
  load_container "${name}"
  if [[ "${C_EXISTS[${name}]}" != 1 || "${C_RUNNING[${name}]}" != true ]]; then
    refuse "${name} is not running"
  fi
}

declare -A DEVICE=()
resolve_devices() {
  local specs name device key dev
  specs="$(compose_bind_volume_specs)" || {
    refuse "cannot read bind volume devices from podman-compose config"
    report_refusals
  }
  while IFS=$'\t' read -r name device; do
    [[ -n "${name}" ]] && DEVICE["${name}"]="${device}"
  done < <(printf '%s\n' "${specs}")
  for key in postgres_data postgres_data_pg18 hpcperfstatsdata; do
    dev="${DEVICE[${project}_${key}]:-${DEVICE[${key}]:-}}"
    if [[ -z "${dev}" ]]; then
      refuse "no o=bind device for volume ${key} in podman-compose config"
    elif ! host_run test -d "${dev}"; then
      refuse "volume ${key} device ${dev} is not a directory"
    fi
    DEVICE["${key}"]="${dev}"
  done
  report_refusals
  PG15_BIND="${DEVICE[postgres_data]}"
  PG18_BIND="${DEVICE[postgres_data_pg18]}"
  DATA_BIND="${DEVICE[hpcperfstatsdata]}"
  PG18_PGDATA="${PG18_BIND}/18/docker"
  MARKER="${PG18_BIND}/hpcperfstats-primary"
  SENTINEL="${PG18_BIND}/hpcperfstats-migrate-target"
  RETIRED="${PG15_BIND}/hpcperfstats-retired"
}

host_stamp_path() {
  if [[ "$1" == verify_ok.json ]]; then
    printf '%s/pg18_migrate/%s' "${DATA_BIND}" "$1"
  else
    printf '%s/%s' "${PG18_BIND}" "$1"
  fi
}

# Prints "epoch<TAB>pg18_id<TAB>oid<TAB>pg15_id<TAB>pg15_writes" for stamp $1
# read from stdin, or a reason on stderr and exit 1.
parse_stamp() {
  python3 -c '
import json, sys

name = sys.argv[1]
try:
  doc = json.load(sys.stdin)
except ValueError as exc:
  sys.exit(f"not JSON ({exc})")
if not isinstance(doc, dict):
  sys.exit("not a JSON object")
got = doc.get("stamp")
if got != name:
  sys.exit(f"stamp name is {got!r}, expected {name!r}")


def integer(key, required=True):
  value = doc.get(key)
  if value is None and not required:
    return ""
  if isinstance(value, bool) or not isinstance(value, int):
    sys.exit(f"{key} is not an integer")
  return str(value)


def ident(key):
  value = doc.get(key)
  if not isinstance(value, str) or not value.lstrip("-").isdigit():
    sys.exit(f"{key} is not a decimal string")
  return value


print(integer("written_at_epoch"), ident("pg18_system_identifier"),
      integer("pg18_database_oid"), ident("pg15_system_identifier"),
      integer("pg15_writes", required=False), sep="\t")
' "$1"
}

declare -A S_EPOCH=() S_PG18=() S_OID=() S_PG15=() S_WRITES=()
# load_stamp NAME READER... : READER prints the stamp, or fails when absent.
load_stamp() {
  local name=$1 content parsed
  shift
  if ! content="$("$@" 2>/dev/null)"; then
    refuse "missing stamp ${name} (${*: -1})"
    return 1
  fi
  if ! parsed="$(printf '%s' "${content}" | parse_stamp "${name}" 2>&1)"; then
    refuse "${name}: ${parsed}"
    return 1
  fi
  IFS=$'\t' read -r S_EPOCH["${name}"] S_PG18["${name}"] S_OID["${name}"] \
    S_PG15["${name}"] S_WRITES["${name}"] < <(printf '%s\n' "${parsed}")
}

# Stamp readers for load_stamp (called through "$@").
# shellcheck disable=SC2317,SC2329
host_cat() {
  host_run test -f "$1" && host_run cat "$1"
}

# shellcheck disable=SC2317
pg18_cat() {
  "${PODMAN[@]}" exec "${CTR_PG18}" test -f "$1" \
    && "${PODMAN[@]}" exec "${CTR_PG18}" cat "$1"
}

# check_identity NAME PG18_ID PG15_ID [OID]
check_identity() {
  local name=$1
  [[ -n "${S_EPOCH[${name}]:-}" ]] || return 0
  if [[ "${S_PG18[${name}]}" != "$2" ]]; then
    refuse "${name}: pg18_system_identifier ${S_PG18[${name}]} is not this PG18 cluster ($2)"
  fi
  if [[ "${S_PG15[${name}]}" != "$3" ]]; then
    refuse "${name}: pg15_system_identifier ${S_PG15[${name}]} is not this PG15 cluster ($3)"
  fi
  if [[ -n "${4:-}" && "${S_OID[${name}]}" != "$4" ]]; then
    refuse "${name}: pg18_database_oid ${S_OID[${name}]} is not the current database ($4)"
  fi
}

# check_writes_equal NOW: NOW against the pg15_readonly_ok counters.
check_writes_equal() {
  local then="${S_WRITES[pg15_readonly_ok]:-}"
  if ! is_int "${then}"; then
    refuse "pg15_readonly_ok: pg15_writes missing"
  elif [[ "$1" != "${then}" ]]; then
    refuse "PG15 write counters moved: pg15_readonly_ok recorded ${then}, now $1" \
      "(something wrote PG15 after it went read-only; redo the freeze and copies)"
  fi
}

# Prints the signed system_identifier (first 8 bytes of pg_control), or
# nothing when the file is unreadable.
read_pg_control() {
  local out
  out="$(host_run od -An -t d8 -N8 "$1" 2>/dev/null)" || out=
  out="${out//[[:space:]]/}"
  if is_int "${out}"; then
    printf '%s' "${out}"
  fi
}

check_pg_version() {
  local file=$1 want=$2 got
  got="$(host_run cat "${file}" 2>/dev/null)" || got=missing
  [[ "${got}" == "${want}" ]] || refuse "${file}: PG_VERSION is '${got}', expected ${want}"
}

check_lock_free() {
  if host_run test -e "$1" && ! host_run flock -n "$1" true; then
    refuse "lock is held: $1 (a server still runs on this bind)"
  fi
}

check_older() {
  local first=$1 second=$2
  [[ -n "${S_EPOCH[${first}]:-}" && -n "${S_EPOCH[${second}]:-}" ]] || return 0
  if ((S_EPOCH[${first}] >= S_EPOCH[${second}])); then
    refuse "stamp order: ${first} must be older than ${second}" \
      "(${S_EPOCH[${first}]} >= ${S_EPOCH[${second}]})"
  fi
}

NOW_EPOCH="$(date -u +%s)"
NOW_ISO="$(date -u -d "@${NOW_EPOCH}" +%Y-%m-%dT%H:%M:%SZ)"
# stamp_json NAME PG18_ID OID PG15_ID [PG15_WRITES]
stamp_json() {
  local doc
  doc="{\"stamp\": \"$1\", \"written_at\": \"${NOW_ISO}\", "
  doc+="\"written_at_epoch\": ${NOW_EPOCH}, \"pg18_system_identifier\": \"$2\", "
  doc+="\"pg18_database_oid\": $3, \"pg15_system_identifier\": \"$4\""
  [[ -n "${5:-}" ]] && doc+=", \"pg15_writes\": $5"
  printf '%s}' "${doc}"
}

# write_file RUNNER... PATH CONTENT: tmp + mv through the given runner.
write_file() {
  local content="${*: -1}" path="${*: -2:1}"
  if ((dry_run)); then
    printf 'would write %s: %s\n' "${path}" "${content}"
    return 0
  fi
  # shellcheck disable=SC2016  # $1/$2 expand in the inner sh
  "${@:1:$#-2}" sh -c 'umask 022 && printf "%s\n" "$2" > "$1.tmp" && mv -f "$1.tmp" "$1"' \
    sh "${path}" "${content}"
  printf 'wrote %s\n' "${path}"
}

remove_host_file() {
  if ((dry_run)); then
    printf 'would remove %s\n' "$1"
  else
    host_run rm -f -- "$1"
    printf 'removed %s\n' "$1"
  fi
}

# PG15 settings row: sets PG15_ID and PG15_WRITES; refuses unless read-only
# is on and persisted by ALTER SYSTEM (postgresql.auto.conf).
read_pg15() {
  local row setting source
  row="$("${PODMAN[@]}" exec "${CTR_DB}" "${PSQL_ARGS[@]}" "${PG15_SQL}" 2>/dev/null)" || {
    refuse "cannot query PG15 in ${CTR_DB}"
    return 1
  }
  IFS='|' read -r setting source PG15_ID PG15_WRITES < <(printf '%s\n' "${row}")
  if [[ "${setting}" != on ]]; then
    refuse "PG15 default_transaction_read_only is ${setting:-unset}" \
      "(ALTER SYSTEM SET default_transaction_read_only = on; SELECT pg_reload_conf();)"
  elif [[ "${source}" != */postgresql.auto.conf ]]; then
    refuse "PG15 default_transaction_read_only is not set in postgresql.auto.conf" \
      "(source '${source}'); set it with ALTER SYSTEM so it survives a restart"
  fi
  is_int "${PG15_ID}" || refuse "cannot read PG15 system_identifier ('${PG15_ID}')"
  is_int "${PG15_WRITES}" || refuse "cannot read PG15 pg15_writes ('${PG15_WRITES}')"
}

read_pg18() {
  local row
  row="$("${PODMAN[@]}" exec "${CTR_PG18}" "${PSQL_ARGS[@]}" "${PG18_SQL}" 2>/dev/null)" || {
    refuse "cannot query PG18 in ${CTR_PG18}"
    return 1
  }
  IFS='|' read -r PG18_ID PG18_OID < <(printf '%s\n' "${row}")
  is_int "${PG18_ID}" || refuse "cannot read PG18 system_identifier ('${PG18_ID}')"
  is_int "${PG18_OID}" || refuse "cannot read PG18 hpcperfstats database oid ('${PG18_OID}')"
}

write_pg18_stamp() {
  local name=$1 writes=$2
  write_file "${PODMAN[@]}" exec "${CTR_PG18}" "${CTR_ROOT}/${name}" \
    "$(stamp_json "${name}" "${PG18_ID}" "${PG18_OID}" "${PG15_ID}" "${writes}")"
}

run_stamp_readonly() {
  require_running "${CTR_DB}"
  require_running "${CTR_PG18}"
  report_refusals
  read_pg15 || true
  read_pg18 || true
  report_refusals
  write_pg18_stamp pg15_readonly_ok "${PG15_WRITES}"
}

run_check_writes() {
  local name
  for name in "${CTR_WEB}" "${CTR_PROXY}" "${CTR_PIPELINE}"; do
    require_stopped "${name}" 0 || true
  done
  require_running "${CTR_DB}"
  require_running "${CTR_PG18}"
  report_refusals
  # A stamp from an earlier run proves nothing about writes since then: drop it
  # first, so a refusal below leaves no pg15_writes_ok for the marker to accept.
  if ((!dry_run)) && pg18_cat "${CTR_ROOT}/pg15_writes_ok" >/dev/null 2>&1; then
    "${PODMAN[@]}" exec "${CTR_PG18}" rm -f "${CTR_ROOT}/pg15_writes_ok"
    printf '%s: removed the earlier pg15_writes_ok\n' "${TAG}" >&2
  fi
  load_stamp pg15_readonly_ok pg18_cat "${CTR_ROOT}/pg15_readonly_ok" || true
  read_pg15 || true
  read_pg18 || true
  report_refusals
  check_identity pg15_readonly_ok "${PG18_ID}" "${PG15_ID}" "${PG18_OID}"
  check_writes_equal "${PG15_WRITES}"
  report_refusals
  write_pg18_stamp pg15_writes_ok "${PG15_WRITES}"
}

run_mark() {
  local name pg18_id pg15_id oid
  resolve_devices
  for name in "${CTR_WEB}" "${CTR_PROXY}" "${CTR_PIPELINE}" "${CTR_DB}"; do
    require_stopped "${name}" 1 || true
  done
  require_stopped "${CTR_PG18}" 0 || true
  check_lock_free "${PG18_BIND}/.hpc-server.lock"
  check_lock_free "${PG15_BIND}/.hpc-server.lock"
  check_pg_version "${PG18_PGDATA}/PG_VERSION" 18
  check_pg_version "${PG15_BIND}/PG_VERSION" 15
  pg18_id="$(read_pg_control "${PG18_PGDATA}/global/pg_control")"
  pg15_id="$(read_pg_control "${PG15_BIND}/global/pg_control")"
  [[ -n "${pg18_id}" ]] || refuse "cannot read ${PG18_PGDATA}/global/pg_control"
  [[ -n "${pg15_id}" ]] || refuse "cannot read ${PG15_BIND}/global/pg_control"
  for name in "${STAMPS[@]}"; do
    load_stamp "${name}" host_cat "$(host_stamp_path "${name}")" || true
    check_identity "${name}" "${pg18_id}" "${pg15_id}"
  done
  oid="${S_OID[pg15_readonly_ok]:-}"
  for name in "${STAMPS[@]}"; do
    if [[ -n "${S_OID[${name}]:-}" && "${S_OID[${name}]}" != "${oid}" ]]; then
      refuse "${name}: pg18_database_oid ${S_OID[${name}]} differs from" \
        "pg15_readonly_ok (${oid:-missing})"
    fi
  done
  if [[ -n "${oid}" ]] && ! host_run test -d "${PG18_PGDATA}/base/${oid}"; then
    refuse "PG18 database directory base/${oid} missing under ${PG18_PGDATA}"
  fi
  for name in verify_ok.json relational_copy_ok pg15_writes_ok; do
    check_older pg15_readonly_ok "${name}"
  done
  [[ -n "${S_EPOCH[pg15_writes_ok]:-}" ]] && check_writes_equal "${S_WRITES[pg15_writes_ok]}"
  for name in "${CTR_WEB}" "${CTR_PROXY}" "${CTR_PIPELINE}"; do
    [[ "${C_EXISTS[${name}]}" == 1 && -n "${S_EPOCH[relational_copy_ok]:-}" ]] || continue
    if [[ -z "${C_FINISH[${name}]}" ]]; then
      refuse "${name} FinishedAt is zero (it never stopped after a run)"
    elif ((C_FINISH[${name}] > S_EPOCH[relational_copy_ok])); then
      refuse "${name} FinishedAt (${C_FINISH[${name}]}) is after relational_copy_ok" \
        "(${S_EPOCH[relational_copy_ok]}): it ran after the relational copy; redo it"
    fi
  done
  if [[ "${C_EXISTS[${CTR_DB}]}" == 1 && -n "${S_EPOCH[pg15_readonly_ok]:-}" ]]; then
    if [[ -z "${C_START[${CTR_DB}]}" ]] \
      || ((C_START[${CTR_DB}] >= S_EPOCH[pg15_readonly_ok])); then
      refuse "${CTR_DB} StartedAt (${C_START[${CTR_DB}]:-zero}) is not before" \
        "pg15_readonly_ok (${S_EPOCH[pg15_readonly_ok]}): PG15 restarted; redo the freeze"
    fi
  fi
  report_refusals
  write_file host_run "${MARKER}" \
    "$(stamp_json hpcperfstats-primary "${pg18_id}" "${oid}" "${pg15_id}")"
  write_file host_run "${RETIRED}" \
    "$(stamp_json hpcperfstats-retired "${pg18_id}" "${oid}" "${pg15_id}")"
  remove_host_file "${SENTINEL}"
  ((dry_run)) || printf 'PG18 bind %s is now the primary; PG15 bind %s is retired\n' \
    "${PG18_BIND}" "${PG15_BIND}"
}

run_rollback() {
  local marker_mtime name
  resolve_devices
  require_stopped "${CTR_DB}" 0 || true
  require_stopped "${CTR_WEB}" 0 || true
  if ! marker_mtime="$(host_run stat -c %Y "${MARKER}" 2>/dev/null)"; then
    marker_mtime=
    refuse "no marker at ${MARKER} (nothing to roll back)"
  fi
  if require_stopped "${CTR_PIPELINE}" 1 && [[ -n "${marker_mtime}" ]]; then
    local started="${C_START[${CTR_PIPELINE}]}"
    if [[ -n "${started}" ]] && ((started >= marker_mtime)); then
      refuse "${CTR_PIPELINE} StartedAt (${started}) is not before the marker" \
        "(mtime ${marker_mtime}): the pipeline ran on PG18; no rollback, fix forward"
    fi
  fi
  report_refusals
  write_file host_run "${SENTINEL}" migrate-target
  for name in "${STAMPS[@]}"; do
    remove_host_file "$(host_stamp_path "${name}")"
  done
  remove_host_file "${RETIRED}"
  remove_host_file "${MARKER}"
}

if [[ "${mode}" == mark || "${mode}" == rollback ]]; then
  ROOTLESS="$("${PODMAN[@]}" info --format '{{.Host.Security.Rootless}}')" || {
    refuse "podman info failed"
    report_refusals
  }
fi
case "${mode}" in
  stamp-readonly) run_stamp_readonly ;;
  check-writes) run_check_writes ;;
  rollback) run_rollback ;;
  *) run_mark ;;
esac
((dry_run)) && printf 'dry run: nothing was written\n'
exit 0
