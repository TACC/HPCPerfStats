"""Drift guards for the core docs governed by docs-current-state-only.mdc.

README.md, docs/using-the-website-as-a-researcher.md,
docs/DEPLOY_CONCURRENCY_AND_NUMA.md, docs/design-document.md and
docs/TESTING.md describe the code at HEAD. These tests fail when they carry
changelog sections or old-version phrasing, cite INI keys or defaults that
disagree with conf_parser, or point TESTING.md at files that do not exist.
"""

import re
import subprocess
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import conf_parser as cfg

_REPO = Path(__file__).resolve().parents[2]

CORE_DOCS = (
  "README.md",
  "docs/using-the-website-as-a-researcher.md",
  "docs/DEPLOY_CONCURRENCY_AND_NUMA.md",
  "docs/design-document.md",
  "docs/TESTING.md",
)

_HISTORY_HEADING_RE = re.compile(
  r"^#+\s*(document history|change ?log|revision history)\b",
  re.IGNORECASE | re.MULTILINE,
)

_OLD_VERSION_PHRASES = (
  r"\bpreviously\b",
  r"\bno longer\b",
  r"\bwas renamed\b",
  r"\bwere renamed\b",
  r"\bwas changed\b",
  r"\bused to (?:be|run|use|show|live)\b",
  r"(?<![-\w])(?:since|as of) 20\d\d-\d\d-\d\d\b",
)
_OLD_VERSION_RE = re.compile("|".join(_OLD_VERSION_PHRASES), re.IGNORECASE)

_REGISTRY_SECTIONS = {
  option: section for section, option, _default in cfg.INI_OPTION_REGISTRY
}

# `[PIPELINE] key` / **`[PIPELINE]`** **`key`** / `[PIPELINE] key = 4`;
# the key must close a code span or be followed by `=` so prose such as
# "`[PIPELINE]` keys" is not read as a key.
_SECTION_KEY_RE = re.compile(
  r"\[(" + "|".join(sorted(set(_REGISTRY_SECTIONS.values()))) + r")\]"
  r"(?:`?\**\s+\**`?)([a-z][a-z0-9_]+)(?=`|\s*=)"
)

# | **`key`** | **`4`** | ... (key/default table rows)
_TABLE_KEY_DEFAULT_RE = re.compile(
  r"^\|\s*\**`([a-z][a-z0-9_]+)`\**\s*\|\s*\**`([^`|]+)`\**\s*\|",
  re.MULTILINE,
)

# Same limits as the Claude Code paste-safety stop hook: TUI copy adds a
# 2-space indent, so 68 + 2 stays under the 70-column hard wrap.
PASTE_LINE_MAX = 68
_SHELL_FENCE_OPEN_RE = re.compile(r"^(\s*)```(?:bash|sh|shell)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")
_HEREDOC_RE = re.compile(r"(?<!<)<<-?[ \t]*['\"]?[A-Za-z_]\w*")

_TESTING_PATH_RE = re.compile(
  r"`((?:[\w.-]+/)*[\w.-]+\.(?:py|sh|ts|tsx|mts|mjs|js|ini|json|yaml|toml))`"
)

# Bare names checked against tracked files; other bare names in TESTING.md
# are runtime artifacts (hpcperfstats.ini, manifest.json, sidecars), as is
# anything under test_runs/.
_TEST_FILE_NAME_RE = re.compile(r"^test_\w+\.py$|\.test\.[jt]sx?$|\.sh$")


def _doc_text(rel: str) -> str:
  return (_REPO / rel).read_text(encoding="utf-8")


def _tracked_paths() -> list[str]:
  out = subprocess.run(
    ["git", "-C", str(_REPO), "ls-files"],
    check=True,
    capture_output=True,
    text=True,
  ).stdout
  return out.splitlines()


def _values_equal(doc_value: str, code_default: str) -> bool:
  if doc_value == code_default:
    return True
  try:
    return float(doc_value) == float(code_default)
  except ValueError:
    return False


def _shell_fence_bodies(text: str) -> list[tuple[int, list[str]]]:
  """Return (opening line number, body lines) for each bash/sh fence.

  Body lines have the fence's own indent removed, so a fence nested in a
  list item is measured the way a reader copies it.
  """
  fences = []
  lines = text.splitlines()
  i = 0
  while i < len(lines):
    opening = _SHELL_FENCE_OPEN_RE.match(lines[i])
    if not opening:
      i += 1
      continue
    indent, start, body = opening.group(1), i + 1, []
    i += 1
    while i < len(lines) and not _FENCE_CLOSE_RE.match(lines[i]):
      line = lines[i]
      body.append(line[len(indent) :] if line.startswith(indent) else line)
      i += 1
    fences.append((start, body))
    i += 1
  return fences


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_shell_fences_are_paste_safe(rel):
  bad = []
  for start, body in _shell_fence_bodies(_doc_text(rel)):
    for offset, line in enumerate(body, 1):
      if len(line) > PASTE_LINE_MAX:
        bad.append(f"{rel}:{start + offset}: {len(line)} chars")
      if _HEREDOC_RE.search(line):
        bad.append(f"{rel}:{start + offset}: heredoc")
  assert not bad, (
    f"bash/sh fence lines must be <= {PASTE_LINE_MAX} chars with no "
    "heredoc (copy indents by 2 and hard-wraps):\n" + "\n".join(bad)
  )


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_is_ascii(rel):
  bad = [
    f"{rel}:{n}: {line.strip()[:80]}"
    for n, line in enumerate(_doc_text(rel).splitlines(), 1)
    if not line.isascii()
  ]
  assert not bad, (
    "core docs are plain ASCII (use -, ->, x, >=, ...):\n" + "\n".join(bad)
  )


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_has_no_history_section(rel):
  match = _HISTORY_HEADING_RE.search(_doc_text(rel))
  assert match is None, f"{rel}: changelog heading {match.group(0)!r}"


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_has_no_old_version_phrasing(rel):
  hits = [
    f"{rel}:{n}: {line.strip()[:120]}"
    for n, line in enumerate(_doc_text(rel).splitlines(), 1)
    if _OLD_VERSION_RE.search(line)
  ]
  assert not hits, "old-version / changelog phrasing:\n" + "\n".join(hits)


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_section_keys_exist_in_registry(rel):
  bad = []
  for section, key in _SECTION_KEY_RE.findall(_doc_text(rel)):
    if key not in _REGISTRY_SECTIONS:
      bad.append(f"[{section}] {key}: not in INI_OPTION_REGISTRY")
    elif _REGISTRY_SECTIONS[key] != section:
      bad.append(
        f"[{section}] {key}: registry section is [{_REGISTRY_SECTIONS[key]}]"
      )
  assert not bad, f"{rel}:\n" + "\n".join(bad)


@pytest.mark.parametrize("rel", CORE_DOCS)
def test_core_doc_table_defaults_match_code(rel):
  bad = []
  for key, value in _TABLE_KEY_DEFAULT_RE.findall(_doc_text(rel)):
    default = cfg.INI_OPTION_DEFAULTS.get(key)
    if default is None:
      continue
    if not _values_equal(value.strip(), default):
      bad.append(f"{key}: doc {value!r}, code {default!r}")
  assert not bad, f"{rel}:\n" + "\n".join(bad)


def test_testing_doc_paths_exist():
  tracked = _tracked_paths()
  basenames = {Path(p).name for p in tracked}
  missing = []
  cited = {
    p.removeprefix("./")
    for p in _TESTING_PATH_RE.findall(_doc_text(CORE_DOCS[4]))
  }
  for path in sorted(cited):
    if path.startswith("test_runs/"):
      continue
    if "/" not in path:
      found = path in basenames or not _TEST_FILE_NAME_RE.search(path)
    else:
      found = any(p == path or p.endswith("/" + path) for p in tracked)
    if not found:
      missing.append(path)
  assert not missing, "docs/TESTING.md cites missing files:\n" + "\n".join(
    missing
  )


def test_section_key_regex_reads_doc_forms():
  text = (
    "`[PIPELINE] archive_zstd_threads` and **`[PORTAL]`** "
    "**`gunicorn_workers`**"
  )
  assert _SECTION_KEY_RE.findall(text) == [
    ("PIPELINE", "archive_zstd_threads"),
    ("PORTAL", "gunicorn_workers"),
  ]
  assert not _SECTION_KEY_RE.findall("`[PIPELINE]` keys and `[RMQ]` hosts")


def test_table_default_regex_reads_doc_rows():
  row = "| **`archive_zstd_threads`** | **`4`** | Seal threads |\n"
  assert _TABLE_KEY_DEFAULT_RE.findall(row) == [("archive_zstd_threads", "4")]


def test_shell_fence_parser_strips_list_indent_and_flags_heredoc():
  text = (
    "1. Step\n"
    "   ```bash\n"
    "   echo ok\n"
    "   cat <<EOF\n"
    "   ```\n"
    "```sh\n" + "x" * 69 + "\n```\n"
  )
  fences = _shell_fence_bodies(text)
  assert fences == [(2, ["echo ok", "cat <<EOF"]), (6, ["x" * 69])]
  assert _HEREDOC_RE.search("cat <<EOF")
  assert not _HEREDOC_RE.search('grep -q x <<<"$out"')


def test_old_version_regex_flags_changelog_prose():
  assert _OLD_VERSION_RE.search("Archive zstd previously ran at nice 19")
  assert _OLD_VERSION_RE.search("since 2026-11-02 it runs at normal priority")
  assert not _OLD_VERSION_RE.search("This script is used to seal tars")
  assert not _OLD_VERSION_RE.search("run.py --since 2026-01-04")
