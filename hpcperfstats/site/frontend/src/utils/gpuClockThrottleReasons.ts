/**
 * Decode DCGM GPU clock throttle / clock event reason bitmasks
 * (dcgm_fields.h DCGM_CLOCKS_THROTTLE_REASON_* / field 112).
 *
 * Persisted metric max_gpu_clock_event_reasons stays numeric; this is display-only.
 * Blank-family and no-known-bit garbage masks return "" (never "unknown (0x…)").
 */

/**
 * DCGM FP64 blank base (dcgm_structs.h / 2**47). Also catches INT64 blank-family
 * values: INT64 blank (~2**63) is larger, but is not a JS-safe integer literal
 * (eslint no-loss-of-precision), so SPA blank checks use this threshold only.
 */
const DCGM_FP64_BLANK = 140737488355328;

function isDcgmNumericBlank(mask: number): boolean {
  if (!Number.isFinite(mask)) return false;
  return mask >= DCGM_FP64_BLANK;
}

export const DCGM_CLOCK_THROTTLE_REASON_FLAGS: ReadonlyArray<{
  bit: number;
  label: string;
}> = [
  { bit: 0x1, label: "GPU idle" },
  { bit: 0x2, label: "Application clocks setting" },
  { bit: 0x4, label: "SW power cap" },
  { bit: 0x8, label: "HW slowdown (temp/power/Pstate)" },
  { bit: 0x10, label: "Sync boost" },
  { bit: 0x20, label: "SW thermal slowdown" },
  { bit: 0x40, label: "HW thermal slowdown" },
  { bit: 0x80, label: "HW power brake" },
  { bit: 0x100, label: "Display clocks setting" },
];

/**
 * Format a DCGM clock-throttle reason bitmask as comma-separated flag names.
 * Returns "" for empty / non-finite / zero / blank / no-known-bit masks.
 */
export function formatGpuClockThrottleReasons(mask: number): string {
  if (!Number.isFinite(mask)) return "";
  if (isDcgmNumericBlank(mask)) return "";
  const intMask = Math.trunc(mask);
  if (intMask === 0) return "";

  const unsigned = intMask >>> 0;
  const parts: string[] = [];
  for (const { bit, label } of DCGM_CLOCK_THROTTLE_REASON_FLAGS) {
    if (unsigned & bit) parts.push(label);
  }
  // Fail closed: never emit unknown (0x…) for residual garbage bits.
  return parts.join(", ");
}
