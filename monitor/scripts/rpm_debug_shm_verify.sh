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
#           WAIT_SHM_SECONDS, WAIT_FULL_SECONDS (default 900; 0 = no wait): after each daemon
#             start, bound on the wait for full rows (and fast rows with the slow tier on),
#           RPM_BUILD=1 (remove stale daemon RPMs and run the debug rpmbuild first),
#           HPCS_RELEASE (RPM_BUILD only: adds --define "hpc_release N"; default spec Release),
#           CROSS_SAMPLE_CHECK (default 1; set 0 to disable),
#           STRICT_LIVE_SPOT_CHECK / STRICT_PLAUSIBILITY / STRICT_CROSS_SAMPLE
#             (default 1; set 0 to disable),
#           STRICT_SENTINELS / SENTINEL_PROBE_CHECK / INDEPENDENT_READ_CHECK /
#             CONSUMER_PARSE_CHECK (value sentinel layers; default 1; set 0 to disable),
#           FAULT_INJECT (default 1; 0 = SKIP): after validation, restart the DEBUG daemon with
#             debug_fault_inject for FAULT_INJECT_TICKS cycles and check codes, recovery,
#             bridges and site coverage (scripts/fault_inject_verify.sh; release RPMs SKIP),
#           COUNTER_CHECK (default 1; 0 = SKIP): after validation, the counter-accuracy layer
#             (canary_counter_check.sh --accuracy): memory bytes, FLOPs, instructions and
#             energy from the DEBUG shm mirror vs known loads; COUNTER_CHECK_SECONDS (load
#             length, default 20), COUNTER_CHECK_TIMEOUT (default 3600; release RPMs SKIP),
#           PERF_RAW_FLOPS_CHECK / SCF_PERF_CHECK / PMU_SHARING_CHECK / FP_SLICES_CHECK
#             (default 1; 0 = SKIP): the standalone counter checks wired from the inventory
#             in docs/how-to/run-correctness-campaign.md (NOT_RUN where the arch does
#             not apply),
#             EXTRA_CHECK_TIMEOUT (default 1800 per check),
#           GOLDEN_DIR=/path|auto or GOLDEN_CHECK=1 (opt-in golden diff),
#           HPCPERFSTATS_CONF=/path/to/hpcperfstats.conf, REPORT_DIR (default
#             <workspace>/test_runs/monitor).
#
# Every run (also an early failure) writes test_runs/monitor/correctness_<host>_<slug>_<UTC>.json
# (scripts/lib/correctness_summary.py: each check PASS / FAIL / NOT_RUN / SKIP with reason and
# ratio) for the cross-architecture campaign; the exit status is non-zero when any check FAILed.
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  # One-off Release without a spec edit, as in the prepare footer.
  local release_define=()
  test -z "${HPCS_RELEASE:-}" || release_define=(--define "hpc_release ${HPCS_RELEASE}")
  echo "Building the debug RPM (rpmbuild -ba, hpc_debug_build 1) ..."
  rpmbuild -ba --define "_topdir ${rpm_topdir}" ${release_define[@]+"${release_define[@]}"} \
    --define "hpc_debug_build 1" "${spec}" </dev/null || {
    echo "ERROR: debug rpmbuild failed" >&2
    return 1
  }
}

# wait_payloads SHM_DIR SECONDS ENABLE_SLOW: waits up to SECONDS for metric rows in full and, with
# the slow tier on, in fast. The daemon unlinks both at startup and rewrites each at its tier's
# first tick (fast up to one sample_freq_slow after full); slow tier off writes no fast.
wait_payloads() {
  local shm_dir="$1" seconds="$2" names=full deadline name

  test "${seconds}" -gt 0 2>/dev/null || return 0
  env_flag_on "$3" && names="full fast"
  deadline=$(($(date +%s) + seconds))
  for name in ${names}; do
    echo "Waiting up to ${seconds}s for a ${name} payload with metric rows ..."
    until grep -qE '^[a-z][a-z0-9_]* ' "${shm_dir}/${name}" 2>/dev/null; do
      if test "$(date +%s)" -ge "${deadline}"; then
        echo "ERROR: no metric rows in ${shm_dir}/${name} after ${seconds}s" >&2
        return 1
      fi
      sleep "${WAIT_FULL_POLL_SECONDS:-15}"
    done
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
After each daemon start, waits up to WAIT_FULL_SECONDS (default 900) for full (and, slow
tier on, fast) payloads with metric rows.
After validation: counter accuracy (COUNTER_CHECK=0 to skip), the wired canary checks, then
fault injection (FAULT_INJECT=0 to skip). Writes a per-node campaign JSON next to the reports.
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

# record_check RESULTS NAME STATUS RATIO REASON: one campaign result line (TSV, no tabs in REASON).
record_check() {
  local reason="${5//$'\t'/ }"
  printf '%s\t%s\t%s\t%s\n' "$2" "$3" "$4" "${reason//$'\n'/ }" >>"$1"
}

# run_counter_accuracy CAPS SHM_DIR PY RESULTS REPORT_DIR: the counter-accuracy layer (DEBUG
# builds; COUNTER_CHECK=0 or a release build SKIPs it). Statuses go to RESULTS; returns 0.
run_counter_accuracy() {
  local caps="$1" shm_dir="$2" py="$3" results="$4" report_dir="$5" debug reason=""
  local lib="${SCRIPT_DIR}/lib/counter_accuracy.py"

  if ! env_flag_on "${COUNTER_CHECK:-1}"; then
    reason="COUNTER_CHECK=0"
  else
    debug="$("${py}" -c "import json,sys; print(int(bool(json.load(open(sys.argv[1])).get('configure', {}).get('debug'))))" "${caps}")"
    test "${debug}" = "1" || reason="release build: no DEBUG /dev/shm mirror"
  fi
  if test -n "${reason}"; then
    echo "SKIP: counter accuracy (${reason})"
    "${py}" "${lib}" record --results "${results}" --status SKIP --reason "${reason}" --all
    return 0
  fi
  echo "Counter accuracy: daemon counters vs known loads (COUNTER_CHECK=0 to skip) ..."
  # --foreground keeps the check in this process group, so Ctrl-C reaches its INT trap; its
  # exit 130 then stops the whole run (bash would otherwise carry on with the next check).
  CANARY_SHM="${shm_dir}" CANARY_PY="${py}" CANARY_LOG_DIR="${report_dir}" \
    timeout --foreground "${COUNTER_CHECK_TIMEOUT:-3600}" "${SCRIPT_DIR}/canary_counter_check.sh" \
    --accuracy --results "${results}" --seconds "${COUNTER_CHECK_SECONDS:-20}" || {
    test $? != 130 || exit 130
  }
}

# run_extra_check RESULTS NAME ENVVAR APPLIES WHY -- CMD...: one wired check, bounded. ENVVAR=0
# SKIPs it; APPLIES other than 1 is NOT_RUN with WHY. Exit 0 PASS, 2 or 77 NOT_RUN (missing
# prerequisite; last output line as reason), anything else FAIL.
run_extra_check() {
  local results="$1" name="$2" var="$3" applies="$4" why="$5" out rc status reason
  shift 6
  if ! env_flag_on "${!var:-1}"; then
    echo "SKIP: ${name} (${var}=0)"
    record_check "${results}" "${name}" SKIP - "${var}=0"
    return 0
  fi
  if test "${applies}" != 1; then
    echo "NOT_RUN: ${name} (${why})"
    record_check "${results}" "${name}" NOT_RUN - "${why}"
    return 0
  fi
  out="$(mktemp)"
  echo "Running ${name} (${var}=0 to skip) ..."
  if timeout --foreground "${EXTRA_CHECK_TIMEOUT:-1800}" "$@" 2>&1 | tee "${out}"; then
    rc=0
  else
    rc=$?
  fi
  reason="$(grep -v '^[[:space:]]*$' "${out}" | tail -n 1 | cut -c1-300)"
  rm -f "${out}"
  test "${rc}" != 130 || exit 130
  case "${rc}" in
  0) status=PASS ;;
  2 | 77) status=NOT_RUN ;;
  124) status=FAIL reason="timed out after ${EXTRA_CHECK_TIMEOUT:-1800}s" ;;
  *) status=FAIL ;;
  esac
  record_check "${results}" "${name}" "${status}" - "exit ${rc}: ${reason}"
}

# run_canary_checks PY RESULTS SHM_DIR ENABLE_SLOW: the standalone counter checks that run
# unattended as root in bounded time (check inventory in
# docs/how-to/run-correctness-campaign.md).
run_canary_checks() {
  local py="$1" results="$2" shm_dir="$3" slow="$4" class grace=0 scf=0 x86=0 idle_pmu=0
  class="$("${py}" "${SCRIPT_DIR}/lib/counter_accuracy.py" arch --field class)" || class=other
  test "${class}" != grace || grace=1
  if test "${grace}" = 1 && ls -d /sys/bus/event_source/devices/nvidia_scf_pmu_* >/dev/null 2>&1; then
    scf=1
  fi
  case "${class}" in intel | amd) x86=1 ;; esac
  # The running daemon's direct-perf reader holds the core FP counters (every perf_raw_flops
  # event reads <not counted>), so that canary runs with the service stopped.
  if test "${x86}" = 1 && env_flag_on "${PERF_RAW_FLOPS_CHECK:-1}"; then
    idle_pmu=1
    systemctl stop hpcperfstats.service
  fi
  run_extra_check "${results}" perf_raw_flops PERF_RAW_FLOPS_CHECK "${x86}" \
    "x86 perf FLOP events only (arch ${class})" -- "${SCRIPT_DIR}/perf_raw_flops_check.sh"
  if test "${idle_pmu}" = 1; then
    systemctl start hpcperfstats.service
    wait_payloads "${shm_dir}" "${WAIT_FULL_SECONDS:-900}" "${slow}"
  fi
  run_extra_check "${results}" scf_perf SCF_PERF_CHECK "${scf}" \
    "needs Grace nvidia_scf_pmu_* PMUs (arch ${class})" -- \
    "${SCRIPT_DIR}/canary_counter_check.sh" --scf --warm 30 --window 60
  run_extra_check "${results}" pmu_sharing PMU_SHARING_CHECK 1 - -- \
    "${SCRIPT_DIR}/canary_counter_check.sh" --sharing-only --window 60
  run_extra_check "${results}" fp_slices FP_SLICES_CHECK "${grace}" \
    "Grace arm_fp_*_spec keys only (arch ${class})" -- \
    "${SCRIPT_DIR}/canary_counter_check.sh" --precisions "dp sp hp" --warm 30 --window 60
}

# write_correctness_summary PY RESULTS REPORT_DIR CAPS VALIDATE_REPORT VALIDATE_RC: the per-node
# campaign JSON next to the text reports; returns 1 when any check FAILed.
write_correctness_summary() {
  local py="$1" results="$2" report_dir="$3" caps="$4" vreport="$5" vrc="$6" slug args=()
  slug="$("${py}" -c "import json,sys; print(json.load(open(sys.argv[1]))['capability_slug'])" \
    "${caps}" 2>/dev/null)" || slug=unknown
  args=(--results "${results}" --caps "${caps}"
    --rpm-build "$(timeout 30 rpm -q hpcperfstatsd 2>/dev/null || echo unknown)"
    --out "${report_dir}/correctness_$(hostname -s)_${slug}_$(date -u +%Y%m%dT%H%M%SZ).json")
  test -z "${vreport}" || args+=(--validate-report "${vreport}")
  test -z "${vrc}" || args+=(--validate-rc "${vrc}")
  env_flag_on "${CROSS_SAMPLE_CHECK:-1}" || args+=(--skip "cross_sample=CROSS_SAMPLE_CHECK=0")
  env_flag_on "${INDEPENDENT_READ_CHECK:-1}" ||
    args+=(--skip "independent_read=INDEPENDENT_READ_CHECK=0")
  env_flag_on "${CONSUMER_PARSE_CHECK:-1}" || args+=(--skip "consumer_parse=CONSUMER_PARSE_CHECK=0")
  test "${GOLDEN_CHECK:-0}" = "1" || test -n "${GOLDEN_DIR:-}" ||
    args+=(--skip "golden=opt-in (GOLDEN_CHECK=1 or GOLDEN_DIR)")
  "${py}" "${SCRIPT_DIR}/lib/correctness_summary.py" write "${args[@]}"
}

main() {
  if test "${1:-}" = "-h" || test "${1:-}" = "--help"; then
    usage
    return 0
  fi
  local work rc=0 vrc=""

  VERIFY_MONITOR_DIR="$(cd "${MONITOR_DIR}" && pwd)"
  VERIFY_WS="${WORKSPACE_ROOT:-$(resolve_workspace_root "${VERIFY_MONITOR_DIR}")}"
  VERIFY_PY="$(resolve_python "${VERIFY_WS}")"
  VERIFY_REPORT_DIR="${REPORT_DIR:-${VERIFY_WS}/test_runs/monitor}"
  mkdir -p "${VERIFY_REPORT_DIR}"
  work="$(mktemp -d)"
  VERIFY_WORK="${work}"
  : >"${work}/results.tsv"
  # The node run is a subshell with its own errexit, so every outcome (including an early
  # failure) still reaches the campaign summary below.
  set +e
  (
    set -e
    verify_node
  )
  rc=$?
  set -e
  test ! -f "${work}/validate_rc" || vrc="$(cat "${work}/validate_rc")"
  write_correctness_summary "${VERIFY_PY}" "${work}/results.tsv" "${VERIFY_REPORT_DIR}" \
    "$(cat "${work}/caps" 2>/dev/null || true)" "$(cat "${work}/validate_report" 2>/dev/null || true)" \
    "${vrc}" || rc=1
  rm -rf "${work}"
  return "${rc}"
}

verify_node() {
  local monitor_dir rpm_topdir dist_top ws py caps shm_dir tier enable_slow
  local slug expectations report_dir verify_dir results validate_report
  local post_install_sleep fi_out fi_rc

  monitor_dir="${VERIFY_MONITOR_DIR}"
  ws="${VERIFY_WS}"
  py="${VERIFY_PY}"
  report_dir="${VERIFY_REPORT_DIR}"
  results="${VERIFY_WORK}/results.tsv"
  rpm_topdir="${RPM_TOPDIR:-${monitor_dir}/rpmbuild}"
  dist_top="${DIST_TOP:-$(resolve_dist_top "${monitor_dir}")}"
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
  printf '%s' "${caps}" >"${VERIFY_WORK}/caps"

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

  wait_payloads "${shm_dir}" "${WAIT_FULL_SECONDS:-900}" "${enable_slow}" || return 1

  slug="$("${py}" -c "import json; print(json.load(open('${caps}'))['capability_slug'])")"
  echo "capability_slug=${slug}"
  record_check "${results}" preflight PASS - "installed, service active, full payload, slug ${slug}"

  if test "${SKIP_SHM_LS:-0}" != "1"; then
    ls -la "${shm_dir}"/{schema,fast,full} || true
  fi

  expectations="${verify_dir}/expectations_${slug}.json"
  validate_report="${report_dir}/validate_rpm_debug_${slug}_$(date +%F).txt"
  mkdir -p "${verify_dir}"

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
    --report "${validate_report}"
  )
  if test -n "${WAIT_SHM_SECONDS:-}"; then
    validate_args+=(--wait-shm-seconds "${WAIT_SHM_SECONDS}")
  else
    validate_args+=(--wait-shm-seconds 300)
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
  # An early validate exit writes no report; never let the summary read an older one.
  rm -f "${validate_report}"
  printf '%s' "${validate_report}" >"${VERIFY_WORK}/validate_report"
  if ! "${py}" "${monitor_dir}/scripts/validate_shm_messages.py" "${validate_args[@]}" 2>&1 \
    | tee "${validate_out}"; then
    echo 1 >"${VERIFY_WORK}/validate_rc"
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
  echo 0 >"${VERIFY_WORK}/validate_rc"
  echo "PASS: validate_shm_messages (slug=${slug})"

  run_counter_accuracy "${caps}" "${shm_dir}" "${py}" "${results}" "${report_dir}"
  run_canary_checks "${py}" "${results}" "${shm_dir}" "${enable_slow}"

  # Live fault injection (DEBUG RPMs; SKIP line otherwise): restarts the daemon twice, so it
  # runs after every check that reads the running daemon.
  fi_out="$(mktemp)"
  if CAPS="${caps}" MANIFEST="${expectations}" SHM_DIR="${shm_dir}" PY="${py}" \
    FAULT_INJECT_SAMPLE_FREQ="${FULL}" \
    FAULT_INJECT_REPORT="${report_dir}/fault_inject_rpm_debug_${slug}_$(date +%F).txt" \
    "${monitor_dir}/scripts/fault_inject_verify.sh" 2>&1 | tee "${fi_out}"; then
    fi_rc=0
  else
    fi_rc=$?
  fi
  record_fault_inject "${results}" "${fi_out}" "${fi_rc}"
  rm -f "${fi_out}"
  # Its last restart returns before the daemon republishes: never hand off a missing mirror.
  wait_payloads "${shm_dir}" "${WAIT_FULL_SECONDS:-900}" "${enable_slow}" || {
    echo "ERROR: debug shm mirrors not republished after the fault_inject restart" >&2
    return 1
  }
}

# record_fault_inject RESULTS OUT RC: fault_inject_verify.sh output and exit code -> one status.
# A SKIP line or the checker's NOT_RUN line (no planned fault point reached) wins over exit 0.
record_fault_inject() {
  local results="$1" out="$2" rc="$3"
  if grep -q '^SKIP:' "${out}"; then
    record_check "${results}" fault_inject SKIP - "$(grep -m1 '^SKIP:' "${out}")"
  elif test "${rc}" = 0 && grep -q '^fault_inject: NOT_RUN' "${out}"; then
    record_check "${results}" fault_inject NOT_RUN - "$(grep -m1 '^fault_inject: NOT_RUN' "${out}")"
  elif test "${rc}" = 0; then
    record_check "${results}" fault_inject PASS - "fault_inject_verify.sh exit 0"
  else
    record_check "${results}" fault_inject FAIL - \
      "exit ${rc}: $(grep -v '^[[:space:]]*$' "${out}" | tail -n 1 | cut -c1-300)"
  fi
}

if test "$(basename "$0")" = "rpm_debug_shm_verify.sh"; then
  main "$@"
fi
