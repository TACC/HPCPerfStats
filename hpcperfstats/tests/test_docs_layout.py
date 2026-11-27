"""Layout, page anatomy and offline link checks for the docs tree.

Placement and anatomy follow docs/reference/documentation-style.md and
docs-structure-and-style.mdc:

- Files under docs/ and monitor/docs/ live in a quadrant directory
  (docs/{tutorials,how-to/operate,how-to/develop,reference,explanation}/,
  monitor/docs/{how-to,explanation,reference}/), in a record directory
  (docs/history/, docs/measurements/, docs/plans/), are docs/index.md, or
  are listed in LEGACY_ALLOWED.
- Evergreen pages (docs/index.md and the quadrant directories) have kebab-case
  names, exactly one H1 on line 1 and a prose purpose line after it; pages in
  GENERATED_DOCS carry the generator line there instead. docs/history/ pages
  carry the record banner there. No README.md under docs/ except
  docs/measurements/README.md, and none in the monitor quadrants.
- Every evergreen page, the root README.md and docs/plans/PLAN_TEMPLATE.md
  have resolving relative links (including into records), #fragment anchors
  that match a GitHub heading slug in the target, defined reference-style
  links, and no links into test_runs/ or .cursor/. Links inside records are
  not checked.

The file list comes from one ``git ls-files`` call (tracked plus untracked,
not ignored), so new pages are covered before they are committed and the test
stays fast enough for pre-push.
"""

from __future__ import annotations

import re
import subprocess
import urllib.parse
from functools import cache
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

DOC_ROOTS = ("docs/", "monitor/docs/")
EVERGREEN_FILES = ("docs/index.md",)
EVERGREEN_DIRS = (
  "docs/tutorials/",
  "docs/how-to/operate/",
  "docs/how-to/develop/",
  "docs/reference/",
  "docs/explanation/",
  "monitor/docs/how-to/",
  "monitor/docs/explanation/",
  "monitor/docs/reference/",
)
RECORD_DIRS = ("docs/history/", "docs/measurements/", "docs/plans/")
# Pages outside the evergreen tree whose links are checked too.
LINK_CHECKED_EXTRA = ("README.md", "docs/plans/PLAN_TEMPLATE.md")
ALLOWED_READMES = frozenset({"docs/measurements/README.md"})

# Pre-quadrant files that exist until their merge group moves or deletes them.
# Each group removes its own entries in the commit that moves the file;
# test_legacy_allowed_entries_still_exist fails on an entry left behind, and
# a new file outside the quadrant and record directories fails placement.
LEGACY_ALLOWED = frozenset(
  {
    "docs/HPCPerfStats Data Flow.pdf",
    "docs/HPCPerfstatsBasicArch.pdf",
    "docs/MONITOR_NAMING_SCHEME.md",
    "docs/MONITOR_VARIABLES.md",
    "docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md",
    "docs/OPERATOR_PG18_MIGRATION.md",
    "docs/OPERATOR_RABBITMQ_RECOVERY.md",
    "docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md",
    "docs/SECURITY_AUDIT.md",
    "docs/SECURITY_REMEDIATION_BACKLOG.md",
    "docs/SPA_UI_FEEDBACK.md",
    "docs/SYNC_TIMEDB_CAMPAIGN_PATCH_INDEX.md",
    "docs/SYNC_TIMEDB_PARALLELISM.md",
    "docs/SYNC_TIMEDB_STATE_MACHINE.md",
    "docs/SYNC_TIMEDB_THROUGHPUT_CAMPAIGN.md",
    "docs/UPDATE_METRICS_STATE_MACHINE.md",
    "docs/apply_monitor_variable_renames.py",
    "docs/artifacts/per_jid_duplicate_query_exceptions.md",
    "docs/artifacts/per_jid_reuse_sweep_report_2026-04-22.md",
    "docs/artifacts/rule_compliance_report.md",
    "docs/artifacts/stress/.gitkeep",
    "docs/artifacts/stress/stress_report_20260406T180559Z.json",
    "docs/artifacts/stress/stress_report_20260406T180631Z.json",
    "docs/artifacts/stress/stress_report_20260406T221120Z.json",
    "docs/artifacts/stress/stress_report_20260406T221241Z.json",
    "docs/artifacts/stress/stress_report_20260406T221342Z.json",
    "docs/artifacts/stress/stress_report_20260406T221511Z.json",
    "docs/artifacts/stress/stress_report_20260406T221549Z.json",
    "docs/artifacts/stress/stress_report_20260407T002508Z.json",
    "docs/artifacts/stress/stress_report_20260407T002601Z.json",
    "docs/artifacts/stress/stress_report_20260407T002755Z.json",
    "docs/artifacts/stress/stress_report_20260407T002834Z.json",
    "docs/artifacts/stress/stress_report_20260407T003051Z.json",
    "docs/artifacts/stress/stress_report_20260407T003132Z.json",
    "docs/artifacts/update_metrics_review_2026-10-30.md",
    "docs/artifacts/update_metrics_speedup_followup.md",
    "docs/artifacts/update_metrics_stall_audit_2026-05-12.md",
    "docs/augment_monitor_variables_diagnostics.py",
    "docs/generate_monitor_rename_table.py",
    "docs/monitor_variable_rename_map.yaml",
    "docs/python_def_inventory.json",
    "docs/regenerate_monitor_emitted_variables_by_architecture.py",
    "docs/regenerate_monitor_variable_usage_gap_analysis.py",
    "docs/regenerate_monitor_variables_catalog.py",
    "docs/upgrade.md",
    "docs/using-the-website-as-a-researcher.md",
    "monitor/docs/C_REFACTOR_CHECKLIST.md",
    "monitor/docs/METRICS_REFERENCE.md",
    "monitor/docs/RAW_DATA_FORMAT.md",
    "monitor/docs/metric_descriptions.tsv",
    "monitor/docs/plans/PLAN_TEMPLATE.md",
  }
)

# Evergreen pages written by a generator (S10). Each must carry
# GENERATED_LINE_RE as the first line after its H1, and a page that carries
# the line must be listed here, so lint-hook exclusions and freshness tests
# can read one list. Add the output path in the commit that adds the
# generator.
GENERATED_DOCS: frozenset[str] = frozenset()

GENERATED_LINE_RE = re.compile(r"^Generated by `([^`]+)`; do not edit\b")
HISTORY_BANNER_RE = re.compile(
  r"^Historical record, \d{4}-\d{2}-\d{2}\. Not maintained\.$"
)
KEBAB_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+$")
# A purpose line is prose: not a heading, list item, fence, table, quote or
# alert, HTML block, or link-reference definition.
_NON_PROSE_RE = re.compile(
  r"^(?:#|[-*+]\s|\d+[.)]\s|```|~~~|\||>|<|\[[^\]]+\]:)"
)

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_INLINE_LINK_RE = re.compile(
  r"!?\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
_REF_USE_RE = re.compile(r"(?<![\]\\])\[([^\]]+)\]\[([^\]]*)\]")
_REF_DEF_RE = re.compile(r"^ {0,3}\[([^\]^][^\]]*)\]:\s*(\S+)")
_HTML_ANCHOR_RE = re.compile(
  r"<a\s[^>]*(?:id|name)=\"([^\"]+)\"", re.IGNORECASE
)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_SLUG_DROP_RE = re.compile(r"[^\w\- ]")
_MD_LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@cache
def _repo_files() -> tuple[str, ...]:
  out = subprocess.run(
    [
      "git",
      "-C",
      str(_REPO),
      "ls-files",
      "--cached",
      "--others",
      "--exclude-standard",
      "-z",
    ],
    check=True,
    capture_output=True,
    text=True,
  ).stdout
  return tuple(
    sorted(p for p in set(out.split("\0")) if p and (_REPO / p).is_file())
  )


def _doc_tree_files() -> list[str]:
  return [p for p in _repo_files() if p.startswith(DOC_ROOTS)]


def _is_evergreen(rel: str) -> bool:
  return rel in EVERGREEN_FILES or rel.startswith(EVERGREEN_DIRS)


def _evergreen_pages() -> list[str]:
  return [
    p for p in _doc_tree_files() if p.endswith(".md") and _is_evergreen(p)
  ]


def _link_checked_pages() -> list[str]:
  extra = [p for p in LINK_CHECKED_EXTRA if (_REPO / p).is_file()]
  return sorted(set(_evergreen_pages()) | set(extra))


def _placement_error(rel: str) -> str | None:
  if rel in LEGACY_ALLOWED or rel.startswith(RECORD_DIRS):
    return None
  if rel in EVERGREEN_FILES:
    return None
  for prefix in EVERGREEN_DIRS:
    if rel.startswith(prefix):
      if "/" in rel[len(prefix) :]:
        return "nested directory inside a quadrant directory"
      return None
  return "not in a quadrant or record directory (docs-structure-and-style.mdc)"


@cache
def _text(rel: str) -> str:
  return (_REPO / rel).read_text(encoding="utf-8")


def _prose_lines(text: str) -> list[tuple[int, str, bool]]:
  """Return (line number, line, inside_fence) for every line of ``text``."""
  rows = []
  fence: str | None = None
  for n, line in enumerate(text.splitlines(), 1):
    match = _FENCE_RE.match(line)
    if fence is None and match:
      fence = match.group(1)
      rows.append((n, line, True))
      continue
    if fence is not None:
      closing = line.strip()
      if closing.startswith(fence[0] * len(fence)) and not closing.strip(
        fence[0]
      ):
        fence = None
      rows.append((n, line, True))
      continue
    rows.append((n, line, False))
  return rows


def _headings(text: str) -> list[tuple[int, int, str]]:
  """Return (line number, level, heading text) for ATX headings."""
  out = []
  for n, line, fenced in _prose_lines(text):
    if fenced:
      continue
    match = _ATX_RE.match(line)
    if match:
      out.append((n, len(match.group(1)), (match.group(2) or "").strip()))
  return out


def github_slug(heading: str) -> str:
  """Return the GitHub anchor for a heading's Markdown source text."""
  text = _MD_LINK_TEXT_RE.sub(r"\1", heading)
  text = _HTML_TAG_RE.sub("", text).replace("`", "").replace("*", "")
  return _SLUG_DROP_RE.sub("", text.strip().lower()).replace(" ", "-")


@cache
def _anchors(rel: str) -> frozenset[str]:
  text = _text(rel)
  seen: dict[str, int] = {}
  anchors = set(_HTML_ANCHOR_RE.findall(text))
  for _n, _level, heading in _headings(text):
    slug = github_slug(heading)
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    anchors.add(slug if count == 0 else f"{slug}-{count}")
  return frozenset(anchors)


def _first_line_after_h1(text: str) -> tuple[int, str] | None:
  lines = text.splitlines()
  for n, line in enumerate(lines[1:], 2):
    if line.strip():
      return n, line.strip()
  return None


def _link_targets(text: str) -> tuple[list[tuple[int, str]], list[str]]:
  """Return inline link targets and errors for undefined reference links."""
  rows = [(n, line) for n, line, fenced in _prose_lines(text) if not fenced]
  defs: dict[str, str] = {}
  for _n, line in rows:
    match = _REF_DEF_RE.match(line)
    if match:
      defs[match.group(1).strip().lower()] = match.group(2)
  targets: list[tuple[int, str]] = []
  errors: list[str] = []
  for n, line in rows:
    match = _REF_DEF_RE.match(line)
    if match:
      targets.append((n, match.group(2).strip("<>")))
      continue
    line = line.replace("\\[", "  ").replace("\\]", "  ")
    line = _CODE_SPAN_RE.sub(lambda m: "x" * len(m.group(0)), line)
    for link in _INLINE_LINK_RE.finditer(line):
      targets.append((n, link.group(2).strip("<>")))
    for ref in _REF_USE_RE.finditer(_INLINE_LINK_RE.sub("", line)):
      label = (ref.group(2) or ref.group(1)).strip().lower()
      if label not in defs:
        errors.append(f"{n}: reference link [{label}] has no definition")
  return targets, errors


def _target_error(page: str, target: str) -> str | None:
  if _SCHEME_RE.match(target) or target.startswith("//"):
    return None
  path_part, _, fragment = target.partition("#")
  path_part = urllib.parse.unquote(path_part)
  fragment = urllib.parse.unquote(fragment)
  if path_part.startswith("/"):
    resolved = (_REPO / path_part.lstrip("/")).resolve()
  elif path_part:
    resolved = ((_REPO / page).parent / path_part).resolve()
  else:
    resolved = (_REPO / page).resolve()
  try:
    rel = resolved.relative_to(_REPO.resolve()).as_posix()
  except ValueError:
    return f"{target}: points outside the repository"
  parts = rel.split("/")
  if parts[0] == "test_runs" or ".cursor" in parts:
    return f"{target}: links into test_runs/ or .cursor/ are not allowed"
  if not resolved.exists():
    return f"{target}: {rel} does not exist"
  if fragment and resolved.is_file() and resolved.suffix == ".md":
    if fragment not in _anchors(rel):
      return f"{target}: no heading with anchor #{fragment} in {rel}"
  return None


def test_doc_files_are_in_allowed_directories():
  bad = [
    f"{rel}: {err}"
    for rel in _doc_tree_files()
    if (err := _placement_error(rel)) is not None
  ]
  assert not bad, "\n".join(bad)


def test_legacy_allowed_entries_still_exist():
  files = set(_repo_files())
  stale = sorted(p for p in LEGACY_ALLOWED if p not in files)
  assert not stale, (
    "remove moved or deleted paths from LEGACY_ALLOWED:\n" + "\n".join(stale)
  )


def test_no_readme_inside_docs_tree():
  bad = [
    rel
    for rel in _doc_tree_files()
    if Path(rel).name.lower() == "readme.md"
    and rel not in ALLOWED_READMES
    and (rel.startswith("docs/") or _is_evergreen(rel))
  ]
  assert not bad, (
    "docs/index.md is the docs map; no README.md under docs/ or in the "
    "monitor quadrants:\n" + "\n".join(bad)
  )


def test_evergreen_file_names_are_kebab_case():
  bad = [
    rel
    for rel in _doc_tree_files()
    if _is_evergreen(rel) and not KEBAB_NAME_RE.match(Path(rel).name)
  ]
  assert not bad, "\n".join(bad)


def test_evergreen_pages_have_h1_and_purpose_line():
  bad = []
  for rel in _evergreen_pages():
    text = _text(rel)
    h1s = [n for n, level, _h in _headings(text) if level == 1]
    if h1s != [1]:
      bad.append(f"{rel}: needs exactly one H1, on line 1 (found {h1s})")
      continue
    first = _first_line_after_h1(text)
    if first is None:
      bad.append(f"{rel}: no purpose line after the H1")
      continue
    n, line = first
    generated = GENERATED_LINE_RE.match(line)
    if rel in GENERATED_DOCS:
      if not generated:
        bad.append(f"{rel}:{n}: generated page needs the generator line")
      elif not (_REPO / generated.group(1).split()[0]).is_file():
        bad.append(f"{rel}:{n}: generator {generated.group(1)} not found")
    elif generated:
      bad.append(f"{rel}:{n}: generator line but not in GENERATED_DOCS")
    elif _NON_PROSE_RE.match(line):
      bad.append(f"{rel}:{n}: purpose line must be prose: {line[:60]}")
  assert not bad, "\n".join(bad)


def test_generated_docs_exist_and_are_evergreen():
  bad = [
    rel
    for rel in GENERATED_DOCS
    if not (_REPO / rel).is_file() or not _is_evergreen(rel)
  ]
  assert not bad, "\n".join(bad)


def test_history_pages_carry_record_banner():
  bad = []
  for rel in _doc_tree_files():
    if not (rel.startswith("docs/history/") and rel.endswith(".md")):
      continue
    text = _text(rel)
    first = _first_line_after_h1(text)
    if not text.startswith("# ") or first is None:
      bad.append(f"{rel}: needs an H1 on line 1 and the record banner")
    elif not HISTORY_BANNER_RE.match(first[1]):
      bad.append(f"{rel}:{first[0]}: expected 'Historical record, <DATE>.'")
  assert not bad, "\n".join(bad)


@pytest.mark.parametrize("rel", _link_checked_pages())
def test_relative_links_and_anchors_resolve(rel):
  targets, errors = _link_targets(_text(rel))
  for n, target in targets:
    err = _target_error(rel, target)
    if err:
      errors.append(f"{n}: {err}")
  assert not errors, f"{rel}:\n" + "\n".join(errors)


def test_github_slug_matches_github_rules():
  assert github_slug("Docs tree and placement") == "docs-tree-and-placement"
  assert github_slug("`[PIPELINE] key` and `sync_timedb`") == (
    "pipeline-key-and-sync_timedb"
  )
  assert github_slug("Section 4.5 Backfill lane") == "section-45-backfill-lane"
  assert github_slug("Run the [linters](x.md) (pre-push)") == (
    "run-the-linters-pre-push"
  )


def test_link_parser_skips_code_and_flags_undefined_refs():
  text = (
    "See [a](b.md#c) and `[x](not-a-link.md)` and [d][e] and [f][].\n"
    "```bash\n[g](fenced.md)\n```\n"
    "[e]: https://example.org\n"
  )
  targets, errors = _link_targets(text)
  assert targets == [(1, "b.md#c"), (5, "https://example.org")]
  assert errors == ["1: reference link [f] has no definition"]


def test_target_errors_cover_missing_files_anchors_and_banned_dirs():
  page = "docs/reference/documentation-style.md"
  assert _target_error(page, "#docs-tree-and-placement") is None
  assert _target_error(page, "#no-such-heading")
  assert _target_error(page, "../no-such-file.md")
  assert _target_error(page, "../../test_runs/x.md")
  assert _target_error(page, "../../../.cursor/plans/x.md")
  assert _target_error(page, "https://example.org/x") is None


def test_placement_rejects_new_nonconforming_files():
  assert _placement_error("docs/NEW_TOPIC.md")
  assert _placement_error("docs/artifacts/new_report.md")
  assert _placement_error("docs/how-to/no-audience.md")
  assert _placement_error("docs/reference/sub/page.md")
  assert _placement_error("monitor/docs/NEW.md")
  assert _placement_error("docs/how-to/operate/upgrade-the-stack.md") is None
  assert _placement_error("docs/history/x-2026-11-27.md") is None
  assert _placement_error("docs/measurements/any/thing.txt") is None
