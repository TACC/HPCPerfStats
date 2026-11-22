#!/bin/sh
# scripts/fault_inject_verify.sh + fault_inject_check.py against a stubbed daemon: the "restart"
# command (re)starts a fake shm writer that honours debug_fault_inject in the conf, at slow-tier
# cadence (`full` and `fast` samples alternate) and with a `fault_fired` file. Checks PASS on a
# clean run (conf restored) where the one injected cycle is a fast-only one, NOTE for planned
# points that never fired (nvml_fetch and dcgm_fetch), FAIL for them without a fault_fired file
# (older daemon), PASS for a fired nvml_fetch whose GPU row reads values from the fallback path
# (and a dcgm_fetch that then fires there), NOT_RUN when no planned point fired, FAIL when a key
# outside the injected point changes code, NOTE for a never-fired site of a CI-covered file, FAIL
# for one outside CI coverage, and the FAULT_INJECT=0 / release SKIP lines.
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
{"capability_slug": "x86_64-ver3.1-debug-hw-x86perf-sent1-slowtier1", "configure": {"debug": true, "cpu_backend": "x86"}}
J
sed 's/"debug": true/"debug": false/' "${WORK}/caps.json" >"${WORK}/caps_release.json"
cat >"${WORK}/manifest.json" <<'J'
{"capability_slug": "x86_64-ver3.1-debug-hw-x86perf-sent1-slowtier1", "enable_slow_tier": true,
 "types": {"host_ps": {"schema_keys": ["ctxt,E", "processes,E,R=S", "load_1,", "load_5,", "load_15,", "nr_running,", "nr_threads,"]},
           "nvidia_gpu": {"schema_keys": ["gpu_util,"]}}}
J
# shellcheck disable=SC2016 # literal $ banner lines
printf '%s\n' '$hpcperfstats 3.1' '$hostname h1' '$sentinels 1' \
  '!host_ps ctxt,E processes,E,R=S load_1, load_5, load_15, nr_running, nr_threads,' \
  '!nvidia_gpu gpu_util,' >"${SHM}/schema"

# Fake daemon: one sample per second (slower than the capture poll, so none is skipped), `full`
# (every key) on even cycles and `fast` (no R=S key) on odd ones. Each debug_fault_inject entry
# `point@d+n` covers cycles d .. d+n-1: open:/proc/loadavg marks the load keys READ_FAILED (and,
# in "leak" mode, ctxt BACKEND_ERROR) and counts its fires in fault_fired; the GPU points fire only
# in "gpm" mode: nvml_fetch marks gpu_util BACKEND_ERROR on its first cycle and the row then reads
# changing values (the DCGM fallback), dcgm_fetch marks it in its own window.
# With FAULT_INJECT_TICKS=1 the only faulted cycle is 1, a fast one. Modes: nofired = no
# fault_fired file (older daemon); unreached = loadavg never fires either.
cat >"${WORK}/writer.sh" <<'W'
conf="$1"; shm="$2"; mode="$3"; i=0; fired=0; nfired=0; dfired=0
rm -f "$shm/fast" "$shm/full" "$shm/fault_fired"
spec="$(sed -n 's/^debug_fault_inject //p' "$conf")"
while test "$i" -lt 200; do
  load="11 12 13 2 300"; ctxt=$((1000 + i)); gpu=40; lines=""
  for e in $(echo "$spec" | tr ',' ' '); do
    w="${e##*@}"; d="${w%+*}"; n="${w#*+}"
    case "$e" in
    open:/proc/loadavg*)
      if test "$mode" != unreached && test "$i" -ge "$d" && test "$i" -lt $((d + n)); then
        fired=$((fired + 1)); load="-3 -3 -3 -3 -3"
        test "$mode" = leak && ctxt=-7
      fi
      lines="${lines}${e} ${fired}
" ;;
    nvml_fetch@*)
      if test "$mode" = gpm && test "$i" -ge "$d"; then
        if test "$i" -eq "$d"; then nfired=1; gpu=-7; else gpu=$((40 + i)); fi
      fi
      lines="${lines}${e} ${nfired}
" ;;
    dcgm_fetch@*)
      if test "$mode" = gpm && test "$i" -ge "$d" && test "$i" -lt $((d + n)); then
        dfired=$((dfired + 1)); gpu=-7
      fi
      lines="${lines}${e} ${dfired}
" ;;
    *) lines="${lines}${e} 0
" ;;
    esac
  done
  ts="$(date +%s.%N)"
  if test $((i % 2)) -eq 0; then
    printf '%s j1 h1\nhost_ps - @full %s 50 %s\nnvidia_gpu 0 @full %s\n' "$ts" "$ctxt" "$load" "$gpu" >"$shm/full.tmp"
    mv "$shm/full.tmp" "$shm/full"
  else
    printf '%s j1 h1\nhost_ps - @fast %s %s\nnvidia_gpu 0 @fast %s\n' "$ts" "$ctxt" "$load" "$gpu" >"$shm/fast.tmp"
    mv "$shm/fast.tmp" "$shm/fast"
  fi
  # One atomic replace each: a reader between a truncate and an append missed the uncovered site
  # (seen under qemu on the aarch64 cross run).
  printf '%s\n' 'ps.c:54 3' 'tmpfs.c:47 0' >"$shm/sites.tmp"
  test "$mode" = uncovered && printf '%s\n' 'hps_new_collector.c:12 0' >>"$shm/sites.tmp"
  mv "$shm/sites.tmp" "$shm/sentinel_sites"
  if test "$mode" != nofired; then
    printf '%s' "$lines" >"$shm/fired.tmp"
    mv "$shm/fired.tmp" "$shm/fault_fired"
  fi
  i=$((i + 1)); sleep 1
done
W
restart_cmd() {
  # shellcheck disable=SC2016 # $(cat ...) and $! expand when the restart command runs
  printf 'test -f %s/pid && kill $(cat %s/pid) 2>/dev/null; sh %s/writer.sh %s %s %s >/dev/null 2>&1 & echo $! >%s/pid' \
    "${WORK}" "${WORK}" "${WORK}" "${WORK}/conf" "${SHM}" "$1" "${WORK}"
}
run_verify() {
  printf 'sample_freq 1\nsample_freq_slow 2\n' >"${WORK}/conf"
  sh -c "$(restart_cmd "$1")"
  sleep 2
  CAPS="${2:-${WORK}/caps.json}" MANIFEST="${WORK}/manifest.json" SHM_DIR="${SHM}" PY="${PY}" \
    FAULT_INJECT_CONF="${WORK}/conf" FAULT_INJECT_TICKS=1 FAULT_INJECT_SAMPLE_FREQ=1 \
    FAULT_INJECT_SYS_ROOT="${WORK}/nosys" FAULT_INJECT_RESTART_CMD="$(restart_cmd "$1")" \
    "${ROOT}/scripts/fault_inject_verify.sh"
}

run_verify clean >"${WORK}/clean.txt" 2>&1 || { cat "${WORK}/clean.txt" >&2; exit 1; }
grep -q '^fault injection: debug_fault_inject open:/proc/loadavg@1+1,nvml_fetch@1+1,dcgm_fetch@6+1$' "${WORK}/clean.txt"
grep -q '^PASS fault_inject open:/proc/loadavg@1+1' "${WORK}/clean.txt"
grep -q '^NOTE fault_inject nvml_fetch@1+1: fault point not reached on this node (0 fires; e.g. DCGM serves the GPUs' "${WORK}/clean.txt"
grep -q '^NOTE fault_inject dcgm_fetch@6+1: fault point not reached on this node (0 fires; e.g. NVML+GPM serves the GPUs' "${WORK}/clean.txt"
grep -q '^NOTE fault_inject site tmpfs.c:47 never fired (covered by CI matrix)' "${WORK}/clean.txt"
grep -q '^fault_inject: PASS' "${WORK}/clean.txt"
if grep -q debug_fault_inject "${WORK}/conf"; then
  echo "FAIL: conf not restored" >&2
  exit 1
fi

# An older daemon writes no fault_fired: the unfired point FAILs as before.
if run_verify nofired >"${WORK}/nofired.txt" 2>&1; then
  echo "FAIL: an unfired point passed without a fault_fired file" >&2
  cat "${WORK}/nofired.txt" >&2
  exit 1
fi
grep -q '^FAIL fault_inject nvml_fetch@1+1: no nvidia_gpu key carried BACKEND_ERROR' "${WORK}/nofired.txt"
grep -q '^FAIL fault_inject dcgm_fetch@6+1: no nvidia_gpu key carried BACKEND_ERROR' "${WORK}/nofired.txt"

# NVML+GPM node: nvml_fetch hands the GPU to the DCGM path, where dcgm_fetch then fires.
run_verify gpm >"${WORK}/gpm.txt" 2>&1 || { cat "${WORK}/gpm.txt" >&2; exit 1; }
grep -q '^PASS fault_inject nvml_fetch@1+1: 1 key(s)' "${WORK}/gpm.txt"
grep -q '^PASS fault_inject nvml_fetch@1+1: 1 nvidia_gpu row(s) read values after the fallback$' "${WORK}/gpm.txt"
grep -q '^PASS fault_inject dcgm_fetch@6+1: 1 key(s)' "${WORK}/gpm.txt"
grep -q '^fault_inject: PASS' "${WORK}/gpm.txt"

run_verify unreached >"${WORK}/unreached.txt" 2>&1 || { cat "${WORK}/unreached.txt" >&2; exit 1; }
grep -q '^fault_inject: NOT_RUN' "${WORK}/unreached.txt"

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
