import { describe, expect, it } from "vitest";
import { formatDecimalStandard } from "./formatDecimal";

describe("formatDecimalStandard", () => {
  it("uses decimal notation with two fraction digits for large magnitudes", () => {
    expect(formatDecimalStandard(1.23e12)).toBe("1,230,000,000,000.00");
  });

  it("uses decimal notation with two fraction digits for small fractions", () => {
    expect(formatDecimalStandard(1.23e-7)).toBe("0.00");
  });

  it("uses two decimal places for whole numbers", () => {
    expect(formatDecimalStandard(7)).toBe("7.00");
  });

  it("returns empty string for null-like inputs", () => {
    expect(formatDecimalStandard(null)).toBe("");
    expect(formatDecimalStandard(undefined)).toBe("");
    expect(formatDecimalStandard("")).toBe("");
  });
});
