#!/usr/bin/env bash
# Post-debug-rpmbuild: install main RPM (--replacepkgs), emit capabilities, validate /dev/shm.
#
# Run from HPCPerfStats/monitor/ after debug rpmbuild (hpc_debug_build 1).
# RPM_TOPDIR and DIST_TOP default from this checkout when unset.
#
# Optional: ENABLE_SLOW_TIER, HPCPERFSTATS_DEBUG_SHM_DIR, WORKSPACE_ROOT,
#           SKIP_INSTALL=1, SKIP_SHM_LS=1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITOR_DIR="${MONITOR_DIR:-${SCRIPT_DIR}/..}"

monitor_spec_field() {
  local field="$1"
  local file="$2"
  grep -E "^${field}:" "${file}" | head -1 | sed 's/^[^:]*:[[:space:]]*//;s/[[:space:]]*$//'
}

resolve_dist_top() {
  local monitor_dir="$1"
  local spec="${monitor_dir}/hpcperfstats.spec"
  local ver tarbase
  ver="$(monitor_spec_field Version "${spec}")"
  tarbase="$(sed -n 's/^AC_INIT(\[\([^]]*\)\].*/\1/p' "${monitor_dir}/configure.ac" | head -1)"
  if test -z "${ver}" || test -z "${tarbase}"; then
    echo "ERROR: could not read Version from ${spec} or AC_INIT from configure.ac" >&2
    return 1
  fi
  printf '%s-%s' "${tarbase}" "${ver}"
}

resolve_workspace_root() {
  local monitor_dir="$1"
  local d="${monitor_dir}"
  local i=0
  while test "${i}" -lt 5; do
    if test -x "${d}/.venv/bin/python3"; then
      printf '%s' "${d}"
      return 0
    fi
    d="$(cd "${d}/.." && pwd)"
    i=$((i + 1))
  done
  printf '%s' "$(cd "${monitor_dir}/.." && pwd)"
}

resolve_python() {
  local ws="$1"
  if test -x "${ws}/.venv/bin/python3"; then
    printf '%s' "${ws}/.venv/bin/python3"
  else
    command -v python3
  fi
}

find_main_daemon_rpm() {
  local rpm_topdir="$1"
  local rpm
  shopt -s nullglob
  for rpm in "${rpm_topdir}"/RPMS/*/hpcperfstatsd-[0-9]*.rpm; do
    printf '%s' "${rpm}"
    return 0
  done
  return 1
}

install_main_daemon_rpm() {
  local rpm_topdir="$1"
  local main_rpm
  main_rpm="$(find_main_daemon_rpm "${rpm_topdir}")" || {
    echo "ERROR: main hpcperfstatsd RPM not found under ${rpm_topdir}/RPMS" >&2
    return 1
  }
  echo "Installing ${main_rpm##*/} (--replacepkgs overwrites same-version installs) ..."
  sudo rpm -Uvh --replacepkgs "${main_rpm}"
}

usage() {
  cat <<EOF
Usage: $(basename "$0")

Run from HPCPerfStats/monitor/ after debug rpmbuild completes.
RPM %post starts hpcperfstats.service on install/upgrade (see hpcperfstats.spec).
Optional: SKIP_INSTALL=1 to re-validate without reinstall.
EOF
}

main() {
  if test "${1:-}" = "-h" || test "${1:-}" = "--help"; then
    usage
    return 0
  fi

  local monitor_dir rpm_topdir dist_top ws py build_src build_static caps shm_dir tier enable_slow
  local slug expectations report_dir

  monitor_dir="$(cd "${MONITOR_DIR}" && pwd)"
  rpm_topdir="${RPM_TOPDIR:-${monitor_dir}/rpmbuild}"
  dist_top="${DIST_TOP:-$(resolve_dist_top "${monitor_dir}")}"
  ws="${WORKSPACE_ROOT:-$(resolve_workspace_root "${monitor_dir}")}"
  py="$(resolve_python "${ws}")"

  build_src="${rpm_topdir}/BUILD/${dist_top}"
  build_static="${build_src}/.build-static"
  caps="${build_static}/monitor-build-capabilities.json"
  shm_dir="${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}"
  enable_slow="${ENABLE_SLOW_TIER:-1}"
  case "${enable_slow}" in
  0 | false | FALSE | no | NO | off | OFF) tier="slowtier0" ;;
  *) tier="slowtier1" ;;
  esac

  if test ! -d "${build_static}"; then
    echo "ERROR: RPM build tree missing: ${build_static}" >&2
    echo "Run debug rpmbuild first (prepare_rpmbuild_dirs.sh --debug-build)." >&2
    return 1
  fi
  if test ! -f "${build_static}/src/hpcperfstatsd"; then
    echo "ERROR: debug daemon not built: ${build_static}/src/hpcperfstatsd" >&2
    return 1
  fi

  if test "${SKIP_INSTALL:-0}" != "1"; then
    install_main_daemon_rpm "${rpm_topdir}"
    echo "RPM %post enables and starts hpcperfstats.service (no extra restart here)."
  fi

  echo "Emitting ${caps} ..."
  CAPABILITIES_TIER="${tier}" "${py}" "${monitor_dir}/scripts/emit_build_capabilities.py" \
    --build-dir "${build_static}" \
    --tier "${tier}"
  test -f "${caps}" || {
    echo "ERROR: failed to write ${caps}" >&2
    return 1
  }

  slug="$("${py}" -c "import json; print(json.load(open('${caps}'))['capability_slug'])")"
  echo "capability_slug=${slug}"

  if test "${SKIP_SHM_LS:-0}" != "1"; then
    ls -la "${shm_dir}"/{schema,fast,full}
  fi

  expectations="${build_static}/expectations_${slug}.json"
  report_dir="${ws}/test_runs/monitor"
  mkdir -p "${report_dir}"

  echo "Building expectations ..."
  "${py}" "${monitor_dir}/scripts/build_message_expectations.py" \
    --capabilities "${caps}" \
    --shm-dir "${shm_dir}" \
    --enable-slow-tier "${enable_slow}" \
    --out "${expectations}"

  echo "Validating /dev/shm payloads ..."
  "${py}" "${monitor_dir}/scripts/validate_shm_messages.py" \
    --capabilities "${caps}" \
    --manifest "${expectations}" \
    --shm-dir "${shm_dir}" \
    --report "${report_dir}/validate_rpm_debug_${slug}_$(date +%F).txt"

  echo "PASS: validate_shm_messages (slug=${slug})"
}

if test "$(basename "$0")" = "rpm_debug_shm_verify.sh"; then
  main "$@"
fi
