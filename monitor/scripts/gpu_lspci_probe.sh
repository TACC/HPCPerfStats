#!/bin/sh
# Probe live lspci -nn output for NVIDIA, AMD, or Intel Data Center GPU class lines.
# Usage: gpu_lspci_probe.sh nvidia|amd|intel
set -e

vendor="${1:?usage: gpu_lspci_probe.sh nvidia|amd|intel}"
case "${vendor}" in
  nvidia|amd|intel) ;;
  *)
    echo "gpu_lspci_probe.sh: unknown vendor '${vendor}'" >&2
    exit 2
    ;;
esac

if ! command -v lspci >/dev/null 2>&1; then
  exit 1
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec lspci -nn 2>/dev/null | awk -v vendor="${vendor}" -f "${script_dir}/gpu_lspci_detect.awk"
