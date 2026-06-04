import { describe, expect, it } from "vitest";
import {
  buildJobListFilterSummaryLines,
  hasExtendedSearchFilters,
  isExtendedSearchJobsRoute,
} from "./job-list-filter-summary";

describe("buildJobListFilterSummaryLines", () => {
  it("formats extended search query params", () => {
    const params = new URLSearchParams({
      runtime__gte: "3600",
      queue: "normal",
      end_time__gte: "2024-01-01",
    });
    const lines = buildJobListFilterSummaryLines(params);
    expect(lines.some((l) => l.includes("3600"))).toBe(true);
    expect(lines.some((l) => l.includes("normal"))).toBe(true);
    expect(lines.some((l) => l.includes("2024-01-01"))).toBe(true);
  });

  it("includes metric thresholds", () => {
    const params = new URLSearchParams({ metrics_avg_cpuusage__gte: "50" });
    expect(buildJobListFilterSummaryLines(params).length).toBeGreaterThan(0);
  });
});

describe("isExtendedSearchJobsRoute", () => {
  it("detects /jobs paths", () => {
    expect(isExtendedSearchJobsRoute("/jobs")).toBe(true);
    expect(isExtendedSearchJobsRoute("/year/2024")).toBe(false);
  });
});

describe("hasExtendedSearchFilters", () => {
  it("is false for pagination-only params", () => {
    expect(hasExtendedSearchFilters(new URLSearchParams({ page: "2" }))).toBe(false);
  });
});
