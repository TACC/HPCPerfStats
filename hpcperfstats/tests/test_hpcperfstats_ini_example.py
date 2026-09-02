"""Drift guards for hpcperfstats.ini.example vs conf_parser.INI_OPTION_REGISTRY."""

import re
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.ini_section_placement import (
    validate_registry_sections,
)


def _repo_ini_example_path():
  module_dir = Path(__file__).resolve().parents[1]
  return module_dir.parent / "hpcperfstats.ini.example"


_OPTION_LINE_RE = re.compile(
    r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=",
)

_ACTIVE_OPTION_LINE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$",
)


def _ini_values_equal_registry_default(example_value, registry_default):
  """True when *example_value* matches *registry_default* (exact or float)."""
  if registry_default is None:
    return False
  if example_value == registry_default:
    return True
  try:
    return float(example_value) == float(registry_default)
  except (TypeError, ValueError):
    return False


def _parse_active_ini_options(path):
  """Return {(section, option): value} for uncommented key lines only."""
  text = path.read_text(encoding="utf-8")
  section = None
  active = {}
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      section = stripped[1:-1].strip()
      continue
    if not section or not stripped or stripped.startswith("#"):
      continue
    match = _ACTIVE_OPTION_LINE_RE.match(stripped)
    if not match:
      continue
    active[(section, match.group(1))] = match.group(2)
  return active


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


def test_ssl_certs_dir_removed_from_registry_and_example():
  """TLS authority moved to docker-compose.settings.yaml proxy_ssl_source volume."""
  registry_options = {option for _section, option, _default in cfg.INI_OPTION_REGISTRY}
  assert "ssl_certs_dir" not in registry_options
  text = _repo_ini_example_path().read_text(encoding="utf-8")
  assert "ssl_certs_dir" not in text


def test_dead_day_close_knobs_removed_from_registry_and_example():
  """Startup inflight, days_per_tick, and dead seal/defer/wait knobs stay gone."""
  dead = {
      "sync_startup_day_close_max_inflight",
      "archive_janitor_days_per_tick",
      "archive_seal_idle_seconds",
      "archive_maintenance_max_defer_seconds",
      "sync_day_close_raw_removal_wait_seconds",
      "sync_cold_path_max_concurrent_seals",
      "sync_dispatch_step_size",
      "metrics_scheduler_compute_threads",
      "sync_archive_require_db_head_ingest",
      "sync_day_close_async_stale_seconds",
      "archive_pool_process_cap",
      "sync_archive_pool_process_cap",
      "sync_budget_archive_ratio",
      "sync_budget_min_archive_percent",
      "sync_overprovision_archive_multiplier",
  }
  registry_options = {option for _section, option, _default in cfg.INI_OPTION_REGISTRY}
  assert not (dead & registry_options)
  path = _repo_ini_example_path()
  text = path.read_text(encoding="utf-8")
  for key in dead:
    # Word-boundary match so ``archive_pool_process_cap`` does not hit
    # ``sync_archive_pool_processes``.
    assert not re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(key), text), (
        "dead key still documented in example: %s" % key
    )


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


def test_sync_archive_require_db_ingest_under_pipeline_not_portal():
  path = _repo_ini_example_path()
  text = path.read_text(encoding="utf-8")
  current = None
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      current = stripped[1:-1].strip()
      continue
    if "sync_archive_require_db_ingest" not in line:
      continue
    assert current == "PIPELINE", (
        "sync_archive_require_db_ingest must be under [PIPELINE], not [%s]"
        % current
    )


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
  assert cfg.ini_registry_default("sync_archive_members_populate_pool_processes") == "4"
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


def test_ini_example_active_keys_do_not_equal_registry_defaults():
  """Active example lines must not duplicate INI_OPTION_DEFAULTS (cleanliness)."""
  path = _repo_ini_example_path()
  active = _parse_active_ini_options(path)
  defaults = {
      (section, option): default
      for section, option, default in cfg.INI_OPTION_REGISTRY
  }
  redundant = []
  for key, value in sorted(active.items()):
    default = defaults.get(key)
    if _ini_values_equal_registry_default(value, default):
      redundant.append("%s.%s=%s (default %r)" % (key[0], key[1], value, default))
  assert not redundant, (
      "hpcperfstats.ini.example has active keys at registry default "
      "(comment them out): %s" % redundant
  )


def test_ini_values_equal_registry_default_float_normalize():
  assert _ini_values_equal_registry_default("24", "24.0")
  assert _ini_values_equal_registry_default("40", "40")
  assert not _ini_values_equal_registry_default("True", "no")
  assert not _ini_values_equal_registry_default("x", None)


def test_ini_example_has_no_pipeline_interpreter_abi_keys():
  """Pipeline ABI is baked in supervisord; INI must not offer interpreter switches."""
  path = _repo_ini_example_path()
  text = path.read_text(encoding="utf-8")
  forbidden = (
      "listend_interpreter",
      "sync_timedb_interpreter",
      "update_metrics_interpreter",
      "pipeline_interpreter",
  )
  for key in forbidden:
    assert key not in text, key
  registry_options = {
      option for _section, option, _default in cfg.INI_OPTION_REGISTRY
  }
  for key in forbidden:
    assert key not in registry_options, key

