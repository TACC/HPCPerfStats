#!/usr/bin/env bash
# Whole-machine overhead profile of the release hpcperfstatsd RPM (run as root on an idle,
# reserved node). Logic lives in lib/orchestrator.py; see RUNBOOK.md "Overhead profile".
#
#   sudo ./bin/run_overhead_profile.sh --rpm rpmbuild/RPMS/x86_64/hpcperfstatsd-*.rpm \
#        --build-info rpmbuild/build-info
#   ./bin/run_overhead_profile.sh --dry-run --rpm FILE      # schedule + projected end only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
PYTHON="${HPCPERFSTATS_PYTHON:-python3}"
need_cmd "${PYTHON}"

exec "${PYTHON}" "${SCRIPT_DIR}/overhead_profile.py" "$@"
