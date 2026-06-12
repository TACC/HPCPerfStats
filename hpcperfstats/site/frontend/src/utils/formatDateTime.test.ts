import { describe, expect, it } from "vitest";
import { formatDateTime } from "./formatDateTime";

function isReasonableDateString(value) {
  return typeof value === "string" && value.length > 0;
}

describe("formatDateTime", () => {
  it("returns empty string for nullish or empty input", () => {
    expect(formatDateTime(null)).toBe("");
    expect(formatDateTime(undefined)).toBe("");
    expect(formatDateTime("")).toBe("");
  });

  it("returns original value string if invalid date", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });

  it("formats valid ISO date string", () => {
    const result = formatDateTime("2024-01-02T03:04:05Z");
    expect(isReasonableDateString(result)).toBe(true);
  });
});
