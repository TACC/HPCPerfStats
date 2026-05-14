import { describe, expect, it } from "vitest";
import {
  JOB_COUNT_COLOR_MAX,
  JOB_COUNT_COLOR_MIN,
  jobCountToHeatColor,
} from "./jobCountHeatColor";

describe("jobCountToHeatColor", () => {
  it("maps 1 job toward purple (high hue) and 100 toward red (low hue)", () => {
    const low = jobCountToHeatColor(1);
    const high = jobCountToHeatColor(100);
    expect(low).toMatch(/^hsl\(/);
    expect(high).toMatch(/^hsl\(/);
    const hueLow = Number(low.match(/hsl\(([\d.]+)/)[1]);
    const hueHigh = Number(high.match(/hsl\(([\d.]+)/)[1]);
    expect(hueLow).toBeGreaterThan(hueHigh);
  });

  it("clamps below min and above max", () => {
    expect(jobCountToHeatColor(0)).toBe(
      jobCountToHeatColor(JOB_COUNT_COLOR_MIN),
    );
    expect(jobCountToHeatColor(500)).toBe(
      jobCountToHeatColor(JOB_COUNT_COLOR_MAX),
    );
  });
});
