import { describe, expect, it } from "vitest";
import { formatRuntimeHms } from "./formatRuntimeHms";

describe("formatRuntimeHms", () => {
  it("formats whole hours as HH:MM:SS", () => {
    expect(formatRuntimeHms(3600)).toBe("01:00:00");
  });

  it("zero-pads minutes and seconds", () => {
    expect(formatRuntimeHms(3661)).toBe("01:01:01");
  });

  it("keeps hours unbounded past 24", () => {
    expect(formatRuntimeHms(90061)).toBe("25:01:01");
  });

  it("rounds fractional seconds", () => {
    expect(formatRuntimeHms(0.4)).toBe("00:00:00");
    expect(formatRuntimeHms(0.6)).toBe("00:00:01");
  });

  it("returns empty string for null-like inputs", () => {
    expect(formatRuntimeHms(null)).toBe("");
    expect(formatRuntimeHms(undefined)).toBe("");
    expect(formatRuntimeHms("")).toBe("");
  });

  it("clamps negative values to zero", () => {
    expect(formatRuntimeHms(-12)).toBe("00:00:00");
  });
});
