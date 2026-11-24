#!/usr/bin/env bash
# After a node's campaign run: what holds perf counters, and what CPU counter rotation costs
# without the daemon (the counterfactual for the campaign's counter_rotation numbers).
#
#   measurement/bin/counter_rotation_check.sh        (root, after the run finished)
#
# Records every process holding perf_event fds and nmi_watchdog, then measures node interrupts
# and interrupt time for HPCS_CHECK_SECONDS (default 60) each: no events; a core event set that
# fits the CPU class's general counters; the monitor's own set, which needs more counters than
# that (c573-002 2026-11-24: rotation 1,000/s per CPU at 3.0 us each). Read-only: perf stat only.
# HPCS_CPUINFO / HPCS_PROC_STAT override /proc paths for the tests.
set -u
SECS="${HPCS_CHECK_SECONDS:-60}"
CPUINFO="${HPCS_CPUINFO:-/proc/cpuinfo}"
STAT="${HPCS_PROC_STAT:-/proc/stat}"
RESULTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results"
OUT="${RESULTS}/counter_rotation_$(hostname -s)_$(date -u +%Y%m%dT%H%MZ).txt"

command -v perf >/dev/null || {
  echo "STOP: perf not installed"
  exit 1
}
if pgrep -x hpcperfstatsd >/dev/null || pgrep -f '[o]verhead_profile.py' >/dev/null; then
  echo "STOP: hpcperfstatsd or the harness is running; this measures the node without them"
  echo "      (after the run: systemctl stop hpcperfstats, run this, systemctl start hpcperfstats)"
  exit 1
fi

field() { awk -F': *' -v k="$1" '$1 ~ "^"k"[ \t]*$" {print $2; exit}' "${CPUINFO}"; }
VENDOR="$(field vendor_id)"
FAMILY="$(field 'cpu family')"
PART="$(field 'CPU part')"
# FIT fits the general counters with nothing else holding one; MONITOR is the monitor's set
# (x86_core_perf.c / arm_core_perf.c tables). AMD event 0x03 takes a merged counter pair.
case "${VENDOR}/${FAMILY}/${PART}" in
  AuthenticAMD/25/*)
    CLASS="AMD Zen3/Zen4, 6 general counters"
    FIT="cycles,instructions,r00c2,r00c3,rff03"
    MONITOR="${FIT},r0729"
    SLOTS="fit 6 slots, monitor 7"
    ;;
  AuthenticAMD/26/*)
    CLASS="AMD Zen5, 6 general counters"
    FIT="cycles,instructions,r00c2,r00c3,r0f03"
    MONITOR="cycles,instructions,r00c2,r00c3,r0729,r0f03,raf03,r8f03"
    SLOTS="fit 6 slots, monitor 11"
    ;;
  GenuineIntel/6/*)
    CLASS="Intel server, fixed instructions/cycles/ref-cycles + 8 general counters (HT off)"
    FIT="instructions,cycles,ref-cycles,r01c7,r02c7,r04c7,r08c7,r10c7,r20c7,r40c7,r80c7"
    MONITOR="${FIT},r0151,r01d1,r02d1,r04d1"
    SLOTS="fit 8 general slots, monitor 12"
    ;;
  */*/0xd4f)
    CLASS="Grace Neoverse V2, cycle counter + 6 general counters"
    FIT="cycles,r08,r801c,r8018,r8014,r80c1,r80c0"
    MONITOR="${FIT},r80e3,r80e7"
    SLOTS="fit 6 general slots, monitor 8"
    ;;
  *)
    echo "STOP: no event sets for vendor '${VENDOR}' family '${FAMILY}' part '${PART}'"
    exit 1
    ;;
esac

mkdir -p "${RESULTS}"
TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

# intr total and irq+softirq jiffies from /proc/stat.
snap() { awk '/^intr /{i=$2} /^cpu /{j=$7+$8} END{print i, j}' "${STAT}"; }

measure() { # measure <label> <events or "none">
  local a b
  read -r -a a <<<"$(snap)"
  if test "$2" = none; then
    sleep "${SECS}"
  else
    : >"${TMP}"
    perf stat -a -x, -e "$2" -o "${TMP}" -- sleep "${SECS}" >/dev/null 2>&1
  fi
  read -r -a b <<<"$(snap)"
  awk -v l="$1" -v i=$((b[0] - a[0])) -v j=$((b[1] - a[1])) -v s="${SECS}" -v n="$(nproc)" \
    'BEGIN { printf "%-8s interrupts/s %10.0f  per CPU %7.1f  irq cores %.4f\n",
             l, i / s, i / s / n, j / 100 / s }'
  if test "$2" != none; then
    awk -F, 'NF > 4 && $1 !~ /^#/ { printf "         %-14s running %s%%\n", $3, $5 }' "${TMP}"
  fi
}

{
  echo "host $(hostname -s)  $(date -u +%FT%TZ)  kernel $(uname -r)  CPUs $(nproc)"
  echo "class: ${CLASS} (${SLOTS})"
  echo "nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog 2>/dev/null || echo unknown)"
  echo "other perf_event holders (process fds):"
  for p in /proc/[0-9]*; do
    n=$(find "$p/fd" -maxdepth 1 -lname 'anon_inode:\[perf_event\]' 2>/dev/null | wc -l)
    test "$n" -gt 0 && echo "  $(cat "$p/comm" 2>/dev/null) $n"
  done | sort
  echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo none)"
  measure none none
  measure fit "${FIT}"
  measure monitor "${MONITOR}"
} | tee "${OUT}"
echo "saved ${OUT}"
