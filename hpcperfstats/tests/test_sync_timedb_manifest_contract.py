"""Drift guard for sync_timedb manifest phase and required-field contracts."""
from __future__ import annotations

from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
    CHECKPOINT_ENTRY_REQUIRED_KEYS,
    MANIFEST_REQUIRED_TOP_LEVEL,
    PHASE_DELETING,
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
    RAW_REMOVAL_PHASES,
    UNPARSABLE_ENTRY_REQUIRED_KEYS,
    manifest_phase_is_valid,
    validate_manifest_payload,
)


def test_raw_removal_phases_stable():
  assert RAW_REMOVAL_PHASES == frozenset(
      {
          PHASE_VERIFYING,
          PHASE_VERIFICATION_COMPLETE,
          PHASE_DELETING,
          PHASE_DONE,
      },
  )


def test_manifest_required_fields_registry_covers_coordinators():
  assert "day_raw_removal" in MANIFEST_REQUIRED_TOP_LEVEL
  assert "day_close_manifest" in MANIFEST_REQUIRED_TOP_LEVEL


def test_validate_manifest_payload_rejects_invalid_phase():
  assert not validate_manifest_payload(
      "day_raw_removal",
      {"phase": "not_a_phase"},
  )
  assert validate_manifest_payload(
      "day_raw_removal",
      {"phase": PHASE_VERIFYING, "tar_path": "/daily/2020-01-01.tar"},
  )


def test_unparsable_and_checkpoint_entry_keys_non_empty():
  assert UNPARSABLE_ENTRY_REQUIRED_KEYS
  assert CHECKPOINT_ENTRY_REQUIRED_KEYS


def test_manifest_phase_is_valid_for_day_raw_removal():
  assert manifest_phase_is_valid("day_raw_removal", PHASE_DELETING)
  assert not manifest_phase_is_valid("day_raw_removal", "discovering")
