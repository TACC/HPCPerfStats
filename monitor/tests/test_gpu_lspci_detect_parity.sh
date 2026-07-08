#!/bin/sh
# Parity: gpu_pci_detect.c and scripts/gpu_lspci_detect.awk must agree on fixtures.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="${ROOT}/tests/fixtures/gpu_lspci_lines.tsv"
AWK="${ROOT}/scripts/gpu_lspci_detect.awk"

test -f "${FIXTURE}"
test -f "${AWK}"

fail=0
while IFS="$(printf '\t')" read -r line expect_nvidia expect_amd _rest; do
  case "${line}" in
    ''|'#'*) continue ;;
  esac

  got_nvidia=0
  if printf '%s\n' "${line}" | awk -v vendor=nvidia -f "${AWK}"; then
    got_nvidia=1
  fi

  got_amd=0
  if printf '%s\n' "${line}" | awk -v vendor=amd -f "${AWK}"; then
    got_amd=1
  fi

  if test "${got_nvidia}" != "${expect_nvidia}" || test "${got_amd}" != "${expect_amd}"; then
    echo "parity mismatch for line: ${line}" >&2
    echo "  expected nvidia=${expect_nvidia} amd=${expect_amd}" >&2
    echo "  got      nvidia=${got_nvidia} amd=${got_amd}" >&2
    fail=1
  fi
done < "${FIXTURE}"

if test "${fail}" -ne 0; then
  exit 1
fi

echo "test_gpu_lspci_detect_parity passed"
