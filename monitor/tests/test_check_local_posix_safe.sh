#!/bin/bash
# Shell traps in the scripts check-local runs as `$(SHELL) script`, each seen on a real node:
# - EL8 /bin/sh is bash 4.4 in posix mode, which rejects process substitution (LS6 c313-031:
#   check_unsafe_c_patterns.sh, syntax error near `<'): <(...) / >(...) need `set +o posix`
#   first.
# - Under pipefail, `echo "$x" | grep -q` fails a passing check when grep exits before echo
#   finishes writing (SIGPIPE, 141; Horizon c122-018: test_perf_raw_flops_check); use a
#   here-string.
set -euo pipefail
set +o posix
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
procsub=""
sigpipe=""
checked=0
while IFS= read -r ref; do
  f="${ref/\$(top_srcdir)/${ROOT}}"
  f="${f/\$(srcdir)/${ROOT}/tests}"
  test -f "${f}" || continue
  checked=$((checked + 1))
  rel="${f#"${ROOT}/"}"
  first="$(awk '!/^[[:space:]]*#/ && /<\(|>\(/ { print NR; exit }' "${f}")"
  off="$(awk '/^set \+o posix/ { print NR; exit }' "${f}")"
  if test -n "${first}" && { test -z "${off}" || test "${off}" -gt "${first}"; }; then
    procsub="${procsub} ${rel}"
  fi
  if grep -q 'pipefail' "${f}" \
    && awk '!/^[[:space:]]*#/ && /echo "[^"]*" \| grep -q/ { hit = 1 } END { exit !hit }' "${f}"; then
    sigpipe="${sigpipe} ${rel}"
  fi
done < <(sed -n "s/^[[:space:]]*\$(SHELL) '\([^']*\)'.*/\1/p" "${ROOT}/tests/Makefile.am")
test "${checked}" -gt 0 || { echo "no \$(SHELL) scripts found in tests/Makefile.am" >&2; exit 1; }
rc=0
if test -n "${procsub}"; then
  echo "process substitution without an earlier 'set +o posix' (fails under EL8 /bin/sh):${procsub}" >&2
  rc=1
fi
if test -n "${sigpipe}"; then
  echo "echo | grep -q under pipefail (SIGPIPE fails passing checks; use grep -q ... <<<\"\$x\"):${sigpipe}" >&2
  rc=1
fi
test "${rc}" = 0 || exit 1
echo "test_check_local_posix_safe passed (${checked} scripts)"
