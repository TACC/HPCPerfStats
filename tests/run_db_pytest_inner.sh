#!/usr/bin/env bash
# Invoked inside the web container by tests/run_db_pytest_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1

compose_inner_pip_install

if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" != "1" ]]; then
  python -m playwright install --with-deps chromium
fi

if [[ "${DOCKER_PYTEST_SKIP_MIGRATE:-0}" != "1" ]]; then
  python hpcperfstats/site/manage.py migrate --noinput
fi

if [[ -n "${DOCKER_PYTEST_SEED_CMD:-}" ]]; then
  bash -lc "$DOCKER_PYTEST_SEED_CMD"
fi

IGNORE=()
if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" == "1" ]]; then
  IGNORE+=(
    --ignore=hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py
    --ignore=hpcperfstats/site/machine/tests/test_bokeh_job_list_embed_browser_e2e.py
  )
fi

ARGS=()
_pytest_args_file="/tmp/hpcperfstats_pytest_extra_args.list"
if [[ -f "$_pytest_args_file" && -s "$_pytest_args_file" ]]; then
  while IFS= read -r _pytest_arg || [[ -n "${_pytest_arg:-}" ]]; do
    [[ -n "$_pytest_arg" ]] && ARGS+=("$_pytest_arg")
  done < "$_pytest_args_file"
elif [[ -d "$_pytest_args_file" ]]; then
  echo "WARNING: ${_pytest_args_file} is a directory; ignoring forwarded pytest args" >&2
fi

if [[ ${#ARGS[@]} -gt 0 ]]; then
  exec python -m pytest -q "${IGNORE[@]}" "${ARGS[@]}"
fi
exec python -m pytest -q hpcperfstats "${IGNORE[@]}"
