"""Drift guards for ini section placement vs INI_OPTION_REGISTRY."""

import pytest

from hpcperfstats import conf_parser as cfg
from hpcperfstats.ini_section_placement import (
    DEFAULT_PINNING_OPTIONS,
    DEFAULT_POSTGRES_OPTIONS,
    PORTAL_WEB_TUNING_OPTIONS,
    expected_section,
    validate_registry_sections,
)


def test_ini_option_registry_matches_section_placement_contract():
  violations = validate_registry_sections(cfg.INI_OPTION_REGISTRY)
  assert not violations, "section placement violations: %s" % violations


def test_pipeline_prefix_keys_not_in_default_or_portal():
  for section, option in cfg.INI_OPTION_REGISTRY:
    if section not in ("DEFAULT", "PORTAL"):
      continue
    if option.startswith("sync_") or option.startswith("metrics_"):
      pytest.fail(
          "pipeline prefix key %r must not be under %r" % (option, section)
      )


def test_archive_keys_not_in_portal():
  for section, option in cfg.INI_OPTION_REGISTRY:
    if option.startswith("archive_") and section != "PIPELINE":
      pytest.fail("archive key %r must be PIPELINE, got %r" % (option, section))


def test_postgres_connection_keys_in_default():
  for option in DEFAULT_POSTGRES_OPTIONS:
    matches = [s for s, o in cfg.INI_OPTION_REGISTRY if o == option]
    assert matches == ["DEFAULT"], (
        "expected %r only in DEFAULT, got %r" % (option, matches)
    )


def test_portal_registry_keys_match_allowlist():
  portal_options = {option for section, option in cfg.INI_OPTION_REGISTRY if section == "PORTAL"}
  assert portal_options == set(PORTAL_WEB_TUNING_OPTIONS)


def test_expected_section_covers_every_registry_option():
  for _section, option in cfg.INI_OPTION_REGISTRY:
    assert expected_section(option) == _section
