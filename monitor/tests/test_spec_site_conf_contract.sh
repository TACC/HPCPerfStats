#!/usr/bin/env bash
# Packaging contract for the site override file and the fleet release:
#   - %files keeps the installed conf with %config(noreplace) (mode 0600 in %install), so an
#     upgrade never overwrites a node's edited /etc/hpcperfstats/hpcperfstats.conf;
#   - Release is %{?hpc_release}%{!?hpc_release:1}%{?dist} (committed baseline 1, one-off
#     builds use --define "hpc_release N");
#   - %build lays site/hpcperfstats.site.conf over src/hpcperfstats.conf (scripts/site_conf.sh);
#   - dist-hook embeds the site file only when present; the script and example are distributed;
#   - prepare runs the fleet broker guard before any build step and passes HPCS_RELEASE through;
#   - the site file is gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "${ROOT}/.." && pwd)"
SPEC="${ROOT}/hpcperfstats.spec"
PREP="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"
VERIFY="${ROOT}/scripts/rpm_debug_shm_verify.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

grep -qxF '%config(noreplace) %{_sysconfdir}/hpcperfstats/hpcperfstats.conf' "${SPEC}" \
  || fail "spec %files must list %config(noreplace) %{_sysconfdir}/hpcperfstats/hpcperfstats.conf"
if grep -qxF '%{_sysconfdir}/hpcperfstats/hpcperfstats.conf' "${SPEC}"; then
  fail "spec %files still lists the conf as a plain file"
fi
grep -qE '^install -m 0600 src/hpcperfstats.conf %\{buildroot\}%\{_sysconfdir\}/hpcperfstats/hpcperfstats.conf$' "${SPEC}" \
  || fail "spec %install must install the conf with mode 0600"

grep -qxF 'Release: %{?hpc_release}%{!?hpc_release:1}%{?dist}' "${SPEC}" \
  || fail "spec Release must be %{?hpc_release}%{!?hpc_release:1}%{?dist}"

grep -qF './scripts/site_conf.sh overlay src/hpcperfstats.conf site/hpcperfstats.site.conf src/hpcperfstats.conf' "${SPEC}" \
  || fail "spec %build must apply the site overlay to src/hpcperfstats.conf"
# Overlay before the debug cadence seds, so a debug RPM keeps sample_freq 30 / 60.
overlay_line="$(grep -n 'scripts/site_conf.sh overlay' "${SPEC}" | head -1 | cut -d: -f1)"
debug_sed_line="$(grep -n "s/^sample_freq " "${SPEC}" | head -1 | cut -d: -f1)"
test "${overlay_line}" -lt "${debug_sed_line}" \
  || fail "spec site overlay must run before the debug sample_freq sed"

grep -qF 'site/hpcperfstats.site.conf' "${ROOT}/Makefile.am" \
  || fail "Makefile.am dist-hook must embed site/hpcperfstats.site.conf when present"
grep -qF 'scripts/site_conf.sh' "${ROOT}/src/Makefile.am" \
  || fail "src/Makefile.am EXTRA_DIST must list scripts/site_conf.sh"
grep -qF 'site/hpcperfstats.site.conf.example' "${ROOT}/src/Makefile.am" \
  || fail "src/Makefile.am EXTRA_DIST must list site/hpcperfstats.site.conf.example"
test -f "${ROOT}/site/hpcperfstats.site.conf.example" \
  || fail "missing site/hpcperfstats.site.conf.example"
if grep -qiE '^password[[:space:]:=]+guest$' "${ROOT}/site/hpcperfstats.site.conf.example"; then
  fail "example must use placeholders, not the guest default"
fi

grep -qF 'scripts/site_conf.sh' "${ROOT}/scripts/lib/monitor_tree_clean.sh" \
  || fail "verify_dist_tarball_build_scripts must require scripts/site_conf.sh in the tarball"

guard_line="$(grep -n 'site_conf.sh" fleet-guard' "${PREP}" | head -1 | cut -d: -f1)"
test -n "${guard_line}" || fail "prepare_rpmbuild_dirs.sh must run scripts/site_conf.sh fleet-guard"
deps_line="$(grep -nF '&& ./scripts/build_static_bundle.sh --deps-only)' "${PREP}" | head -1 | cut -d: -f1)"
test -n "${deps_line}" || fail "prepare_rpmbuild_dirs.sh static deps build line not found"
test "${guard_line}" -lt "${deps_line}" \
  || fail "fleet guard must run before the static deps build"

# shellcheck disable=SC2016  # literal $ text matched in script source
grep -qF 'hpc_release ${HPCS_RELEASE}' "${PREP}" \
  || fail "prepare footer must pass --define hpc_release when HPCS_RELEASE is set"
# A Release that could break the printed (and --measure-overhead eval'd) rpmbuild line is
# refused before any build step.
if out="$(env -u HPCS_BUNDLE_FLEET HPCS_RELEASE='2;x' "${PREP}" 2>&1)"; then
  fail "prepare accepted HPCS_RELEASE='2;x'"
fi
printf '%s\n' "${out}" | grep -q 'HPCS_RELEASE must start with a digit' \
  || fail "prepare HPCS_RELEASE rejection message missing: ${out}"
# shellcheck disable=SC2016  # literal $ text matched in script source
grep -qF 'hpc_release ${HPCS_RELEASE}' "${VERIFY}" \
  || fail "rpm_debug_shm_verify.sh RPM_BUILD must pass --define hpc_release when HPCS_RELEASE is set"

if test -f "${REPO}/.gitignore"; then
  grep -qxF 'monitor/site/hpcperfstats.site.conf' "${REPO}/.gitignore" \
    || fail "repo .gitignore must ignore monitor/site/hpcperfstats.site.conf"
fi

# rpmspec resolves both Release forms (skipped where rpm-build is absent).
if command -v rpmspec >/dev/null 2>&1; then
  rel="$(rpmspec -q --srpm --queryformat '%{release}\n' --define 'dist %{nil}' "${SPEC}")"
  test "${rel}" = "1" || fail "default Release resolved to '${rel}', want 1"
  rel="$(rpmspec -q --srpm --queryformat '%{release}\n' --define 'dist %{nil}' \
    --define 'hpc_release 7' "${SPEC}")"
  test "${rel}" = "7" || fail "hpc_release 7 resolved to '${rel}', want 7"
fi

echo "test_spec_site_conf_contract.sh passed"
