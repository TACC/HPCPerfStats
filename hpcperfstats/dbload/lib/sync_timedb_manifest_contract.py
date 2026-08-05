"""
Manifest phase enums and required-field registry for sync_timedb sidecars.

Attributes:
  CHECKPOINT_ENTRY_REQUIRED_KEYS: Attribute.
  DAY_PHASE_ORDER: Attribute.
  MANIFEST_REQUIRED_TOP_LEVEL: Attribute.
  PHASE_DELETING: Attribute.
  PHASE_DONE: Attribute.
  PHASE_VERIFICATION_COMPLETE: Attribute.
  PHASE_VERIFYING: Attribute.
  RAW_REMOVAL_PHASES: Attribute.
  UNPARSABLE_ENTRY_REQUIRED_KEYS: Attribute.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

# Raw-removal coordinators (startup + per-day).
PHASE_VERIFYING = "verifying"
PHASE_VERIFICATION_COMPLETE = "verification_complete"
PHASE_DELETING = "deleting"
PHASE_DONE = "done"

RAW_REMOVAL_PHASES: FrozenSet[str] = frozenset(
    {
        PHASE_VERIFYING,
        PHASE_VERIFICATION_COMPLETE,
        PHASE_DELETING,
        PHASE_DONE,
    },
)

# Required top-level keys per persistence kind (after envelope unwrap where applicable).
MANIFEST_REQUIRED_TOP_LEVEL: Dict[str, Tuple[str, ...]] = {
    "day_raw_removal": ("phase", "tar_path"),
    "day_close_manifest": ("entries",),
    "archive_maint_hints": ("version",),
}

UNPARSABLE_ENTRY_REQUIRED_KEYS: Tuple[str, ...] = (
    "original_path",
    "quarantined_path",
    "reason",
)

CHECKPOINT_ENTRY_REQUIRED_KEYS: Tuple[str, ...] = ("path", "size", "mtime")

# Janitor cold-path day phase ordering (archive_maint_hints.day_phases).
DAY_PHASE_ORDER: Dict[str, int] = {
    "sealed": 1,
    "raw_removed": 2,
    "tar_dropped": 3,
}


def day_phase_name_from_hints(day_phases: Any, tar_path: str) -> Optional[str]:
  """
  Day phase name from hints.
  
  Args:
    day_phases (Any): Day phases passed to this helper.
    tar_path (str): String for tar path.
  
  Returns:
    Optional[str]: Optional[str] — the result, or None when unavailable.
  
  Examples:
    >>> day_phase_name_from_hints(None, "x")  # doctest: +SKIP
  """
  import os

  tar_norm = os.path.normpath(str(tar_path or ""))
  phase = (day_phases or {}).get(tar_norm)
  if isinstance(phase, dict):
    return phase.get("phase")
  return phase


def day_phase_at_least(day_phases: Any, tar_path: str, target: str) -> bool:
  """
  True when persisted hint phase for ``tar_path`` is >= ``target``.
  
  Args:
    day_phases (Any): Day phases passed to this helper.
    tar_path (str): String for tar path.
    target (str): String for target.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> day_phase_at_least(None, "x", "x")  # doctest: +SKIP
  """
  phase_name = day_phase_name_from_hints(day_phases, tar_path)
  if phase_name not in DAY_PHASE_ORDER or target not in DAY_PHASE_ORDER:
    return False
  return DAY_PHASE_ORDER[phase_name] >= DAY_PHASE_ORDER[target]


def manifest_phase_is_valid(kind: str, phase: str) -> bool:
  """
  Return True if the manifest phase is is valid.
  
  Args:
    kind (str): String for kind.
    phase (str): String for phase.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> manifest_phase_is_valid("x", "x")  # doctest: +SKIP
  """
  if kind == "day_raw_removal":
    return phase in RAW_REMOVAL_PHASES
  return True


def validate_manifest_payload(kind: str, payload: Any) -> bool:
  """
  Return True when ``payload`` has required top-level keys for ``kind``.
  
  Args:
    kind (str): String for kind.
    payload (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> validate_manifest_payload("x", None)  # doctest: +SKIP
  """
  if not isinstance(payload, dict):
    return False
  required = MANIFEST_REQUIRED_TOP_LEVEL.get(kind)
  if not required:
    return True
  if not all(key in payload for key in required):
    return False
  phase = payload.get("phase")
  if phase is not None and not manifest_phase_is_valid(kind, str(phase)):
    return False
  return True
