"""
Decode DCGM GPU clock throttle / clock event reason bitmasks.

Mirrors ``dcgm_fields.h`` ``DCGM_CLOCKS_THROTTLE_REASON_*`` and the SPA helper
``gpuClockThrottleReasons.ts``. Display-only; persisted metrics stay numeric.

Blank-family and no-known-bit garbage masks return ``""`` (never ``unknown
(0x…)``).

Attributes:
  DCGM_CLOCK_THROTTLE_REASON_FLAGS: ``DCGM_CLOCK_THROTTLE_REASON_FLAGS``.
  _KNOWN_BITS_MASK: ``_KNOWN_BITS_MASK``.
"""

from __future__ import annotations

from hpcperfstats.lib.dcgm_blank import is_dcgm_numeric_blank

DCGM_CLOCK_THROTTLE_REASON_FLAGS: tuple[tuple[int, str], ...] = (
    (0x1, "GPU idle"),
    (0x2, "Application clocks setting"),
    (0x4, "SW power cap"),
    (0x8, "HW slowdown (temp/power/Pstate)"),
    (0x10, "Sync boost"),
    (0x20, "SW thermal slowdown"),
    (0x40, "HW thermal slowdown"),
    (0x80, "HW power brake"),
    (0x100, "Display clocks setting"),
)

_KNOWN_BITS_MASK = 0
for _bit, _ in DCGM_CLOCK_THROTTLE_REASON_FLAGS:
  _KNOWN_BITS_MASK |= _bit


def format_gpu_clock_throttle_reasons(mask: float | int | None) -> str:
  """
  Format a DCGM clock-throttle bitmask as comma-separated flag names.
  
  Returns ``""`` for missing / non-finite / zero / blank / no-known-bit masks.
  Known bits only — residual garbage hex is never shown.
  
  Args:
    mask (float | int | None): One of ``float``, ``int``, ``None``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> format_gpu_clock_throttle_reasons(None)  # doctest: +SKIP
  """
  if mask is None:
    return ""
  if is_dcgm_numeric_blank(mask):
    return ""
  try:
    value = float(mask)
  except (TypeError, ValueError):
    return ""
  if value != value:  # NaN
    return ""
  int_mask = int(value)
  if int_mask == 0:
    return ""

  # Match JS >>> 0 for non-negative display of the low 32 bits.
  unsigned = int_mask & 0xFFFFFFFF
  parts: list[str] = []
  for bit, label in DCGM_CLOCK_THROTTLE_REASON_FLAGS:
    if unsigned & bit:
      parts.append(label)
  # Fail closed: unknown residual alone or with known bits is omitted.
  return ", ".join(parts)
