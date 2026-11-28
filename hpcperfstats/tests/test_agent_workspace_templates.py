"""Guards for the agent workspace templates in cursor-hooks/agent-workspace/.

``isolated_commit.py``, ``sync-cursor-rules.py`` and ``settings.json.template``
are unsanitized byte copies: the canonical files hold no personal paths, so
the copies must equal them whenever they exist on this host (skipped in a
bare clone). The installer runs against a throwaway workspace and HOME.
"""

from __future__ import annotations

import ast
import getpass
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

_CHECKOUT = Path(__file__).resolve().parents[2]
_WORKSPACE = _CHECKOUT.parent
_AW = _CHECKOUT / "cursor-hooks" / "agent-workspace"
_INSTALLER = _AW / "install-agent-workspace.sh"

# (committed copy, canonical file it mirrors byte for byte)
_BYTE_COPIES = [
  (
    _AW / "isolated_commit.py",
    Path.home() / ".claude" / "hooks" / "isolated_commit.py",
  ),
  (
    _AW / "sync-cursor-rules.py",
    _WORKSPACE / ".claude" / "scripts" / "sync-cursor-rules.py",
  ),
]
_SYSTEM_PYTHON_FILES = [copy for copy, _ in _BYTE_COPIES]
# Account names that are words in the templates, not a person.
_GENERIC_USERS = {"root", "runner", "user", "admin", "ci"}


@pytest.mark.parametrize(
  ("copy", "canonical"), _BYTE_COPIES, ids=lambda p: p.name
)
def test_byte_copy_matches_canonical(copy: Path, canonical: Path) -> None:
  if not canonical.is_file():
    pytest.skip(f"canonical {canonical} absent on this host")
  assert copy.read_bytes() == canonical.read_bytes(), (
    f"{copy} differs from {canonical}; copy the canonical file over it"
  )


def test_settings_template_hooks_match_workspace() -> None:
  live = _WORKSPACE / ".claude" / "settings.json"
  if not live.is_file():
    pytest.skip(f"{live} absent on this host")
  tmpl = json.loads((_AW / "settings.json.template").read_text())
  assert json.loads(live.read_text())["hooks"] == tmpl["hooks"]


@pytest.mark.parametrize("path", _SYSTEM_PYTHON_FILES, ids=lambda p: p.name)
def test_system_python_files_parse_on_python39(path: Path) -> None:
  """Both run under the system python3 (3.9 on the dev host)."""
  ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))


def test_byte_copies_excluded_from_ruff() -> None:
  """Ruff's py314 fixes would break the byte copies and 3.9 syntax."""
  pre_commit = (_CHECKOUT / ".pre-commit-config.yaml").read_text()
  assert pre_commit.count("|^cursor-hooks/agent-workspace/") == 2
  ruff = tomllib.loads((_CHECKOUT / "pyproject.toml").read_text())["tool"]
  assert "cursor-hooks/agent-workspace" in ruff["ruff"]["extend-exclude"]


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_templates_not_gitignored() -> None:
  """Root ``*.sh`` is ignored; the installer needs its own negation."""
  for path in sorted(_AW.iterdir()):
    proc = subprocess.run(
      ["git", "check-ignore", "-q", str(path.relative_to(_CHECKOUT))],
      cwd=_CHECKOUT,
      check=False,
    )
    assert proc.returncode == 1, f"{path.name} is gitignored"


def test_templates_hold_no_personal_details() -> None:
  user = getpass.getuser()
  banned = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"  # email address
    r"|/Users/\w|/home/\w"  # absolute home paths
    r"|hpcperfstats0\d"  # site host names
    + (rf"|\b{re.escape(user)}\b" if user not in _GENERIC_USERS else "")
  )
  for path in sorted(_AW.iterdir()):
    if path.is_file():
      hits = banned.findall(path.read_text())
      assert not hits, f"{path.name}: {hits}"


def _fake_workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
  """Workspace with a minimal git checkout copy, plus an isolated HOME."""
  ws = tmp_path / "ws"
  checkout = ws / "HPCPerfStats"
  ignore = shutil.ignore_patterns("__pycache__")
  shutil.copytree(
    _CHECKOUT / "cursor-hooks", checkout / "cursor-hooks", ignore=ignore
  )
  shutil.copytree(
    _CHECKOUT / "hpcperfstats" / "cursor-rules",
    checkout / "hpcperfstats" / "cursor-rules",
  )
  subprocess.run(["git", "init", "-q", str(checkout)], check=True)
  home = tmp_path / "home"
  home.mkdir()
  env = dict(os.environ, HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
  env["GIT_CONFIG_GLOBAL"] = os.devnull
  return ws, home, env


def _install(ws: Path, env: dict[str, str], *args: str) -> str:
  script = ws / "HPCPerfStats" / _INSTALLER.relative_to(_CHECKOUT)
  proc = subprocess.run(
    ["bash", str(script), *args],
    env=env,
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0, proc.stdout + proc.stderr
  return proc.stdout


def _tree(root: Path) -> dict[str, str]:
  """Relative path -> file bytes digest or symlink target, under root."""
  out = {}
  for p in sorted(root.rglob("*")):
    rel = str(p.relative_to(root))
    if rel.startswith("HPCPerfStats/") and "/.git" not in f"/{rel}":
      continue
    if p.is_symlink():
      out[rel] = "-> " + os.readlink(p)
    elif p.is_file():
      out[rel] = p.read_text(errors="replace")
  return out


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_installer_dry_run_writes_nothing(tmp_path: Path) -> None:
  ws, _home, env = _fake_workspace(tmp_path)
  before = _tree(tmp_path)
  out = _install(ws, env, "--dry-run")
  assert "sync-cursor-rules.py" in out
  assert _tree(tmp_path) == before


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_installer_creates_workspace_and_is_idempotent(tmp_path: Path) -> None:
  ws, home, env = _fake_workspace(tmp_path)
  checkout = ws / "HPCPerfStats"
  _install(ws, env, "--dual")

  cursor = ws / ".cursor"
  assert os.readlink(cursor / "rules") == (
    "../HPCPerfStats/hpcperfstats/cursor-rules"
  )
  assert os.readlink(cursor / "hooks") == "../HPCPerfStats/cursor-hooks"
  hooks_json = cursor / "hooks.json"
  assert not hooks_json.is_symlink()
  assert (
    hooks_json.read_bytes()
    == (checkout / "cursor-hooks" / "hooks.json").read_bytes()
  )
  assert (cursor / "plans").is_dir()
  assert (cursor / "workspace-profile").read_text().split()[0] == "dual"

  claude = ws / ".claude"
  pairs = {
    claude / "hooks" / "hpc_claude_hooks.py": _CHECKOUT
    / "cursor-hooks"
    / "hpc_claude_hooks.py",
    claude / "scripts" / "sync-cursor-rules.py": _AW / "sync-cursor-rules.py",
    claude / "settings.json": _AW / "settings.json.template",
    ws / "CLAUDE.md": _AW / "CLAUDE.md.template",
  }
  for dst, src in pairs.items():
    assert dst.read_bytes() == src.read_bytes(), dst
  assert (claude / "skills").is_dir()
  assert (claude / "cursor-always-on.md").is_file()
  assert list((claude / "rules").glob("cursor-*.md"))

  link = home / ".local" / "bin" / "git-isolated-commit"
  assert link.resolve() == (
    checkout / "cursor-hooks" / "agent-workspace" / "isolated_commit.py"
  )
  links = subprocess.run(
    ["git", "-C", str(checkout), "config", "--get-all", "isolated-commit.link"],
    env=env,
    capture_output=True,
    text=True,
    check=True,
  ).stdout.split()
  assert "hpcperfstats.ini" in links

  (ws / "CLAUDE.md").write_text("mine\n")
  before = _tree(tmp_path)
  _install(ws, env)
  assert _tree(tmp_path) == before


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_installer_keeps_foreign_isolated_commit_link(tmp_path: Path) -> None:
  ws, home, env = _fake_workspace(tmp_path)
  bindir = home / ".local" / "bin"
  bindir.mkdir(parents=True)
  (bindir / "git-isolated-commit").symlink_to("/nonexistent/other.py")
  out = _install(ws, env)
  assert os.readlink(bindir / "git-isolated-commit") == "/nonexistent/other.py"
  assert "skip:" in out
