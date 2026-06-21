"""Manifest phase enums and required-field registry for sync_timedb sidecars."""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Tuple

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

# Startup day-close preflight.
PHASE_DISCOVERING = "discovering"

STARTUP_DAY_CLOSE_PHASES: FrozenSet[str] = frozenset(
    {
        PHASE_DISCOVERING,
        PHASE_DONE,
    },
)

# Required top-level keys per persistence kind (after envelope unwrap where applicable).
MANIFEST_REQUIRED_TOP_LEVEL: Dict[str, Tuple[str, ...]] = {
    "startup_raw_removal": ("phase",),
    "day_raw_removal": ("phase", "tar_path"),
    "startup_day_close": ("phase",),
    "async_day_close": ("entries",),
    "archive_maint_hints": ("version",),
}

UNPARSABLE_ENTRY_REQUIRED_KEYS: Tuple[str, ...] = (
    "original_path",
    "quarantined_path",
    "reason",
)

CHECKPOINT_ENTRY_REQUIRED_KEYS: Tuple[str, ...] = ("path", "size", "mtime")


def manifest_phase_is_valid(kind: str, phase: str) -> bool:
  if kind in ("startup_raw_removal", "day_raw_removal"):
    return phase in RAW_REMOVAL_PHASES
  if kind == "startup_day_close":
    return phase in STARTUP_DAY_CLOSE_PHASES
  return True


def validate_manifest_payload(kind: str, payload: Any) -> bool:
  """Return True when ``payload`` has required top-level keys for ``kind``."""
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
