"""Drift guards for hpcperfstats.ini.example vs conf_parser.INI_OPTION_REGISTRY."""

import re
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.ini_section_placement import (
    DEFAULT_PINNING_OPTIONS,
    validate_registry_sections,
)


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


def test_ini_example_registry_matches_section_placement_contract():
  violations = validate_registry_sections(cfg.INI_OPTION_REGISTRY)
  assert not violations, violations


def test_sync_archive_require_db_head_ingest_under_pipeline_not_portal():
  path = _repo_ini_example_path()
  text = path.read_text(encoding="utf-8")
  current = None
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      current = stripped[1:-1].strip()
      continue
    if "sync_archive_require_db_head_ingest" not in line:
      continue
    assert current == "PIPELINE", (
        "sync_archive_require_db_head_ingest must be under [PIPELINE], not [%s]"
        % current
    )


def test_default_pinning_keys_are_last_in_section():
  path = _repo_ini_example_path()
  lines = path.read_text(encoding="utf-8").splitlines()
  in_default = False
  default_keys = []
  for line in lines:
    stripped = line.strip()
    if stripped == "[DEFAULT]":
      in_default = True
      continue
    if in_default and stripped.startswith("[") and stripped.endswith("]"):
      break
    match = _OPTION_LINE_RE.match(line)
    if in_default and match:
      default_keys.append(match.group(1))
  assert default_keys, "expected documented keys under [DEFAULT]"
  pinning = [k for k in default_keys if k in DEFAULT_PINNING_OPTIONS]
  non_pinning = [k for k in default_keys if k not in DEFAULT_PINNING_OPTIONS]
  assert pinning, "expected cpuset/pinning keys documented in [DEFAULT]"
  assert default_keys[-len(pinning):] == pinning, (
      "cpuset/pinning keys must be last in [DEFAULT]; order was %s"
      % default_keys
  )
  assert not any(k in DEFAULT_PINNING_OPTIONS for k in non_pinning[len(non_pinning):])


def test_no_duplicate_options_across_sections_in_example():
  path = _repo_ini_example_path()
  documented = _parse_documented_ini_options(path)
  by_option = {}
  for section, option in documented:
    by_option.setdefault(option, set()).add(section)
  duplicates = {opt: sections for opt, sections in by_option.items() if len(sections) > 1}
  assert not duplicates, "options documented in multiple sections: %s" % duplicates


def test_ini_option_registry_defaults_are_strings_or_none():
  """Registry third element is None (required key) or a str code default."""
  for section, option, default in cfg.INI_OPTION_REGISTRY:
    assert default is None or isinstance(default, str), (
        "%s.%s default must be None or str, got %r" % (section, option, default)
    )
  assert cfg.ini_registry_default("sync_archive_members_populate_pool_processes") == "2"
  assert cfg.ini_option_registry_set() == {
      (section, option) for section, option, _default in cfg.INI_OPTION_REGISTRY
  }


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
