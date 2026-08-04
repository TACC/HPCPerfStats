"""Regression: git hooks must not source interactive shell rc under bash.

Failure signature (Cursor commit / pre-commit): sourcing ~/.zshrc from
`#!/usr/bin/env bash` prints Oh My Zsh errors (autoload/zle not found) and
noise from a broken ~/.bash_profile when those lines were injected into
.git/hooks/pre-commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = ROOT / "scripts" / "install-git-hooks.sh"
PRE_COMMIT_HOOK = ROOT / ".git" / "hooks" / "pre-commit"

# Same intent as scripts/install-git-hooks.sh _hook_sources_interactive_rc.
_RC_SOURCE_RE = re.compile(
    r"(^|[^A-Za-z0-9_])(\.|source)\s+[^\n]*(~|/)?\.?(bash_profile|bashrc|zshrc|profile)\b"
)


def test_install_git_hooks_script_sanitizes_interactive_rc_sourcing():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "_hook_sources_interactive_rc" in text
    assert "_sanitize_hook" in text
    assert "zshrc" in text
    assert "Oh My Zsh" in text or "interactive" in text


@pytest.mark.skipif(
    not PRE_COMMIT_HOOK.is_file(),
    reason="no local .git/hooks/pre-commit (hooks not installed)",
)
def test_installed_pre_commit_hook_does_not_source_shell_rc():
    text = PRE_COMMIT_HOOK.read_text(encoding="utf-8")
    assert not _RC_SOURCE_RE.search(text), (
        "pre-commit hook must not source ~/.zshrc / ~/.bash_profile under bash; "
        "re-run scripts/install-git-hooks.sh"
    )
    assert "INSTALL_PYTHON=" in text
