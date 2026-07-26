/**
 * Decode DCGM GPU clock throttle / clock event reason bitmasks
 * (dcgm_fields.h DCGM_CLOCKS_THROTTLE_REASON_* / field 112).
 *
 * Persisted metric max_gpu_clock_event_reasons stays numeric; this is display-only.
 */

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

/** Known bits OR'd together (for residual / unknown detection). */
const KNOWN_BITS_MASK = DCGM_CLOCK_THROTTLE_REASON_FLAGS.reduce(
  (acc, { bit }) => acc | bit,
  0,
);

/**
 * Format a DCGM clock-throttle reason bitmask as comma-separated flag names.
 * Returns "" for empty / non-finite / zero masks (caller keeps no-data path).
 */
export function formatGpuClockThrottleReasons(mask: number): string {
  if (!Number.isFinite(mask)) return "";
  const intMask = Math.trunc(mask);
  if (intMask === 0) return "";

  const unsigned = intMask >>> 0;
  const parts: string[] = [];
  for (const { bit, label } of DCGM_CLOCK_THROTTLE_REASON_FLAGS) {
    if (unsigned & bit) parts.push(label);
  }
  const unknown = unsigned & ~KNOWN_BITS_MASK;
  if (unknown) {
    parts.push(`unknown (0x${unknown.toString(16)})`);
  }
  return parts.join(", ");
}
