#!/usr/bin/env bash
# Set up the Cursor + Claude Code agent workspace around this checkout.
#
# Usage: install-agent-workspace.sh [--dry-run] [--force] [--dual] [WORKSPACE_ROOT]
#
# WORKSPACE_ROOT defaults to the parent of the checkout and must contain the
# checkout as HPCPerfStats/. Idempotent: existing CLAUDE.md, settings.json,
# symlinks and git config entries are never changed. Copied repo files
# (.claude hook, sync script, .cursor/hooks.json) are refreshed only with
# --force when they differ. --dual writes .cursor/workspace-profile if absent.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CHECKOUT="$(cd "$HERE/../.." && pwd -P)"
DRY=0
FORCE=0
DUAL=0
WS=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    --dual) DUAL=1 ;;
    -h | --help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    -*)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
    *) WS="$arg" ;;
  esac
done
WS="$(cd "${WS:-$CHECKOUT/..}" && pwd -P)"

if [ "$(cd "$WS/HPCPerfStats" 2>/dev/null && pwd -P)" != "$CHECKOUT" ]; then
  echo "error: $WS/HPCPerfStats must be this checkout ($CHECKOUT)" >&2
  exit 1
fi

say() { echo "install-agent-workspace: $*"; }
run() {
  say "$*"
  if [ "$DRY" -eq 0 ]; then "$@"; fi
}

# copy SRC DST: create DST, or refresh it with --force when it differs.
copy() {
  if [ ! -e "$2" ]; then
    run cp "$1" "$2"
  elif cmp -s "$1" "$2"; then
    say "ok: $2"
  elif [ "$FORCE" -eq 1 ]; then
    run cp "$1" "$2"
  else
    say "skip: $2 differs from $1 (rerun with --force to refresh)"
  fi
}

# install_once SRC DST: create DST from a template; never overwrite.
install_once() {
  if [ -e "$2" ]; then
    say "keep: $2"
  else
    run cp "$1" "$2"
  fi
}

# link TARGET NAME: create symlink NAME -> TARGET unless NAME exists.
link() {
  if [ -L "$2" ] && [ "$(readlink "$2")" = "$1" ]; then
    say "ok: $2 -> $1"
  elif [ -e "$2" ] || [ -L "$2" ]; then
    say "skip: $2 exists and is not a link to $1"
  else
    run ln -s "$1" "$2"
  fi
}

run mkdir -p "$WS/.cursor/plans" \
  "$WS/.claude/hooks" "$WS/.claude/scripts" \
  "$WS/.claude/rules" "$WS/.claude/skills"

link ../HPCPerfStats/hpcperfstats/cursor-rules "$WS/.cursor/rules"
link ../HPCPerfStats/cursor-hooks "$WS/.cursor/hooks"
copy "$CHECKOUT/cursor-hooks/hooks.json" "$WS/.cursor/hooks.json"
if [ "$DUAL" -eq 1 ]; then
  if [ -e "$WS/.cursor/workspace-profile" ]; then
    say "keep: $WS/.cursor/workspace-profile"
  else
    say "write dual to $WS/.cursor/workspace-profile"
    if [ "$DRY" -eq 0 ]; then
      echo dual >"$WS/.cursor/workspace-profile"
    fi
  fi
fi

copy "$CHECKOUT/cursor-hooks/hpc_claude_hooks.py" \
  "$WS/.claude/hooks/hpc_claude_hooks.py"
copy "$HERE/sync-cursor-rules.py" "$WS/.claude/scripts/sync-cursor-rules.py"
install_once "$HERE/settings.json.template" "$WS/.claude/settings.json"
install_once "$HERE/CLAUDE.md.template" "$WS/CLAUDE.md"
if [ -d "$WS/.cursor/skills" ]; then
  for skill in "$WS/.cursor/skills"/*/; do
    [ -d "$skill" ] || continue
    name="$(basename "$skill")"
    link "../../.cursor/skills/$name" "$WS/.claude/skills/$name"
  done
fi

run mkdir -p "$HOME/.local/bin"
link "$HERE/isolated_commit.py" "$HOME/.local/bin/git-isolated-commit"
links="$(git -C "$CHECKOUT" config --get-all isolated-commit.link || true)"
for rel in hpcperfstats.ini hpcperfstats/site/frontend/node_modules \
  hpcperfstats/site/frontend/src/api/generated \
  hpcperfstats/site/frontend/src/api/generated-zod; do
  if grep -qxF "$rel" <<<"$links"; then
    say "ok: isolated-commit.link $rel"
  else
    run git -C "$CHECKOUT" config --add isolated-commit.link "$rel"
  fi
done

if [ "$DRY" -eq 0 ]; then
  python3 "$WS/.claude/scripts/sync-cursor-rules.py"
else
  say "python3 $WS/.claude/scripts/sync-cursor-rules.py"
fi
