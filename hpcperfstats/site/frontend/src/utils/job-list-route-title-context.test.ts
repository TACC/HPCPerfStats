import { describe, expect, it } from "vitest";
import { jobListPageHumanSummary, jobListRouteTitleContext } from "./job-list-route-title-context";

describe("jobListRouteTitleContext", () => {
  it("returns empty string when no route or query hints", () => {
    expect(jobListRouteTitleContext({}, new URLSearchParams())).toBe("");
  });
});

describe("jobListPageHumanSummary", () => {
  it("describes year slice", () => {
    expect(jobListPageHumanSummary({ year: "2024" })).toContain("2024");
    expect(jobListPageHumanSummary({ year: "2024" })).toContain("calendar year");
  });

  it("describes query-only date slice", () => {
    expect(jobListPageHumanSummary({ date: "2024-06-01" })).toContain("2024-06-01");
  });

  it("returns null for generic jobs route", () => {
    expect(jobListPageHumanSummary({})).toBeNull();
  });
});
