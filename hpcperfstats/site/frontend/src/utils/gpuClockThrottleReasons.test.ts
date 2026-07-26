import { describe, expect, it } from "vitest";
import { formatGpuClockThrottleReasons } from "./gpuClockThrottleReasons";

describe("formatGpuClockThrottleReasons", () => {
  it("decodes 7 as idle + clocks setting + SW power cap", () => {
    expect(formatGpuClockThrottleReasons(7)).toBe(
      "GPU idle, Application clocks setting, SW power cap",
    );
  });

  it("returns empty string for 0", () => {
    expect(formatGpuClockThrottleReasons(0)).toBe("");
  });

  it("returns empty string for NaN", () => {
    expect(formatGpuClockThrottleReasons(Number.NaN)).toBe("");
  });

  it("truncates float masks like 7.0", () => {
    expect(formatGpuClockThrottleReasons(7.0)).toBe(
      "GPU idle, Application clocks setting, SW power cap",
    );
  });

  it("appends unknown residual bits as hex", () => {
    // 0x200 is not in dcgm_fields.h throttle reasons table used here
    expect(formatGpuClockThrottleReasons(0x200)).toBe("unknown (0x200)");
    expect(formatGpuClockThrottleReasons(0x201)).toBe(
      "GPU idle, unknown (0x200)",
    );
  });

  it("decodes a high known bit alone", () => {
    expect(formatGpuClockThrottleReasons(0x100)).toBe("Display clocks setting");
  });
});
