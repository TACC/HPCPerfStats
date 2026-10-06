#!/usr/bin/env bash
# Fail fast if -march=native/-mtune=native are unusable; log Clang target detection.
# Installed at /usr/local/lib/hpcperfstats/clang_march_native_probe.sh (see Dockerfiles).
# GCC builds used: -Q --help=target (not supported by Clang).
set -euo pipefail

: "${CC:=clang}"

"${CC}" --version

if ! printf 'void hpcperfstats_clang_native_probe(void);\n' \
  | "${CC}" -march=native -mtune=native -xc - -fsyntax-only -; then
  echo "clang_march_native_probe: -march=native -mtune=native syntax check FAILED" >&2
  exit 1
fi

echo "clang_march_native_probe: native CPU (from driver -###):"
_log="$(mktemp)"
trap 'rm -f "${_log}"' EXIT
# Log only (-### + -fsyntax-only). Do not -c compile here (spurious failures / pipefail).
printf 'int main(void){return 0;}\n' \
  | "${CC}" -march=native -mtune=native -### -xc - -fsyntax-only - >"${_log}" 2>&1 \
  || echo "clang_march_native_probe: note: -### returned non-zero (log may still be useful)" >&2
grep -Eo '"-target-cpu" "[^"]+"' "${_log}" 2>/dev/null | head -1 || true
grep -Eo '"-tune-cpu" "[^"]+"' "${_log}" 2>/dev/null | head -1 || true
