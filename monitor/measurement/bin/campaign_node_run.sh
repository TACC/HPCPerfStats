#!/usr/bin/env bash
# One campaign node run: gates, fleet release RPM build, harness-built conf, detached launch.
#
#   measurement/bin/campaign_node_run.sh stampede3|ls6|vista|horizon     (root, any cwd)
#
# Replaces the long operator paste: a paste over 4 KB loses lines while a command runs
# (c313-031 2026-11-23: the tty buffer dropped the allocation check). Every gate runs before
# the build where it can; any STOP means nothing was installed or launched. Campaign settings
# are fixed here (monitor-overhead-measurement.mdc law 16); only the broker keys are per site.
# HPCS_ETC_CONF / HPCS_SYSCONFIG / HPCS_DROPIN_DIR override system paths for the tests.
set -u

CLUSTER="${1:-}"
case "${CLUSTER}" in
  stampede3 | ls6 | horizon) PREP="prepare_rpmbuild_${CLUSTER}.sh" ;;
  vista) PREP="prepare_rpmbuild_dirs.sh" ;;
  *)
    echo "usage: $0 stampede3|ls6|vista|horizon" >&2
    exit 2
    ;;
esac

MONITOR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${MONITOR}" || exit 1
N="$(hostname -s)"
SITE=site/hpcperfstats.site.conf
ETC="${HPCS_ETC_CONF:-/etc/hpcperfstats/hpcperfstats.conf}"
SC="${HPCS_SYSCONFIG:-/etc/sysconfig/hpcperfstatsd}"
DI="${HPCS_DROPIN_DIR:-/etc/systemd/system/hpcperfstats.service.d}"
L="${MONITOR}/../test_runs/overhead"
D="${HOME}/hps_campaign"
C="${D}/measure.conf"
QF='%{VERSION}-%{RELEASE}'
OK=1
mkdir -p "${L}" "${D}/py"

stop() {
  OK=0
  echo "STOP: $*"
}

pkill -TERM -f overhead_profile.py
while pgrep -f overhead_profile.py >/dev/null; do sleep 5; done
unset HPCS_BUNDLE_FLEET LIBS CPPFLAGS LDFLAGS CFLAGS CC

case "$(uname -m)" in
  x86_64) BACKEND=x86 MIN_HOURS=16 ;;
  aarch64) BACKEND=arm MIN_HOURS=19 ;;
  *) stop "unsupported arch $(uname -m)" ;;
esac

# Harness and build scripts need Python 3.9+; LS6 (EL8) system python3 is 3.6.
P3=""
for p in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  cand="$(command -v "${p}")" || continue
  "${cand}" -c 'import sys; sys.exit(sys.version_info < (3, 9))' && P3="${cand}" && break
done
test -n "${P3}" || stop "no python 3.9+"
ln -sf "${P3}" "${D}/py/python3"
export PATH="${D}/py:${PATH}" HPCPERFSTATS_PYTHON="${P3}"
CM="$(printf '%s\n' /opt/apps/cmake/*/bin | sort -V | tail -1)"
command -v cmake >/dev/null || export PATH="${PATH}:${CM}"
for t in gcc make autoreconf rpmbuild cmake; do
  command -v "${t}" >/dev/null || stop "no ${t}"
done
echo "cluster=${CLUSTER} host=${N} arch=$(uname -m) cpus=$(nproc) python=${P3}"

# Broker keys: the site conf unless it is the repo-default localhost, then the node's conf.
SRC="${SITE}"
declare -A B
case "$(awk '$1=="server"{print $2; exit}' "${SITE}" 2>/dev/null)" in
  "" | localhost | 127.*) SRC="${ETC}" ;;
esac
for k in server queue user password; do
  v="$(awk -v k="${k}" '$1==k{print $2; exit}' "${SRC}" 2>/dev/null)"
  test -n "${v}" || stop "no ${k} in ${SRC}"
  B[${k}]="${v}"
done
case "${B[server]}" in localhost | 127.*) stop "${SRC} broker is ${B[server]}" ;; esac
echo "broker=${B[server]} queue=${B[queue]} from ${SRC}"

# Leftovers from fault injection or a manual unit override change what is measured.
E='^[[:space:]]*(#|$)'
if grep -qvE "${E}" "${SC}" 2>/dev/null; then
  stop "${SC} has settings:"
  grep -vE "${E}" "${SC}"
fi
if ls "${DI}"/* >/dev/null 2>&1; then
  stop "unit drop-ins in ${DI}:"
  ls "${DI}"
fi
# xpum.service's 60 s start wait eats every leg's settle; only Intel GPU nodes need it.
if test "$(systemctl is-active xpum 2>/dev/null)" = active \
  && ! lspci -d 8086::0380 2>/dev/null | grep -q .; then
  stop "xpum.service active on a node without an Intel GPU"
fi
FREE="$(df -Pk . | awk 'NR==2{print int($4/1048576)}')"
test "${FREE}" -ge 20 || stop "${FREE} GB free here, need 20"

# Cluster manager pushes its RPM over an older or missing one (law 19): build above it.
INST="$(rpm -q --qf "${QF}" hpcperfstatsd 2>/dev/null)" || INST=""
EXP="3.1-${HPCS_RELEASE:-1}$(rpm --eval '%{?dist}')"
echo "installed=${INST:-none} will build=${EXP}"
if test -n "${INST}" \
  && test "$(printf '%s\n%s\n' "${EXP}" "${INST}" | sort -V | tail -1)" = "${INST}"; then
  stop "installed ${INST} sorts >= ${EXP}: rerun with HPCS_RELEASE=<higher>"
fi

# HPCS_ALLOC_LEFT (squeue %L, e.g. 23:10:00) when squeue on the node does not list the job
# (c122-018 2026-11-23). Fields are read right to left, so an empty or short value is 0 or
# seconds, never a field -1 read.
LEFT="${HPCS_ALLOC_LEFT:-$(squeue -h -w "${N}" -o %L | head -1)}"
SECS="$(echo "${LEFT}" | awk -F'[-:]' '{split("1 60 3600 86400", m, " ")
  for (i = NF; i >= 1 && NF - i < 4; i--) s += $i * m[NF - i + 1]} END {print s + 0}')"
echo "allocation left: ${LEFT:-unknown} (${SECS} s)"
test "${SECS}" -ge $((MIN_HOURS * 3600)) || stop "need >= ${MIN_HOURS} h left"

if test "${OK}" = 1; then
  rm -f scripts/fleet/*.force
  test -f Makefile && make distclean >/dev/null 2>&1
  ./scripts/build_static_bundle.sh >"${L}/${N}_31_bundle.log" 2>&1
  echo "bundle rc=$?"
  make -C .build-static check >"${L}/${N}_31_check.log" 2>&1
  CRC=$?
  echo "check rc=${CRC}"
  grep -E '^# (TOTAL|PASS|SKIP|FAIL)' "${L}/${N}_31_check.log"
  grep -E '^(FAIL|ERROR):' "${L}/${N}_31_check.log"
  # check-local scripts fail without a FAIL: line (c313-031), so show the log's end.
  if test "${CRC}" != 0; then
    stop "make check failed (${L}/${N}_31_check.log), last lines:"
    tail -12 "${L}/${N}_31_check.log"
  fi
  make -C .build-static distclean >/dev/null 2>&1
fi
if test "${OK}" = 1; then
  rm -rf rpmbuild
  # The harness installs its own conf, so the packaged localhost broker is irrelevant.
  if HPCS_ALLOW_DEFAULT_BROKER=1 "./scripts/${PREP}" >"${L}/${N}_31_prepare.log" 2>&1; then
    rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" "${PWD}/rpmbuild/SPECS/hpcperfstats.spec" \
      >"${L}/${N}_31_rpmbuild.log" 2>&1
    echo "rpmbuild rc=$?"
  else
    stop "${PREP} failed (${L}/${N}_31_prepare.log)"
  fi
fi
if test "${OK}" = 1; then
  # The glob cannot match hpcperfstatsd-debuginfo-*.
  RPM="$(compgen -G 'rpmbuild/RPMS/*/hpcperfstatsd-3.1-*.rpm' | head -1)"
  test -n "${RPM}" || stop "no 3.1 release RPM"
  NT="$(compgen -G 'rpmbuild/SOURCES/hpcperfstats-*.tar.gz' | wc -l)"
  test "${NT}" = 1 || stop "${NT} dist tarballs in rpmbuild/SOURCES"
  grep -q "\"cpu_backend\": *\"${BACKEND}\"" rpmbuild/build-info/monitor-build-capabilities.json \
    2>/dev/null || stop "build capabilities are not cpu_backend ${BACKEND}"
  echo "new=$(rpm -qp --qf "${QF}" "${RPM}" 2>/dev/null) rpm=${RPM}"
fi
if test "${OK}" = 1; then
  (
    umask 077
    printf '%s %s\n' server "${B[server]}" queue "${B[queue]}" port 5672 \
      user "${B[user]}" password "${B[password]}" buffer_hours 6 sample_freq 30 \
      sample_freq_slow 600 send_freq 300 enable_slow_tier 1 emit_value_sentinels 1 >"${C}"
  )
  sed 's/^password .*/password REDACTED/' "${C}"
  OUT="measurement/results/v31_${N}_$(date -u +%Y%m%dT%H%MZ)"
  setsid nohup timeout --signal=TERM $((SECS - 1800)) ./measurement/bin/run_overhead_profile.sh \
    --governors performance --profile-collectors --leg-seconds 1800 --settle-seconds 60 \
    --fwq-seconds 300 --blocks 3 --max-hours 20 --cadence-sweep 60 \
    --allocation-user sharrell --conf "${C}" --rpm "${RPM}" \
    --build-info rpmbuild/build-info --out "${OUT}" >"${L}/v31_${N}.log" 2>&1 </dev/null &
  echo "launched out=${OUT} log=${L}/v31_${N}.log; stops by $((SECS - 1800)) s"
  sleep 90
  pgrep -af overhead_profile | head -2
  tail -4 "${L}/v31_${N}.log" | cut -c1-150
else
  echo "NOT launched"
  exit 1
fi
