#!/usr/bin/env bash
# scripts/site_conf.sh: the site override overlay (RPM %build) and the fleet broker guard
# (prepare_rpmbuild_dirs.sh). Credentials must never reach stdout or stderr.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/scripts/site_conf.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/test_site_conf.XXXXXX")"
trap 'rm -rf "${TMP}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test -x "${SCRIPT}" || fail "missing executable ${SCRIPT}"

cat >"${TMP}/default.conf" <<'EOF'
#
# header comment
#

# server: RabbitMQ host
server localhost

# queue: queue name
queue  default
port   5672
user guest
password guest
sample_freq 30
EOF

# No site file: output is byte-identical to the default.
"${SCRIPT}" overlay "${TMP}/default.conf" "${TMP}/absent.conf" "${TMP}/out0.conf" \
  >"${TMP}/log0" 2>&1 || fail "overlay without site file failed"
cmp -s "${TMP}/default.conf" "${TMP}/out0.conf" || fail "no-site output differs from default"

# Site file: replace existing keys, append absent ones, last duplicate wins, separators
# ' ', ':' and '=' as the daemon parser accepts, a value with spaces kept whole.
cat >"${TMP}/site.conf" <<'EOF'
# site overrides
server broker.example.org

queue: stampede3
user=svc_monitor
password S3cr3t-Pa55 with spaces
jobid_file /var/run/site_jobid
jobid_file /var/run/site_jobid_last
EOF

"${SCRIPT}" overlay "${TMP}/default.conf" "${TMP}/site.conf" "${TMP}/out1.conf" \
  >"${TMP}/log1" 2>&1 || fail "overlay with site file failed"

cat >"${TMP}/expect1.conf" <<'EOF'
#
# header comment
#

# server: RabbitMQ host
server broker.example.org

# queue: queue name
queue stampede3
port   5672
user svc_monitor
password S3cr3t-Pa55 with spaces
sample_freq 30
jobid_file /var/run/site_jobid_last
EOF
if ! cmp -s "${TMP}/expect1.conf" "${TMP}/out1.conf"; then
  diff -u "${TMP}/expect1.conf" "${TMP}/out1.conf" >&2 || true
  fail "overlay output mismatch"
fi

# Output file is private (it holds broker credentials).
mode="$(stat -c '%a' "${TMP}/out1.conf")"
test "${mode}" = "600" || fail "overlay output mode ${mode}, want 600"

# In place (spec %build: default and output are the same path).
cp "${TMP}/default.conf" "${TMP}/inplace.conf"
"${SCRIPT}" overlay "${TMP}/inplace.conf" "${TMP}/site.conf" "${TMP}/inplace.conf" \
  >"${TMP}/log2" 2>&1 || fail "in-place overlay failed"
cmp -s "${TMP}/expect1.conf" "${TMP}/inplace.conf" || fail "in-place overlay mismatch"

# Credentials never echoed by overlay.
for log in "${TMP}/log0" "${TMP}/log1" "${TMP}/log2"; do
  if grep -q -e 'S3cr3t' -e 'svc_monitor' "${log}"; then
    fail "overlay printed a credential: $(cat "${log}")"
  fi
done

# Missing default conf is an error naming the path, not a silent empty file.
if "${SCRIPT}" overlay "${TMP}/nope.conf" "${TMP}/site.conf" "${TMP}/out3.conf" 2>"${TMP}/log3"; then
  fail "overlay accepted a missing default conf"
fi
grep -q 'nope.conf' "${TMP}/log3" || fail "missing-default error does not name the path"

# Fleet guard. Fake monitor tree: src/hpcperfstats.conf, optional site file, optional force marker.
mk_tree() {
  local dir="$1"
  mkdir -p "${dir}/src" "${dir}/site" "${dir}/scripts/fleet"
  cp "${TMP}/default.conf" "${dir}/src/hpcperfstats.conf"
}

guard() {
  # guard <tree> [env assignments...]; stdout+stderr to ${TMP}/guard.log
  local tree="$1"
  shift
  env -u HPCS_BUNDLE_FLEET -u HPCS_ALLOW_DEFAULT_BROKER "$@" \
    "${SCRIPT}" fleet-guard "${tree}" >"${TMP}/guard.log" 2>&1
}

# Non-fleet dev build without a site file: ok.
mk_tree "${TMP}/t_dev"
guard "${TMP}/t_dev" || fail "non-fleet build refused: $(cat "${TMP}/guard.log")"

# Fleet via env + repo default localhost: refused with a clear message.
mk_tree "${TMP}/t_env"
if guard "${TMP}/t_env" HPCS_BUNDLE_FLEET=stampede3; then
  fail "fleet build with server localhost was accepted"
fi
grep -q 'stampede3' "${TMP}/guard.log" || fail "guard message does not name the fleet"
grep -q 'site/hpcperfstats.site.conf' "${TMP}/guard.log" || fail "guard message does not name the site file"
grep -q 'HPCS_ALLOW_DEFAULT_BROKER=1' "${TMP}/guard.log" || fail "guard message does not name the opt-out"

# Fleet via a force marker only (stale marker counts: dist-hook would embed it).
mk_tree "${TMP}/t_force"
: >"${TMP}/t_force/scripts/fleet/ls6.force"
if guard "${TMP}/t_force"; then
  fail "fleet (ls6.force) build with server localhost was accepted"
fi
grep -q 'ls6' "${TMP}/guard.log" || fail "guard message does not name the ls6 fleet"

# Loopback address and an empty server value are refused too.
for bad in 'server 127.0.0.1' 'server' 'server LocalHost'; do
  mk_tree "${TMP}/t_bad"
  printf '%s\n' "${bad}" >"${TMP}/t_bad/site/hpcperfstats.site.conf"
  if guard "${TMP}/t_bad" HPCS_BUNDLE_FLEET=horizon; then
    fail "fleet build accepted site line '${bad}'"
  fi
  rm -rf "${TMP}/t_bad"
done

# Fleet + site server: ok, and the credentials in the site file are not printed.
mk_tree "${TMP}/t_site"
cp "${TMP}/site.conf" "${TMP}/t_site/site/hpcperfstats.site.conf"
guard "${TMP}/t_site" HPCS_BUNDLE_FLEET=stampede3 \
  || fail "fleet build with a site server refused: $(cat "${TMP}/guard.log")"
if grep -q -e 'S3cr3t' -e 'svc_monitor' "${TMP}/guard.log"; then
  fail "guard printed a credential: $(cat "${TMP}/guard.log")"
fi

# Opt-out: fleet + localhost passes with a warning.
guard "${TMP}/t_env" HPCS_BUNDLE_FLEET=stampede3 HPCS_ALLOW_DEFAULT_BROKER=1 \
  || fail "HPCS_ALLOW_DEFAULT_BROKER=1 did not bypass the guard"
grep -q -i 'warn' "${TMP}/guard.log" || fail "opt-out did not warn"

# Unknown subcommand: usage error.
if "${SCRIPT}" bogus >/dev/null 2>&1; then
  fail "unknown subcommand accepted"
fi

echo "test_site_conf.sh passed"
