#!/usr/bin/env bash
# Post-debug-rpmbuild: install main RPM (--replacepkgs --replacefiles), resolve capabilities,
# wait for a full payload, validate /dev/shm. RPM_BUILD=1 runs the debug rpmbuild first.
#
# Run from HPCPerfStats/monitor/ after debug rpmbuild (hpc_debug_build 1).
# RPM_TOPDIR and DIST_TOP default from this checkout when unset.
#
# Capabilities: prefer %{_topdir}/debug-verify/monitor-build-capabilities.json (stashed
# during debug %install; survives EL10 rmbuild). Fall back to emitting into
# BUILD/.../.build-static when that tree still exists (--noclean / older rpm).
#
# Optional: ENABLE_SLOW_TIER, HPCPERFSTATS_DEBUG_SHM_DIR, WORKSPACE_ROOT,
#           SKIP_INSTALL=1, SKIP_SHM_LS=1, FAST (default 30), FULL (default 60),
#           POST_INSTALL_SLEEP_SECONDS (defaults to FULL; soak via stress-ng --cpu 0),
#           WAIT_SHM_SECONDS, WAIT_FULL_SECONDS (default 900; 0 = no wait for full rows),
#           RPM_BUILD=1 (remove stale daemon RPMs and run the debug rpmbuild first),
#           CROSS_SAMPLE_CHECK (default 1; set 0 to disable),
#           STRICT_LIVE_SPOT_CHECK / STRICT_PLAUSIBILITY / STRICT_CROSS_SAMPLE
#             (default 1; set 0 to disable),
#           STRICT_SENTINELS / SENTINEL_PROBE_CHECK / INDEPENDENT_READ_CHECK /
#             CONSUMER_PARSE_CHECK (value sentinel layers; default 1; set 0 to disable),
#           FAULT_INJECT (default 1; 0 = SKIP): after validation, restart the DEBUG daemon with
#             debug_fault_inject for FAULT_INJECT_TICKS cycles and check codes, recovery,
#             bridges and site coverage (scripts/fault_inject_verify.sh; release RPMs SKIP),
#           GOLDEN_DIR=/path|auto or GOLDEN_CHECK=1 (opt-in golden diff),
#           HPCPERFSTATS_CONF=/path/to/hpcperfstats.conf
set -euo pipefail

# Debug verify cadence: matches hpcperfstats.conf when built with hpc_debug_build 1.
readonly FAST="${FAST:-30}"
readonly FULL="${FULL:-60}"

env_flag_on() {
  # Default ON unless explicitly 0/false/no/off.
  case "${1:-1}" in
  0 | false | FALSE | no | NO | off | OFF) return 1 ;;
  *) return 0 ;;
  esac
}

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

# The verify scripts need python >= 3.7 (EL8's python3 is 3.6): repo venv, then the newest
# python3.N on PATH, then plain python3.
resolve_python() {
  local ws="$1"
  local v
  if test -x "${ws}/.venv/bin/python3"; then
    printf '%s' "${ws}/.venv/bin/python3"
    return 0
  fi
  for v in python3.12 python3.11 python3.10 python3.9 python3.8; do
    command -v "${v}" && return 0
  done
  command -v python3
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
  echo "Installing ${main_rpm##*/} (--replacepkgs --replacefiles: same-version rebuilds) ..."
  sudo rpm -Uvh --replacepkgs --replacefiles "${main_rpm}"
}

# RPM_BUILD=1: remove stale daemon RPMs, then run the debug rpmbuild, so a failed build can
# never install an older package and the node needs no hand-pasted rpmbuild line.
build_debug_rpm() {
  local rpm_topdir="$1"
  local spec="${rpm_topdir}/SPECS/hpcperfstats.spec"

  test -f "${spec}" || {
    echo "ERROR: ${spec} missing; run prepare_rpmbuild_*.sh --debug-build first" >&2
    return 1
  }
  rm -f "${rpm_topdir}"/RPMS/*/hpcperfstatsd-*.rpm
  echo "Building the debug RPM (rpmbuild -ba, hpc_debug_build 1) ..."
  rpmbuild -ba --define "_topdir ${rpm_topdir}" --define "hpc_debug_build 1" "${spec}" \
    </dev/null || {
    echo "ERROR: debug rpmbuild failed" >&2
    return 1
  }
}

# Waits up to <seconds> for a full payload with at least one metric row: the shm files exist
# right after the restart, but a slow-tier daemon writes its first full sample later.
wait_full_payload() {
  local shm_dir="$1"
  local seconds="$2"
  local deadline

  test "${seconds}" -gt 0 2>/dev/null || return 0
  deadline=$(($(date +%s) + seconds))
  echo "Waiting up to ${seconds}s for a full payload with metric rows ..."
  until grep -qE '^[a-z][a-z0-9_]* ' "${shm_dir}/full" 2>/dev/null; do
    if test "$(date +%s)" -ge "${deadline}"; then
      echo "ERROR: no metric rows in ${shm_dir}/full after ${seconds}s" >&2
      return 1
    fi
    sleep "${WAIT_FULL_POLL_SECONDS:-15}"
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0")

Run from HPCPerfStats/monitor/ after debug rpmbuild completes.
RPM %post starts hpcperfstats.service on install/upgrade (see hpcperfstats.spec).
After install, runs stress-ng --cpu 0 for POST_INSTALL_SLEEP_SECONDS (default FULL=60)
before validation (installs stress-ng via dnf/yum if missing).
Debug RPM sets sample_freq=${FAST} and sample_freq_slow=${FULL} in hpcperfstats.conf.
Capabilities come from rpmbuild/debug-verify/ (stashed at %install; survives EL10 rmbuild).
Defaults: cross-sample + strict plausibility/live-spot/cross-sample (set *=0 to disable).
Golden diff is opt-in: GOLDEN_DIR=/path|auto or GOLDEN_CHECK=1.
Optional: SKIP_INSTALL=1 to re-validate without reinstall or post-install wait.
Optional: RPM_BUILD=1 to remove stale daemon RPMs and run the debug rpmbuild first.
Waits up to WAIT_FULL_SECONDS (default 900) for a full payload with metric rows.
EOF
}

ensure_capabilities_json() {
  # Args: monitor_dir rpm_topdir dist_top py tier
  # Echoes absolute path to monitor-build-capabilities.json on stdout.
  local monitor_dir="$1"
  local rpm_topdir="$2"
  local dist_top="$3"
  local py="$4"
  local tier="$5"
  local stash_dir stash_caps build_static_path

  stash_dir="${rpm_topdir}/debug-verify"
  stash_caps="${stash_dir}/monitor-build-capabilities.json"
  build_static_path="${rpm_topdir}/BUILD/${dist_top}/.build-static"
  mkdir -p "${stash_dir}"

  if test -f "${stash_caps}"; then
    echo "Using stashed capabilities: ${stash_caps}" >&2
    printf '%s' "${stash_caps}"
    return 0
  fi

  if test -d "${build_static_path}"; then
    echo "Stash missing; emitting capabilities from ${build_static_path} ..." >&2
    CAPABILITIES_TIER="${tier}" "${py}" "${monitor_dir}/scripts/emit_build_capabilities.py" \
      --build-dir "${build_static_path}" \
      --tier "${tier}"
    if test ! -f "${build_static_path}/monitor-build-capabilities.json"; then
      echo "ERROR: failed to emit ${build_static_path}/monitor-build-capabilities.json" >&2
      return 1
    fi
    cp -f "${build_static_path}/monitor-build-capabilities.json" "${stash_caps}"
    echo "Wrote stash ${stash_caps} (from BUILD tree)" >&2
    printf '%s' "${stash_caps}"
    return 0
  fi

  cat <<EOF >&2
ERROR: debug-verify stash missing: ${stash_caps}
EL10 rpmbuild removes BUILD/ after success (rmbuild); debug %install must copy
monitor-build-capabilities.json into rpmbuild/debug-verify/.
Re-run: prepare_rpmbuild_dirs.sh --debug-build, then debug rpmbuild (hpc_debug_build 1).
Optional: rpmbuild --noclean keeps BUILD for inspection only; stash is the supported path.
EOF
  return 1
}

ensure_stress_ng() {
  if command -v stress-ng >/dev/null 2>&1; then
    return 0
  fi
  echo "stress-ng not found; installing via package manager ..."
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y stress-ng
  elif command -v yum >/dev/null 2>&1; then
    yum install -y stress-ng
  else
    echo "ERROR: stress-ng missing and neither dnf nor yum is available" >&2
    return 1
  fi
  if ! command -v stress-ng >/dev/null 2>&1; then
    echo "ERROR: stress-ng still missing after package install" >&2
    return 1
  fi
}

main() {
  if test "${1:-}" = "-h" || test "${1:-}" = "--help"; then
    usage
    return 0
  fi

  local monitor_dir rpm_topdir dist_top ws py caps shm_dir tier enable_slow
  local slug expectations report_dir verify_dir
  local post_install_sleep

  monitor_dir="$(cd "${MONITOR_DIR}" && pwd)"
  rpm_topdir="${RPM_TOPDIR:-${monitor_dir}/rpmbuild}"
  dist_top="${DIST_TOP:-$(resolve_dist_top "${monitor_dir}")}"
  ws="${WORKSPACE_ROOT:-$(resolve_workspace_root "${monitor_dir}")}"
  py="$(resolve_python "${ws}")"
  verify_dir="${rpm_topdir}/debug-verify"

  shm_dir="${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}"
  enable_slow="${ENABLE_SLOW_TIER:-1}"
  case "${enable_slow}" in
  0 | false | FALSE | no | NO | off | OFF) tier="slowtier0" ;;
  *) tier="slowtier1" ;;
  esac

  if test "${RPM_BUILD:-0}" = "1"; then
    build_debug_rpm "${rpm_topdir}" || return 1
  fi

  caps="$(ensure_capabilities_json "${monitor_dir}" "${rpm_topdir}" "${dist_top}" "${py}" "${tier}")" \
    || return 1
  test -f "${caps}" || {
    echo "ERROR: capabilities not found at ${caps}" >&2
    return 1
  }

  if test "${SKIP_INSTALL:-0}" != "1"; then
    install_main_daemon_rpm "${rpm_topdir}"
    echo "RPM %post enables and starts hpcperfstats.service (no extra restart here)."
    post_install_sleep="${POST_INSTALL_SLEEP_SECONDS:-${FULL}}"
    if test "${post_install_sleep}" -gt 0 2>/dev/null; then
      ensure_stress_ng || return 1
      echo "Running stress-ng --cpu 0 --timeout ${post_install_sleep}s after install (shm soak) ..."
      stress-ng --cpu 0 --timeout "${post_install_sleep}s"
    fi
  else
    find_main_daemon_rpm "${rpm_topdir}" >/dev/null || {
      echo "ERROR: main hpcperfstatsd RPM not found under ${rpm_topdir}/RPMS (SKIP_INSTALL=1)" >&2
      echo "Run debug rpmbuild first (prepare_rpmbuild_dirs.sh --debug-build)." >&2
      return 1
    }
  fi

  # Crash-loop / failed start leaves stale shm; do not treat it as a live sample.
  # Only after install (SKIP_INSTALL=0): unit tests use SKIP_INSTALL=1 without a real daemon.
  if test "${SKIP_INSTALL:-0}" != "1" && test "${SKIP_SYSTEMCTL_CHECK:-0}" != "1"; then
    if command -v systemctl >/dev/null 2>&1; then
      svc_state="$(systemctl is-active hpcperfstats.service 2>/dev/null || true)"
      if test "${svc_state}" != "active"; then
        echo "ERROR: hpcperfstats.service is '${svc_state:-unknown}' (want active); refusing shm validate" >&2
        systemctl status hpcperfstats.service --no-pager -l 2>&1 | head -40 >&2 || true
        return 1
      fi
      echo "hpcperfstats.service is active"
    else
      echo "WARN: systemctl not found; skipping hpcperfstats.service active check" >&2
    fi
  fi

  wait_full_payload "${shm_dir}" "${WAIT_FULL_SECONDS:-900}" || return 1

  slug="$("${py}" -c "import json; print(json.load(open('${caps}'))['capability_slug'])")"
  echo "capability_slug=${slug}"

  if test "${SKIP_SHM_LS:-0}" != "1"; then
    ls -la "${shm_dir}"/{schema,fast,full}
  fi

  expectations="${verify_dir}/expectations_${slug}.json"
  report_dir="${ws}/test_runs/monitor"
  mkdir -p "${report_dir}" "${verify_dir}"

  echo "Building expectations ..."
  "${py}" "${monitor_dir}/scripts/build_message_expectations.py" \
    --capabilities "${caps}" \
    --shm-dir "${shm_dir}" \
    --enable-slow-tier "${enable_slow}" \
    --out "${expectations}"

  echo "Validating /dev/shm payloads ..."
  validate_args=(
    --capabilities "${caps}"
    --manifest "${expectations}"
    --shm-dir "${shm_dir}"
    --live-spot-check
    --report "${report_dir}/validate_rpm_debug_${slug}_$(date +%F).txt"
  )
  if test -n "${WAIT_SHM_SECONDS:-}"; then
    validate_args+=(--wait-shm-seconds "${WAIT_SHM_SECONDS}")
  else
    validate_args+=(--wait-shm-seconds 30)
  fi
  if env_flag_on "${STRICT_LIVE_SPOT_CHECK:-1}"; then
    validate_args+=(--strict-live-spot-check)
  fi
  if env_flag_on "${STRICT_PLAUSIBILITY:-1}"; then
    validate_args+=(--strict-plausibility)
  fi
  if env_flag_on "${CROSS_SAMPLE_CHECK:-1}"; then
    validate_args+=(--cross-sample-check --cross-sample-wait-full)
    if env_flag_on "${STRICT_CROSS_SAMPLE:-1}"; then
      validate_args+=(--strict-cross-sample)
    fi
    if test -n "${HPCPERFSTATS_CONF:-}"; then
      validate_args+=(--conf "${HPCPERFSTATS_CONF}")
    fi
  fi
  if ! env_flag_on "${STRICT_SENTINELS:-1}"; then
    validate_args+=(--no-strict-sentinels)
  fi
  if ! env_flag_on "${SENTINEL_PROBE_CHECK:-1}"; then
    validate_args+=(--no-sentinel-probe-check)
  fi
  if ! env_flag_on "${INDEPENDENT_READ_CHECK:-1}"; then
    validate_args+=(--no-independent-read)
  fi
  if env_flag_on "${CONSUMER_PARSE_CHECK:-1}"; then
    validate_args+=(--consumer-parse-check)
  fi
  # Golden is opt-in only (GOLDEN_DIR=/path|auto or GOLDEN_CHECK=1).
  if test "${GOLDEN_CHECK:-0}" = "1" || test -n "${GOLDEN_DIR:-}"; then
    resolved_golden="$(
      MONITOR_DIR="${monitor_dir}" SLUG="${slug}" ENABLE_SLOW="${enable_slow}" \
      GOLDEN_DIR="${GOLDEN_DIR:-}" GOLDEN_CHECK="${GOLDEN_CHECK:-0}" \
      "${py}" - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["MONITOR_DIR"]) / "scripts"))
from lib.golden_diff import resolve_optin_golden_dir

slow = os.environ.get("ENABLE_SLOW", "1")
enable_slow = slow not in ("0", "false", "FALSE", "no", "NO", "off", "OFF")
path = resolve_optin_golden_dir(
    monitor_dir=Path(os.environ["MONITOR_DIR"]),
    slug=os.environ["SLUG"],
    golden_dir_env=os.environ.get("GOLDEN_DIR") or None,
    golden_check=os.environ.get("GOLDEN_CHECK", "0") == "1",
    enable_slow_tier=enable_slow,
)
print(path if path is not None else "")
PY
    )"
    if test -n "${resolved_golden}"; then
      echo "Using golden dir: ${resolved_golden}"
      validate_args+=(--golden-dir "${resolved_golden}")
    else
      echo "WARN: golden opted in but no matching shm_*_${slug} files under tests/expected (or GOLDEN_DIR)"
    fi
  fi
  validate_out="$(mktemp)"
  if ! "${py}" "${monitor_dir}/scripts/validate_shm_messages.py" "${validate_args[@]}" 2>&1 \
    | tee "${validate_out}"; then
    # GPU hint only for the no-rows failure (cross-sample nvidia_gpu WARNs are not it).
    grep -qF "type 'nvidia_gpu': no rows" "${validate_out}" || {
      rm -f "${validate_out}"
      return 1
    }
    rm -f "${validate_out}"
    echo "HINT: nvidia_gpu expected devices but no rows: check the journal for 'nvidia_gpu: source'" >&2
    echo "      (NVML+GPM vs DCGM and why), then on the DCGM path 'field group creation failed' /" >&2
    echo "      stage=6 and Driver/stack probe libdcgm=." >&2
    if command -v journalctl >/dev/null 2>&1; then
      journalctl -u hpcperfstats.service -b --no-pager 2>/dev/null \
        | grep -E 'Driver/stack probe|field group creation failed|nvidia_gpu: no device rows|nvidia_gpu: source|NVML\+GPM failed' \
        | tail -20 >&2 || true
    fi
    return 1
  fi

  rm -f "${validate_out}"
  echo "PASS: validate_shm_messages (slug=${slug})"

  # Live fault injection (DEBUG RPMs; SKIP line otherwise): restarts the daemon twice.
  CAPS="${caps}" MANIFEST="${expectations}" SHM_DIR="${shm_dir}" PY="${py}" \
    FAULT_INJECT_SAMPLE_FREQ="${FULL}" \
    FAULT_INJECT_REPORT="${report_dir}/fault_inject_rpm_debug_${slug}_$(date +%F).txt" \
    "${monitor_dir}/scripts/fault_inject_verify.sh"
}

if test "$(basename "$0")" = "rpm_debug_shm_verify.sh"; then
  main "$@"
fi
