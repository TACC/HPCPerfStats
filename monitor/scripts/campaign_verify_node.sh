#!/usr/bin/env bash
# Correctness-campaign node verify: site conf, pull, gates, debug RPM verify, GPU / DF steps,
# then erase the debug RPM only when every step returned 0.
#
#   scripts/campaign_verify_node.sh ls6|gb200      (root, any cwd)
#
#   ls6    adaptive LS6 node: Genoa (lscpu "EPYC 9xxx") adds the DF channel step and needs the
#          Genoa DF commit; the GPU field-response step runs only when nvidia-smi lists a GPU.
#   gb200  Horizon GB200: needs the CC 10.0 peak row, then a short verify (standalone checks
#          off) and one tensor load (1004).
#
# Replaces two operator paste blocks (6.2 KB / 4.1 KB): a paste over 4 KB loses lines on
# cluster nodes (c313-031 2026-11-23, see measurement/bin/campaign_node_run.sh). The output
# lines are the ones the blocks printed. Everything runs inside main, which bash has parsed
# before the `git pull` below can rewrite this file.
# HPCS_CONF_BAK_DIR / HPCS_TMP / HPCS_DEBUG_SHM / HPCS_AMD_DF / HPCS_CMAKE_ROOT override
# system paths for the tests (defaults /root, /tmp, /dev/shm/hpcperfstatsd-debug,
# /sys/bus/event_source/devices/amd_df, /opt/apps/cmake).

BAK="${HPCS_CONF_BAK_DIR:-/root}"
TMPD="${HPCS_TMP:-/tmp}"
D="${HPCS_DEBUG_SHM:-/dev/shm/hpcperfstatsd-debug}"
A="${HPCS_AMD_DF:-/sys/bus/event_source/devices/amd_df}"
CMR="${HPCS_CMAKE_ROOT:-/opt/apps/cmake}"
TY=nvidia_gpu
W='^(sm_active|fp64|fp32|fp16|tensor|integer|gpu_flops_rate)'

# Site conf from the packaged conf (broker keys only; values never printed), the given fleet
# markers removed, packaged files reset so the pull fast-forwards.
prepare_checkout() {
  echo "== $(hostname -f) $(date -u +%FT%TZ) in $PWD"
  echo "== 1. site conf, markers, pull, broker guard"
  cp -p src/hpcperfstats.conf "${BAK}/hps-conf.bak.$(date +%s)"
  git diff --stat src/hpcperfstats.conf hpcperfstats.spec
  mkdir -p site
  SF=site/hpcperfstats.site.conf
  K='^(server|queue|port|user|password)[[:space:]:=]'
  test -s "$SF" || grep -E "$K" src/hpcperfstats.conf >"$SF"
  chmod 600 "$SF"
  echo "site keys: $(cut -d' ' -f1 "$SF" | xargs)"
  ls -l scripts/fleet/*.force 2>/dev/null || echo "no markers"
  rm -f "${@/#/scripts/fleet/}"
  git checkout -- src/hpcperfstats.conf hpcperfstats.spec
  git pull --ff-only 2>&1 | tail -n 3
}

broker_guard() {
  export HPCS_BUNDLE_FLEET="$1"
  BK=1
  test "$G" = 1 && scripts/site_conf.sh fleet-guard . || BK=0
  test "$BK" = 0 && export HPCS_ALLOW_DEFAULT_BROKER=1
  test "$BK" = 0 && echo "NOTE localhost broker: no DB rows"
}

df_run() {
  echo "== 2a. DF channels 0-11 under socket-0 read load"
  lscpu | grep -E '^(Model name|Socket|NUMA node)' | cut -c1-90
  echo "cpumask $(cat "$A/cpumask")"
  cat "$A/format/event" "$A/format/umask"
  C=$(lscpu -p=CPU,SOCKET | awk -F, '!/^#/ && $2==0 {print $1}' \
    | paste -sd, -)
  N=$(echo "$C" | tr ',' '\n' | wc -l)
  echo "socket0 cpus=$N"
  for U in 0xffe 0xfff; do
    E=""
    for n in 0 1 2 3 4 5 6 7 8 9 10 11; do
      EV=$(printf 0x%x $((31 + 64 * n)))
      E="$E -e amd_df/event=$EV,umask=$U/"
    done
    echo "== umask $U (0xffe read beats, 0xfff write beats)"
    # shellcheck disable=SC2086  # E is a word list of -e options
    perf stat -a -x, $E -- taskset -c "$C" \
      ./scripts/canary_gemm_load.sh run --kernel read --mib 8192 \
      --threads "$N" --seconds 20 2>&1 \
      | grep -E 'amd_df|total_bytes' | cut -c1-120
  done
  DRC=0
}

# $1 prepare script; the rest are extra environment assignments for rpm_debug_shm_verify.sh.
verify_run() {
  local prep="$1"
  shift
  H=$(hostname -s)
  L="${TMPD}/hps-cc-$H"
  R=$PWD/../test_runs/monitor
  mkdir -p "$L"
  rm -f "$L/prep.log" "$L/verify.log"
  touch "$L/start"
  echo "== 2. verify start $(date -u +%FT%TZ)"
  timeout 1800 "./scripts/${prep}" --debug-build >"$L/prep.log" 2>&1
  PRC=$?
  echo "== prep rc=$PRC"
  test "$PRC" -ne 0 && { tail -n 20 "$L/prep.log"; return 1; }
  env "$@" RPM_BUILD=1 REPORT_DIR="$R" timeout 7200 \
    ./scripts/rpm_debug_shm_verify.sh </dev/null >"$L/verify.log" 2>&1
  VRC=$?
  echo "== verify rc=$VRC end $(date -u +%FT%TZ)"
  tail -n 5 "$L/verify.log" | cut -c1-160
  FR=$(find "$R" -maxdepth 1 -newer "$L/start" \
    -name "fault_inject_rpm_debug_*.txt" | head -n 1)
  P='^(FAIL|PASS|NOTE) fault_inject [a-z_]+[:@!]'
  test -n "$FR" && grep -E "$P" "$FR" | cut -c1-150
  test -n "$FR" && grep '^FAIL' "$FR" | cut -c1-110 | sort \
    | uniq -c | sort -rn | head -n 12
  J=$(find "$R" -maxdepth 1 -newer "$L/start" \
    -name "correctness_${H}_*.json" 2>/dev/null | sort | tail -n 1)
  echo "json=$J"
  test -n "$J" && python3 -c 'import json, sys; d = json.load(
      open(sys.argv[1])); print(d["overall"], d["counts"],
      d["capability_slug"]); print("\n".join("%-8s %-16s %s %s" % (
      c["status"], c["name"], "-" if c["ratio"] is None
      else "%.3f" % c["ratio"], (c["reason"] or "")[:80])
      for c in d["checks"]))' "$J"
  return 0
}

# One nvidia_gpu row set from the debug shm mirror ($1 fast|full), keys filtered by $W. A daemon
# restart unlinks fast/full until that tier's first tick: poll up to 60 s for the file.
snap() {
  local i=0
  while test ! -e "$D/$1" && test "$i" -lt 60; do sleep 1; i=$((i + 1)); done
  awk -v t="@$1" -v w="$W" -v ty="$TY" '
    FNR==NR { if ($1=="!" ty) { n=0
        for (i=2;i<=NF;i++) {
          if (t=="@fast" && $i ~ /,R=S/) continue
          n++; split($i,a,","); k[n]=a[1] } }
      next }
    !h && NF { print t, "ts", $1; h=1; next }
    $1==ty && $3==t { s="dev " $2
      for (j=1;j<=n;j++) if (k[j] ~ w) s=s " " k[j] "=" $(3+j)
      print s }
  ' "$D/schema" "$D/$1" | cut -c1-230
}

dg() {
  timeout 20 dcgmi dmon -e "$1" -c 3 -d 1000 2>&1 \
    | tail -n 2 | cut -c1-150
}

one() {
  echo "== load $1 $(date -u +%T)"
  timeout 260 "$T" --no-dcgm-validation -t "$1" -d 200 \
    >"${TMPD}/hps-gpu-$1.log" 2>&1 &
  BP=$!
  sleep 45; snap fast; snap full
  sleep 31; snap fast; sleep 31; snap fast; snap full
  sleep 31; snap fast
  dg 1002,1003,1004,1006; dg 1008,1013,1014; dg 1007; dg 1015,1016
  wait "$BP"
  echo "tester rc=$?"
  tail -n 3 "${TMPD}/hps-gpu-$1.log" | cut -c1-150
}

find_tester() {
  T=$(command -v dcgmproftester13 dcgmproftester12 | head -n 1)
}

gpu_run() {
  echo "== 3. GPU field response $(date -u +%FT%TZ)"
  GN=$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ')
  test "$GN" -gt 0 || { echo "no NVIDIA GPU: GPU step skipped"; GRC=0; return 0; }
  find_tester
  echo "tester=$T gpus=$GN"
  test -n "$T" || { echo "STOP no dcgmproftester"; return 1; }
  test -r "$D/schema" || { echo "STOP no debug shm"; return 1; }
  nvidia-smi --query-gpu=index,name,driver_version --format=csv
  "$T" --help 2>&1 | grep -E -A3 -- '-t <|-d ' | head -n 12
  echo "== idle"; snap fast; snap full
  one 1006; one 1007; one 1008; one 1004
  echo "== idle after"; sleep 65; snap fast; snap full
  GRC=0
}

tensor_run() {
  echo "== 3. tensor load 1004 $(date -u +%FT%TZ)"
  find_tester
  test -n "$T" || { echo "STOP no dcgmproftester"; return 1; }
  test -r "$D/schema" || { echo "STOP no debug shm"; return 1; }
  timeout 300 "$T" --no-dcgm-validation -t 1004 -d 120 \
    >"${TMPD}/hps-gpu-1004.log" 2>&1 &
  BP=$!
  sleep 70; snap fast; sleep 31; snap fast; snap full
  sleep 31; snap fast
  wait "$BP"
  TRC=$?
  echo "tester rc=$TRC"
}

# $1 header line, $2 KEEP line; erases only when VRC, S2 (GPU / tester) and DRC are all 0.
cleanup() {
  echo "$1"
  OK=0
  test "$VRC" = 0 && test "$S2" = 0 && OK=1
  test "$DRC" = 0 || OK=0
  M="$2"
  test "$OK" = 1 && M="== erasing debug RPM"
  echo "$M"
  test "$OK" = 1 && systemctl stop hpcperfstats
  test "$OK" = 1 && timeout 120 rpm -e hpcperfstatsd
  rpm -q hpcperfstatsd
  systemctl is-active hpcperfstats
  echo "== end $(date -u +%FT%TZ)"
}

run_ls6() {
  prepare_checkout stampede3.force horizon.force
  G=1
  grep -q job_gpu_assign_job_set src/job_shared_emit.c || G=0
  test -x scripts/site_conf.sh || G=0
  CPU=$(lscpu | sed -n 's/^Model name: *//p' | head -n 1)
  GEN=0
  echo "$CPU" | grep -qE 'EPYC 9[0-9]{3}' && GEN=1
  echo "cpu=$CPU genoa=$GEN"
  test "$GEN" = 1 && { grep -q 'genoa.*df_zen5' src/amd_x86_uncore_df.c || G=0; }
  M="== STOP: checkout too old (Genoa needs its commit)"
  test "$G" = 1 && M="== checkout ok"
  echo "$M"
  # EL8 system python3 is 3.6; put a 3.9+ python3 first on PATH.
  P3=$(command -v python3.12 python3.11 python3.9 | head -n 1)
  mkdir -p "${TMPD}/hps-py"
  test -n "$P3" && ln -sf "$P3" "${TMPD}/hps-py/python3"
  echo "python shim=$P3"
  export PATH="${TMPD}/hps-py:$PATH"
  CM=$(compgen -G "$CMR/*/bin" | sort -V | tail -n 1)
  command -v cmake >/dev/null || export PATH="$PATH:$CM"
  echo "cmake=$(command -v cmake)"
  broker_guard ls6
  VRC=x GRC=x DRC=0
  test "$GEN" = 1 && DRC=x
  # The DF step runs whether or not the checkout gate passed.
  test "$GEN" = 1 && test -x scripts/canary_gemm_load.sh && df_run
  test "$G" = 1 && verify_run prepare_rpmbuild_ls6.sh && gpu_run
  S2=$GRC
  cleanup "== 4. cleanup (only if verify, GPU and DF steps ok)" \
    "== KEEP debug RPM: verify=$VRC gpu=$GRC df=$DRC"
}

run_gb200() {
  prepare_checkout stampede3.force ls6.force
  G=1
  grep -q job_gpu_assign_job_set src/job_shared_emit.c || G=0
  grep -q '{10, 0, 128, {128, 256, 512, 8192, 128}}' \
    src/nvidia_gpu_estimate.c || G=0
  test -x scripts/site_conf.sh || G=0
  M="== STOP: GB200 peak change not pulled yet"
  test "$G" = 1 && M="== checkout ok (GB200 peaks)"
  echo "$M"
  broker_guard horizon
  VRC=x TRC=x DRC=0
  test "$G" = 1 && verify_run prepare_rpmbuild_horizon.sh PERF_RAW_FLOPS_CHECK=0 \
    SCF_PERF_CHECK=0 PMU_SHARING_CHECK=0 FP_SLICES_CHECK=0 && tensor_run
  S2=$TRC
  cleanup "== 4. cleanup (only if verify rc=0 and tester rc=0)" \
    "== KEEP debug RPM: verify=$VRC tester=$TRC (rerun needs it)"
}

main() {
  case "${1:-}" in
    ls6 | gb200) ;;
    *)
      echo "usage: $0 ls6|gb200" >&2
      return 2
      ;;
  esac
  cd "$(dirname "${BASH_SOURCE[0]}")/.." || return 1
  if test "$1" = ls6; then run_ls6; else run_gb200; fi
}

main "$@"; exit $?
