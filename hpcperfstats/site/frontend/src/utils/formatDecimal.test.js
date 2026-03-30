import { describe, expect, it } from "vitest";
import { formatDecimalStandard } from "./formatDecimal";

describe("formatDecimalStandard", () => {
  it("uses decimal notation for large magnitudes", () => {
    expect(formatDecimalStandard(1.23e12)).toBe("1,230,000,000,000");
  });

  it("uses decimal notation for small fractions", () => {
    expect(formatDecimalStandard(1.23e-7)).toBe("0.000000123");
  });

  it("returns empty string for null-like inputs", () => {
    expect(formatDecimalStandard(null)).toBe("");
    expect(formatDecimalStandard(undefined)).toBe("");
    expect(formatDecimalStandard("")).toBe("");
  });
});
