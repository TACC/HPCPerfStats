#!/usr/bin/env bash
# Run the Bokeh browser contract through the standard isolated test project.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=compose_test_cmd.sh
. tests/compose_test_cmd.sh
podman_runtime_require

exec tests/run_db_pytest_workflow.sh "$@" -- \
  hpcperfstats/site/lib/machine/tests/test_bokeh_job_list_embed_browser_e2e.py
