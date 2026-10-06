#!/usr/bin/env bash
# Fail fast if -march=native/-mtune=native are unusable; log Clang target detection.
# GCC builds used: -Q --help=target (not supported by Clang).
set -euo pipefail

: "${CC:=clang}"

"${CC}" --version
printf 'void hpcperfstats_clang_native_probe(void);\n' \
  | "${CC}" -march=native -mtune=native -xc - -fsyntax-only -
echo "clang_march_native_probe: driver invocation (-###):"
printf 'int main(void){return 0;}\n' \
  | "${CC}" -march=native -mtune=native -### -xc - -c -o /dev/null - 2>&1 | head -30
