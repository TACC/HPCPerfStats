#!/bin/bash
# check-local runs scripts as `$(SHELL) script`; on EL8 /bin/sh is bash 4.4 in posix mode,
# which rejects process substitution (LS6 c313-031: check_unsafe_c_patterns.sh, syntax error
# near `<'). Every such script that uses <(...) or >(...) must `set +o posix` first.
set -euo pipefail
set +o posix
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bad=""
checked=0
while IFS= read -r ref; do
  f="${ref/\$(top_srcdir)/${ROOT}}"
  f="${f/\$(srcdir)/${ROOT}/tests}"
  test -f "${f}" || continue
  checked=$((checked + 1))
  first="$(awk '!/^[[:space:]]*#/ && /<\(|>\(/ { print NR; exit }' "${f}")"
  test -n "${first}" || continue
  off="$(awk '/^set \+o posix/ { print NR; exit }' "${f}")"
  if test -z "${off}" || test "${off}" -gt "${first}"; then
    bad="${bad} ${f#"${ROOT}/"}"
  fi
done < <(sed -n "s/^[[:space:]]*\$(SHELL) '\([^']*\)'.*/\1/p" "${ROOT}/tests/Makefile.am")
test "${checked}" -gt 0 || { echo "no \$(SHELL) scripts found in tests/Makefile.am" >&2; exit 1; }
if test -n "${bad}"; then
  echo "process substitution without an earlier 'set +o posix' (fails under EL8 /bin/sh):${bad}" >&2
  exit 1
fi
echo "test_check_local_posix_safe passed (${checked} scripts)"
