import { describe, expect, it } from "vitest";
import {
  buildJobListActiveFilterLines,
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

  it("includes browse end_time__date and sort order", () => {
    const params = new URLSearchParams({
      end_time__date: "2024-01-15",
      queue: "normal",
    });
    const lines = buildJobListFilterSummaryLines(params, "-end_time");
    expect(lines.some((l) => l.includes("2024-01-15"))).toBe(true);
    expect(lines.some((l) => l.includes("normal"))).toBe(true);
    expect(lines.some((l) => l.toLowerCase().includes("sort:"))).toBe(true);
  });

  it("includes metric thresholds", () => {
    const params = new URLSearchParams({ metrics_avg_cpuusage__gte: "50" });
    expect(buildJobListFilterSummaryLines(params).length).toBeGreaterThan(0);
  });
});

describe("buildJobListActiveFilterLines", () => {
  it("merges route date into active filters on /jobs query routes", () => {
    const lines = buildJobListActiveFilterLines(
      new URLSearchParams("end_time__date=2024-01-15&queue=gpu"),
      {},
      { orderBy: "-runtime" },
    );
    expect(lines.some((l) => l.includes("2024-01-15"))).toBe(true);
    expect(lines.some((l) => l.includes("gpu"))).toBe(true);
    expect(lines.some((l) => l.includes("Sort:"))).toBe(true);
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
