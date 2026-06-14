import { describe, expect, it } from "vitest";
import { jobPlotEntryEqual } from "./job-detail-plots";

describe("jobPlotEntryEqual", () => {
  it("compares plot items by root_id and target_id without JSON.stringify", () => {
    const left = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1", doc: { a: 1 }, version: "3.0.0" },
    };
    const right = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1", doc: { b: 2 }, version: "3.0.0" },
    };
    expect(jobPlotEntryEqual(left, right)).toBe(true);
  });

  it("returns false when root_id differs", () => {
    const base = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1" },
    };
    const other = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r2", target_id: "t1" },
    };
    expect(jobPlotEntryEqual(base, other)).toBe(false);
  });
});
