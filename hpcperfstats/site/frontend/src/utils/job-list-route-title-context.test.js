import { describe, expect, it } from "vitest";
import { jobListRouteTitleContext } from "./job-list-route-title-context";

describe("jobListRouteTitleContext", () => {
  it("returns empty string when no route filters", () => {
    expect(jobListRouteTitleContext({}, new URLSearchParams())).toBe("");
  });

  it("includes year and page from route and query", () => {
    const q = new URLSearchParams({ page: "2" });
    expect(jobListRouteTitleContext({ year: "2024" }, q)).toContain("year 2024");
    expect(jobListRouteTitleContext({ year: "2024" }, q)).toContain("page 2");
  });
});
