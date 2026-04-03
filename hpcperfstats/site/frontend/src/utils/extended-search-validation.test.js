import { describe, expect, it } from "vitest";
import { validateExtendedSearchForm } from "./extended-search-validation";

describe("validateExtendedSearchForm", () => {
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
});
