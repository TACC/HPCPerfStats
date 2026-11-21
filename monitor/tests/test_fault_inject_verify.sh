#!/bin/sh
# scripts/fault_inject_verify.sh + fault_inject_check.py against a stubbed daemon: the "restart"
# command (re)starts a fake shm writer that honours debug_fault_inject in the conf. Checks PASS on
# a clean run (conf restored), FAIL when a key outside the injected point changes code, NOTE for a
# never-fired site of a CI-covered file, FAIL for one outside CI coverage, and the
# FAULT_INJECT=0 / release SKIP lines.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${PY}"; then
  PY="$(command -v python3)"
fi
WORK="$(mktemp -d)"
trap 'test -f "${WORK}/pid" && kill "$(cat "${WORK}/pid")" 2>/dev/null; rm -rf "${WORK}"' EXIT
SHM="${WORK}/shm"
mkdir "${SHM}"

cat >"${WORK}/caps.json" <<'J'
{"capability_slug": "x86_64-ver3.1-debug-hw-x86perf-sent1-slowtier0", "configure": {"debug": true, "cpu_backend": "x86"}}
J
sed 's/"debug": true/"debug": false/' "${WORK}/caps.json" >"${WORK}/caps_release.json"
cat >"${WORK}/manifest.json" <<'J'
{"capability_slug": "x86_64-ver3.1-debug-hw-x86perf-sent1-slowtier0", "enable_slow_tier": false,
 "types": {"host_ps": {"schema_keys": ["ctxt,E", "processes,E", "load_1,", "load_5,", "load_15,", "nr_running,", "nr_threads,"]}}}
J
# shellcheck disable=SC2016 # literal $ banner lines
printf '%s\n' '$hpcperfstats 3.1' '$hostname h1' '$sentinels 1' \
  '!host_ps ctxt,E processes,E load_1, load_5, load_15, nr_running, nr_threads,' >"${SHM}/schema"

# Fake daemon: one `full` sample per second (slower than the capture poll, so no sample is
# skipped); while the conf holds debug_fault_inject, samples 1 and 2 (one clean cycle, then
# FAULT_INJECT_TICKS=2) carry the injected code (and, in "leak" mode, a second key the plan never
# named).
cat >"${WORK}/writer.sh" <<'W'
conf="$1"; shm="$2"; mode="$3"; i=0
while test "$i" -lt 200; do
  load="11 12 13 2 300"; ctxt=$((1000 + i))
  if grep -q '^debug_fault_inject' "$conf" && test "$i" -ge 1 && test "$i" -lt 3; then
    load="-3 -3 -3 -3 -3"
    test "$mode" = leak && ctxt=-7
  fi
  printf '%s j1 h1\nhost_ps - %s 50 %s\n' "$(date +%s.%N)" "$ctxt" "$load" >"$shm/full.tmp"
  mv "$shm/full.tmp" "$shm/full"
  # One atomic replace: a reader between a truncate and an append missed the uncovered site
  # (seen under qemu on the aarch64 cross run).
  printf '%s\n' 'ps.c:54 3' 'tmpfs.c:47 0' >"$shm/sites.tmp"
  test "$mode" = uncovered && printf '%s\n' 'hps_new_collector.c:12 0' >>"$shm/sites.tmp"
  mv "$shm/sites.tmp" "$shm/sentinel_sites"
  i=$((i + 1)); sleep 1
done
W
restart_cmd() {
  # shellcheck disable=SC2016 # $(cat ...) and $! expand when the restart command runs
  printf 'test -f %s/pid && kill $(cat %s/pid) 2>/dev/null; sh %s/writer.sh %s %s %s >/dev/null 2>&1 & echo $! >%s/pid' \
    "${WORK}" "${WORK}" "${WORK}" "${WORK}/conf" "${SHM}" "$1" "${WORK}"
}
run_verify() {
  printf 'sample_freq 1\n' >"${WORK}/conf"
  sh -c "$(restart_cmd "$1")"
  sleep 1
  CAPS="${2:-${WORK}/caps.json}" MANIFEST="${WORK}/manifest.json" SHM_DIR="${SHM}" PY="${PY}" \
    FAULT_INJECT_CONF="${WORK}/conf" FAULT_INJECT_TICKS=2 FAULT_INJECT_SAMPLE_FREQ=1 \
    FAULT_INJECT_SYS_ROOT="${WORK}/nosys" FAULT_INJECT_RESTART_CMD="$(restart_cmd "$1")" \
    "${ROOT}/scripts/fault_inject_verify.sh"
}

run_verify clean >"${WORK}/clean.txt" 2>&1 || { cat "${WORK}/clean.txt" >&2; exit 1; }
grep -q '^PASS fault_inject open:/proc/loadavg@1+2' "${WORK}/clean.txt"
grep -q '^fault injection: debug_fault_inject open:/proc/loadavg@1+2$' "${WORK}/clean.txt"
grep -q '^NOTE fault_inject site tmpfs.c:47 never fired (covered by CI matrix)' "${WORK}/clean.txt"
grep -q '^fault_inject: PASS' "${WORK}/clean.txt"
if grep -q debug_fault_inject "${WORK}/conf"; then
  echo "FAIL: conf not restored" >&2
  exit 1
fi

if run_verify leak >"${WORK}/leak.txt" 2>&1; then
  echo "FAIL: a key outside the injected point was not reported" >&2
  cat "${WORK}/leak.txt" >&2
  exit 1
fi
grep -q 'host_ps/- ctxt changed to BACKEND_ERROR outside the injected points' "${WORK}/leak.txt"
! grep -q debug_fault_inject "${WORK}/conf" || { echo "FAIL: conf not restored after FAIL" >&2; exit 1; }

# A never-fired site in a file no CI fault target forces still FAILs.
if run_verify uncovered >"${WORK}/uncovered.txt" 2>&1; then
  echo "FAIL: a site outside CI coverage was accepted" >&2
  cat "${WORK}/uncovered.txt" >&2
  exit 1
fi
grep -q '^FAIL fault_inject site hps_new_collector.c:12 never fired' "${WORK}/uncovered.txt"

FAULT_INJECT=0 run_verify clean | grep -q '^SKIP: fault injection (FAULT_INJECT=0)'
run_verify clean "${WORK}/caps_release.json" | grep -q '^SKIP: fault injection (release build'
bash -n "${ROOT}/scripts/fault_inject_verify.sh"
bash -n "${ROOT}/scripts/rpm_debug_shm_verify.sh"
echo "test_fault_inject_verify passed"
