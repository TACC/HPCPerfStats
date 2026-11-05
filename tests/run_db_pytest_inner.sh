#!/usr/bin/env bash
# Invoked inside the web container by tests/run_db_pytest_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
export USER="${USER:-$(id -un)}"
export PYTHONPYCACHEPREFIX="${XDG_CACHE_HOME:-/tmp}/python-pycache"

compose_inner_pip_install

# Test-only tools the runtime image does not ship: git, and filefrag
# (e2fsprogs) for test_clone_append_shares_extents_with_the_raw_file.
_apt_pkgs=()
command -v git >/dev/null 2>&1 || _apt_pkgs+=(git)
command -v filefrag >/dev/null 2>&1 || _apt_pkgs+=(e2fsprogs)
if [[ ${#_apt_pkgs[@]} -gt 0 ]]; then
  apt-get update -qq
  apt-get install -y --no-install-recommends "${_apt_pkgs[@]}"
  rm -rf /var/lib/apt/lists/*
fi

if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" != "1" ]]; then
  python3 -m pip install -q "playwright>=1.60.0"
  python3 -m playwright install --with-deps chromium
fi

if [[ "${DOCKER_PYTEST_SKIP_MIGRATE:-0}" != "1" ]]; then
  python3 hpcperfstats/site/manage.py migrate --noinput
fi

if [[ -n "${DOCKER_PYTEST_SEED_CMD:-}" ]]; then
  bash -lc "$DOCKER_PYTEST_SEED_CMD"
fi

IGNORE=()
if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" == "1" ]]; then
  IGNORE+=(
    --ignore=hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py
    --ignore=hpcperfstats/site/lib/machine/tests/test_bokeh_job_list_embed_browser_e2e.py
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

# Reflink-capable tmp_path outside the checkout, bind-mounted by
# run_db_pytest_workflow.sh (see the PYTEST_TMP_HOST comment there).
BASETEMP=()
if [[ -n "${DOCKER_PYTEST_BASETEMP:-}" ]]; then
  BASETEMP=(--basetemp "$DOCKER_PYTEST_BASETEMP")
fi

if [[ ${#ARGS[@]} -gt 0 ]]; then
  exec python3 -m pytest -q "${BASETEMP[@]}" "${IGNORE[@]}" "${ARGS[@]}"
fi
exec python3 -m pytest -q "${BASETEMP[@]}" hpcperfstats "${IGNORE[@]}"
