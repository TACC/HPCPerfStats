import { describe, expect, it } from "vitest";
import {
  EXTENDED_SEARCH_ALLOWED_PARAM_NAMES,
  EXTENDED_SEARCH_DATE_RANGE_PAIRS,
  EXTENDED_SEARCH_PARAMETER_DEFINITIONS,
  EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS,
} from "./extended-search-parameters";
import { validateExtendedSearchForm } from "./extended-search-validation";

describe("validateExtendedSearchForm", () => {
  it("keeps the search parameter allowlist in sync with the parameter definitions", () => {
    expect(new Set(EXTENDED_SEARCH_ALLOWED_PARAM_NAMES)).toEqual(
      new Set(EXTENDED_SEARCH_PARAMETER_DEFINITIONS.map((param) => param.name)),
    );
    const rangeKeys = [
      ...EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS.flatMap((range) => [range.gteKey, range.lteKey]),
      ...EXTENDED_SEARCH_DATE_RANGE_PAIRS.flatMap((range) => [range.gteKey, range.lteKey]),
    ];
    for (const key of rangeKeys) {
      expect(EXTENDED_SEARCH_ALLOWED_PARAM_NAMES).toContain(key);
    }
  });

  it("rejects completely empty criteria", () => {
    const r = validateExtendedSearchForm({});
    expect(r.ok).toBe(false);
    expect(r.messages.length).toBeGreaterThan(0);
  });

  it("accepts when at least one field is set", () => {
    const r = validateExtendedSearchForm({ host: "n1" });
    expect(r.ok).toBe(true);
  });

  it("rejects non-numeric runtime bounds", () => {
    const r = validateExtendedSearchForm({ runtime__gte: "x" });
    expect(r.ok).toBe(false);
    expect(r.invalidHtmlIds.has("ext-runtime-gte")).toBe(true);
  });

  it("rejects runtime min greater than max", () => {
    const r = validateExtendedSearchForm({
      runtime__gte: "100",
      runtime__lte: "10",
    });
    expect(r.ok).toBe(false);
    expect(r.invalidHtmlIds.has("ext-runtime-gte")).toBe(true);
  });

  it.each(EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS)(
    "validates numeric bounds for $label",
    (range) => {
      const invalid = validateExtendedSearchForm({ [range.gteKey]: "not-a-number" });
      expect(invalid.ok).toBe(false);
      expect(invalid.invalidHtmlIds.has(range.gteId)).toBe(true);

      const reversed = validateExtendedSearchForm({
        [range.gteKey]: "20",
        [range.lteKey]: "10",
      });
      expect(reversed.ok).toBe(false);
      expect(reversed.invalidHtmlIds.has(range.gteId)).toBe(true);
      expect(reversed.invalidHtmlIds.has(range.lteId)).toBe(true);
    },
  );

  it.each(EXTENDED_SEARCH_DATE_RANGE_PAIRS)("validates date bounds for $label", (range) => {
    const invalid = validateExtendedSearchForm({ [range.gteKey]: "2024-02-31" });
    expect(invalid.ok).toBe(false);
    expect(invalid.invalidHtmlIds.has(range.gteId)).toBe(true);

    const reversed = validateExtendedSearchForm({
      [range.gteKey]: "2024-02-02",
      [range.lteKey]: "2024-02-01",
    });
    expect(reversed.ok).toBe(false);
    expect(reversed.invalidHtmlIds.has(range.gteId)).toBe(true);
    expect(reversed.invalidHtmlIds.has(range.lteId)).toBe(true);
  });

  it("validates derived metric min and max fields", () => {
    const options = { metrics: [{ metric: "avg_freq" }] };
    const invalid = validateExtendedSearchForm({ metrics_avg_freq__gte: "x" }, options);
    expect(invalid.ok).toBe(false);
    expect(invalid.invalidHtmlIds.has("ext-metric-0-gte")).toBe(true);

    const reversed = validateExtendedSearchForm(
      { metrics_avg_freq__gte: "3.0", metrics_avg_freq__lte: "2.0" },
      options,
    );
    expect(reversed.ok).toBe(false);
    expect(reversed.invalidHtmlIds.has("ext-metric-0-gte")).toBe(true);
    expect(reversed.invalidHtmlIds.has("ext-metric-0-lte")).toBe(true);
  });
});
