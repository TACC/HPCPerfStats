#!/bin/sh
# prepare_rpmbuild_dirs.sh --measure-overhead: flag parsing, fail-fast before any build step,
# harness argument passthrough, and an unchanged default footer.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
PREP=./scripts/prepare_rpmbuild_dirs.sh
BUILD_MARK="Removing any prior"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/hps_measure_flag.XXXXXX")"
trap 'rm -rf "${tmpdir}"' EXIT INT HUP TERM

fail() { echo "test_prepare_rpmbuild_measure_flag: $*" >&2; exit 1; }

# 1. --measure-overhead and --debug-build are mutually exclusive (exit 1, before building).
set +e
out="$(bash "${PREP}" --measure-overhead --debug-build 2>&1)"; rc=$?
set -e
test "${rc}" -eq 1 || fail "exclusive flags: expected exit 1, got ${rc}"
echo "${out}" | grep -q 'cannot be combined with --debug-build' || fail "exclusive flags message missing"
echo "${out}" | grep -q "${BUILD_MARK}" && fail "exclusive flags reached the build"

set +e
out="$(bash "${PREP}" --measure-arg --blocks 2>&1)"; rc=$?
set -e
test "${rc}" -eq 1 || fail "--measure-arg without --measure-overhead must exit 1"

# A PATH with only the basic tools the early part of the script needs, plus stubs.
mkdir -p "${tmpdir}/bin"
for t in bash sh dirname uname id cat grep sed head mktemp rm env printf true ls; do
  p="$(command -v "${t}" 2>/dev/null || true)"
  test -n "${p}" && ln -sf "${p}" "${tmpdir}/bin/${t}"
done
for t in gcc make autoreconf curl; do
  printf '#!/bin/sh\nexit 0\n' > "${tmpdir}/bin/${t}"
  chmod +x "${tmpdir}/bin/${t}"
done

# 2. sudo that wants a password: non-root fails before building and prints the root command.
if test "$(id -u)" != "0"; then
  printf '#!/bin/sh\necho "sudo: a password is required" >&2\nexit 1\n' > "${tmpdir}/bin/sudo"
  chmod +x "${tmpdir}/bin/sudo"
  set +e
  out="$(PATH="${tmpdir}/bin" bash "${PREP}" --measure-overhead --measure-arg --blocks --measure-arg 1 2>&1)"; rc=$?
  set -e
  test "${rc}" -ne 0 || fail "prompting sudo must fail"
  echo "${out}" | grep -q 'sudo would prompt' || fail "prompting sudo message missing: ${out}"
  echo "${out}" | grep -q 'run_overhead_profile.sh --rpm .* --blocks 1' || fail "root command not printed: ${out}"
  echo "${out}" | grep -q "${BUILD_MARK}" && fail "prompting sudo reached the build"
fi

# 3. Passwordless sudo but rpmbuild missing: fail before building and name the tool.
printf '#!/bin/sh\nexit 0\n' > "${tmpdir}/bin/sudo"
chmod +x "${tmpdir}/bin/sudo"
set +e
out="$(PATH="${tmpdir}/bin" bash "${PREP}" --measure-overhead 2>&1)"; rc=$?
set -e
test "${rc}" -ne 0 || fail "missing rpmbuild must fail"
echo "${out}" | grep -q 'missing build prerequisites: rpmbuild' || fail "missing rpmbuild not named: ${out}"
echo "${out}" | grep -q "${BUILD_MARK}" && fail "missing rpmbuild reached the build"

# 4. --measure-arg values reach the harness command; RPM discovery needs exactly one RPM.
# shellcheck source=../scripts/lib/measure_overhead.sh
cmd="$(bash -c '. ./scripts/lib/measure_overhead.sh; measure_overhead_command /m /r/x.rpm /r/build-info --max-hours 8 "two words"')"
echo "${cmd}" | grep -q '/m/measurement/bin/run_overhead_profile.sh --rpm /r/x.rpm --build-info /r/build-info --max-hours 8 two\\ words' \
  || fail "harness command wrong: ${cmd}"
if test "$(id -u)" != "0"; then
  echo "${cmd}" | grep -q '^sudo -n ' || fail "non-root harness command must use sudo -n: ${cmd}"
fi
arch="$(uname -m)"
mkdir -p "${tmpdir}/top/RPMS/${arch}"
set +e
bash -c ". ./scripts/lib/measure_overhead.sh; measure_overhead_find_rpm '${tmpdir}/top' 3.0" >/dev/null 2>&1
rc=$?
set -e
test "${rc}" -ne 0 || fail "zero RPMs must fail"
: > "${tmpdir}/top/RPMS/${arch}/hpcperfstatsd-3.0-7.el9.${arch}.rpm"
found="$(bash -c ". ./scripts/lib/measure_overhead.sh; measure_overhead_find_rpm '${tmpdir}/top' 3.0")"
test "${found}" = "${tmpdir}/top/RPMS/${arch}/hpcperfstatsd-3.0-7.el9.${arch}.rpm" || fail "find_rpm: ${found}"
: > "${tmpdir}/top/RPMS/${arch}/hpcperfstatsd-3.0-8.el9.${arch}.rpm"
set +e
bash -c ". ./scripts/lib/measure_overhead.sh; measure_overhead_find_rpm '${tmpdir}/top' 3.0" >/dev/null 2>&1
rc=$?
set -e
test "${rc}" -ne 0 || fail "two RPMs must fail"

# 5. Default (no flag) footer is unchanged.
grep -q '^  echo "Build binary and source RPMs with:"$' "${PREP}" || fail "release footer header changed"
grep -q '^  echo "  ${release_rpmbuild}"$' "${PREP}" || fail "release footer command changed"
grep -q '^release_rpmbuild="rpmbuild -ba --define \\"_topdir ${topdir}\\" \\"${specs_dir}/hpcperfstats.spec\\""$' "${PREP}" \
  || fail "release rpmbuild string changed"
grep -q 'eval "env ${measure_caller_env}${release_rpmbuild}"' "${PREP}" \
  || fail "--measure-overhead must run the printed release rpmbuild under the caller's environment"

# 6. c122-018 regression: --measure-overhead ran rpmbuild with prepare's exported LIBS, which put
# -lm inside %build's -Wl,-Bstatic group (ld: cannot find -lm). rpmbuild must see the caller's
# values: set ones restored (spaces intact), unset ones removed.
got="$(LIBS='-lcaller x' bash -c '
  unset CPPFLAGS PREFIX
  . ./scripts/lib/measure_overhead.sh
  e="$(measure_overhead_caller_env)"
  export LIBS="-Wl,--start-group -llikwid -Wl,--end-group -lm" CPPFLAGS=-Ileaked PREFIX=/leaked
  eval "env ${e}sh -c '\''printf \"%s|%s|%s\" \"\$LIBS\" \"\${CPPFLAGS-unset}\" \"\${PREFIX-unset}\"'\''"')"
test "${got}" = "-lcaller x|unset|unset" || fail "caller env not restored for rpmbuild: ${got}"
# Every variable prepare exports must be in the restore list.
for v in $(sed -n 's/^export \([A-Z_][A-Z0-9_]*\)=.*/\1/p' "${PREP}"); do
  bash -c ". ./scripts/lib/measure_overhead.sh; case \" \${MEASURE_OVERHEAD_PREPARE_ENV} \" in *\" ${v} \"*) ;; *) exit 1;; esac" \
    || fail "prepare exports ${v} but MEASURE_OVERHEAD_PREPARE_ENV does not restore it"
done

echo "test_prepare_rpmbuild_measure_flag passed"
