"""Drift guards for the evergreen docs governed by docs-current-state-only.mdc.

Evergreen docs describe the code at HEAD: the root README.md, docs/index.md,
every Markdown page under docs/{tutorials,how-to,reference,explanation}/ and
monitor/docs/{how-to,explanation,reference}/ (globbed at test time, so a new
page is covered without editing this file), plus CORE_DOCS, which must exist.
These tests fail when a page uses characters
outside the allowlist, carries changelog sections or old-version phrasing,
has paste-unsafe shell fences, cites INI keys or defaults that disagree with
conf_parser, or (TESTING_DOCS pages) points at files that do not exist.

Exemptions: docs/reference/documentation-style.md quotes the banned phrases,
so it skips the old-version phrasing check; docs/reference/release-notes.md
records one-time cutovers, so it skips both current-state wording checks
(changelog headings and old-version phrasing). Every other check applies to
both.
"""

import re
import subprocess
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import conf_parser as cfg

_REPO = Path(__file__).resolve().parents[2]

CORE_DOCS = ("README.md",)
TESTING_DOCS = (
  "docs/how-to/develop/run-tests.md",
  "docs/reference/test-layout.md",
  "docs/how-to/develop/run-sync-timedb-benchmarks.md",
  "docs/how-to/develop/diagnose-update-metrics.md",
)

_EVERGREEN_FILES = ("README.md", "docs/index.md")
_EVERGREEN_DIRS = (
  "docs/tutorials",
  "docs/how-to",
  "docs/reference",
  "docs/explanation",
  "monitor/docs/how-to",
  "monitor/docs/explanation",
  "monitor/docs/reference",
)


def _evergreen_docs() -> tuple[str, ...]:
  found = set(CORE_DOCS)
  found.update(rel for rel in _EVERGREEN_FILES if (_REPO / rel).is_file())
  for rel_dir in _EVERGREEN_DIRS:
    for path in (_REPO / rel_dir).rglob("*.md"):
      found.add(path.relative_to(_REPO).as_posix())
  return tuple(sorted(found))


EVERGREEN_DOCS = _evergreen_docs()
STYLE_GUIDE = "docs/reference/documentation-style.md"
RELEASE_NOTES = "docs/reference/release-notes.md"
CURRENT_STATE_DOCS = tuple(d for d in EVERGREEN_DOCS if d != RELEASE_NOTES)
PHRASING_DOCS = tuple(d for d in CURRENT_STATE_DOCS if d != STYLE_GUIDE)

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

# Character allowlist (docs/reference/documentation-style.md, V16): ASCII;
# U+00B0 degree and U+00B5 micro signs; letters for accented proper names:
# Latin-1 Supplement U+00C0-U+00FF without the operators U+00D7 and U+00F7,
# plus Latin Extended-A U+0100-U+017F (all letters, for Central European
# names). Dashes, arrows, quotes, nbsp, U+00A7 and emoji stay out.
_ALLOWED_NON_ASCII = (
  frozenset("\u00b0\u00b5")
  | frozenset(chr(c) for c in range(0xC0, 0x100) if c not in (0xD7, 0xF7))
  | frozenset(chr(c) for c in range(0x100, 0x180))
)

_TESTING_PATH_RE = re.compile(
  r"`((?:[\w.-]+/)*[\w.-]+\.(?:py|sh|ts|tsx|mts|mjs|js|ini|json|yaml|toml))`"
)

# Bare names checked against tracked files; other bare names in TESTING_DOCS
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


@pytest.mark.parametrize("rel", EVERGREEN_DOCS)
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


def _disallowed_chars(line: str) -> list[str]:
  return [
    f"U+{ord(ch):04X}"
    for ch in line
    if not ch.isascii() and ch not in _ALLOWED_NON_ASCII
  ]


@pytest.mark.parametrize("rel", EVERGREEN_DOCS)
def test_core_doc_chars_in_allowlist(rel):
  bad = []
  for n, line in enumerate(_doc_text(rel).splitlines(), 1):
    chars = _disallowed_chars(line)
    if chars:
      bad.append(f"{rel}:{n}: {' '.join(chars)}: {line.strip()[:80]}")
  assert not bad, (
    "evergreen docs are ASCII plus the degree sign, micro sign and "
    "accented letters (use -, ->, x, >=, ...):\n" + "\n".join(bad)
  )


@pytest.mark.parametrize("rel", CURRENT_STATE_DOCS)
def test_core_doc_has_no_history_section(rel):
  match = _HISTORY_HEADING_RE.search(_doc_text(rel))
  assert match is None, f"{rel}: changelog heading {match.group(0)!r}"


@pytest.mark.parametrize("rel", PHRASING_DOCS)
def test_core_doc_has_no_old_version_phrasing(rel):
  hits = [
    f"{rel}:{n}: {line.strip()[:120]}"
    for n, line in enumerate(_doc_text(rel).splitlines(), 1)
    if _OLD_VERSION_RE.search(line)
  ]
  assert not hits, "old-version / changelog phrasing:\n" + "\n".join(hits)


@pytest.mark.parametrize("rel", EVERGREEN_DOCS)
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


@pytest.mark.parametrize("rel", EVERGREEN_DOCS)
def test_core_doc_table_defaults_match_code(rel):
  bad = []
  for key, value in _TABLE_KEY_DEFAULT_RE.findall(_doc_text(rel)):
    default = cfg.INI_OPTION_DEFAULTS.get(key)
    if default is None:
      continue
    if not _values_equal(value.strip(), default):
      bad.append(f"{key}: doc {value!r}, code {default!r}")
  assert not bad, f"{rel}:\n" + "\n".join(bad)


@pytest.mark.parametrize("rel", TESTING_DOCS)
def test_testing_doc_paths_exist(rel):
  tracked = _tracked_paths()
  basenames = {Path(p).name for p in tracked}
  missing = []
  cited = {
    p.removeprefix("./") for p in _TESTING_PATH_RE.findall(_doc_text(rel))
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
  assert not missing, f"{rel} cites missing files:\n" + "\n".join(missing)


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


def test_char_allowlist_admits_units_and_names_only():
  assert not _disallowed_chars(
    "21 \u00b0C, 5 \u00b5s, Dvo\u0159\u00e1k, Jos\u00e9"
  )
  for ch in "\u00a7\u00d7\u00f7\u2014\u2013\u2192\u2026\u201c\u00a0\u2713":
    assert _disallowed_chars(ch) == [f"U+{ord(ch):04X}"]


def test_evergreen_scope_covers_quadrants_and_core_docs():
  assert set(CORE_DOCS) <= set(EVERGREEN_DOCS)
  assert set(TESTING_DOCS) <= set(EVERGREEN_DOCS)
  assert "README.md" in EVERGREEN_DOCS
  assert STYLE_GUIDE in EVERGREEN_DOCS
  assert STYLE_GUIDE in CURRENT_STATE_DOCS
  assert STYLE_GUIDE not in PHRASING_DOCS
  assert RELEASE_NOTES not in CURRENT_STATE_DOCS
