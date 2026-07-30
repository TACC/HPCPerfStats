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

  it("returns empty for unknown residual / garbage masks", () => {
    expect(formatGpuClockThrottleReasons(0x200)).toBe("");
    expect(formatGpuClockThrottleReasons(0x6b48c000)).toBe("");
  });

  it("keeps known bits and drops residual garbage", () => {
    expect(formatGpuClockThrottleReasons(0x201)).toBe("GPU idle");
  });

  it("returns empty for DCGM blank family (FP64 base and INT64-scale)", () => {
    // FP64 blank base (exact). INT64 blank (~2**63) is not a safe JS literal —
    // use 2**63 (exact IEEE754) as the float-scale stand-in.
    expect(formatGpuClockThrottleReasons(140737488355328)).toBe("");
    expect(formatGpuClockThrottleReasons(2 ** 63)).toBe("");
  });

  it("decodes a high known bit alone", () => {
    expect(formatGpuClockThrottleReasons(0x100)).toBe("Display clocks setting");
  });
});
