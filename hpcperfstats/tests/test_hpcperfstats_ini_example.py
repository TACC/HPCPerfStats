"""Drift guards for hpcperfstats.ini.example vs conf_parser.INI_OPTION_REGISTRY."""

import re
from pathlib import Path

import pytest

from hpcperfstats import conf_parser as cfg


def _repo_ini_example_path():
  module_dir = Path(__file__).resolve().parents[1]
  return module_dir.parent / "hpcperfstats.ini.example"


_OPTION_LINE_RE = re.compile(
    r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=",
)


def _parse_documented_ini_options(path):
  """Return {(section, option), ...} from active and commented key lines."""
  text = path.read_text(encoding="utf-8")
  section = None
  documented = set()
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      section = stripped[1:-1].strip()
      continue
    if not section or not stripped:
      continue
    match = _OPTION_LINE_RE.match(line)
    if not match:
      continue
    documented.add((section, match.group(1)))
  return documented


def test_ini_example_documents_every_registry_option():
  path = _repo_ini_example_path()
  assert path.is_file(), "missing %s" % path
  documented = _parse_documented_ini_options(path)
  registry = cfg.ini_option_registry_set()
  missing = registry - documented
  assert not missing, (
      "hpcperfstats.ini.example missing documented keys: %s"
      % sorted(missing)
  )


def test_ini_example_has_no_unknown_options():
  path = _repo_ini_example_path()
  documented = _parse_documented_ini_options(path)
  registry = cfg.ini_option_registry_set()
  extra = documented - registry
  assert not extra, (
      "hpcperfstats.ini.example documents unknown keys: %s"
      % sorted(extra)
  )


def test_sync_archive_require_db_head_ingest_not_under_portal():
  path = _repo_ini_example_path()
  text = path.read_text(encoding="utf-8")
  in_portal = False
  for line in text.splitlines():
    stripped = line.strip()
    if stripped == "[PORTAL]":
      in_portal = True
      continue
    if stripped.startswith("[") and stripped.endswith("]"):
      in_portal = stripped == "[PORTAL]"
      continue
    if in_portal and "sync_archive_require_db_head_ingest" in line:
      pytest.fail(
          "sync_archive_require_db_head_ingest must be under [DEFAULT], not [PORTAL]",
      )


def test_ini_example_option_blocks_have_preceding_comment():
  path = _repo_ini_example_path()
  lines = path.read_text(encoding="utf-8").splitlines()
  section = None
  for idx, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      section = stripped[1:-1].strip()
      continue
    match = _OPTION_LINE_RE.match(line)
    if not match or not section:
      continue
    if idx == 0:
      pytest.fail("first key line without comment: %s" % line)
    prev = lines[idx - 1].strip()
    assert prev.startswith("#"), (
        "expected comment immediately above %s in section %s, got: %r"
        % (match.group(1), section, prev)
    )
