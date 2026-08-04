#!/usr/bin/env bash
# Install pre-commit / pre-push lint hooks for HPCPerfStats.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "../.venv/bin/python3" ]]; then
  echo "error: workspace venv missing at ../.venv — create it before installing hooks" >&2
  exit 1
fi

../.venv/bin/pip3 install -e ".[dev]"
../.venv/bin/pre-commit install
../.venv/bin/pre-commit install --hook-type pre-push

# Never source interactive shell rc files from git hooks. Hooks run under
# `#!/usr/bin/env bash`; sourcing ~/.zshrc loads Oh My Zsh and prints noise /
# errors (autoload/zle not found). Absolute INSTALL_PYTHON already provides PATH.
_hook_sources_interactive_rc() {
  local hook="$1"
  grep -qE '(^|[^[:alnum]_])(\.|source)[[:space:]]+[^\n]*(~|/)?\.?(bash_profile|bashrc|zshrc|profile)\b' "$hook" 2>/dev/null
}
_sanitize_hook() {
  local hook="$1"
  [[ -f "$hook" ]] || return 0
  if _hook_sources_interactive_rc "$hook"; then
    echo "warning: stripping interactive profile sourcing from $hook" >&2
    local ht
    ht="$(basename "$hook")"
    ../.venv/bin/pre-commit install --overwrite --hook-type "$ht"
  fi
}
_sanitize_hook "$ROOT/.git/hooks/pre-commit"
_sanitize_hook "$ROOT/.git/hooks/pre-push"

FRONTEND="$ROOT/hpcperfstats/site/frontend"
if [[ -f "$FRONTEND/package.json" ]]; then
  (cd "$FRONTEND" && npm ci)
fi

echo "Git hooks installed (pre-commit + pre-push)."
