import { describe, expect, it } from "vitest";
import { buildJobListBreadcrumbs } from "./job-list-breadcrumbs";

describe("buildJobListBreadcrumbs", () => {
  it("returns browse plus terminal label for the default jobs route", () => {
    expect(buildJobListBreadcrumbs({})).toEqual([
      { label: "Browse", to: "/" },
      { label: "Jobs" },
    ]);
  });

  it("adds a year crumb when routeParams.year is set", () => {
    expect(buildJobListBreadcrumbs({ year: "2024" })).toEqual([
      { label: "Browse", to: "/" },
      { label: "Year 2024", to: "/year/2024" },
      { label: "Jobs" },
    ]);
  });

  it("prefers the first matching scope param in priority order", () => {
    expect(buildJobListBreadcrumbs({ year: "2024", queue: "normal" })).toEqual([
      { label: "Browse", to: "/" },
      { label: "Year 2024", to: "/year/2024" },
      { label: "Jobs" },
    ]);
  });

  it("encodes username and account segments in links", () => {
    expect(buildJobListBreadcrumbs({ username: "alice@site" })).toEqual([
      { label: "Browse", to: "/" },
      { label: "User alice@site", to: "/username/alice%40site" },
      { label: "Jobs" },
    ]);
    expect(buildJobListBreadcrumbs({ account: "proj/a" }, "Project jobs")).toEqual([
      { label: "Browse", to: "/" },
      { label: "Project proj/a", to: "/account/proj%2Fa" },
      { label: "Project jobs" },
    ]);
  });

  it("supports date, queue, and host scopes", () => {
    expect(buildJobListBreadcrumbs({ date: "2024-01-15" })[1]).toEqual({
      label: "Date 2024-01-15",
      to: "/date/2024-01-15",
    });
    expect(buildJobListBreadcrumbs({ queue: "gpu" })[1]).toEqual({
      label: "Queue gpu",
      to: "/queue/gpu",
    });
    expect(buildJobListBreadcrumbs({ host: "n1.cluster" })[1]).toEqual({
      label: "Host n1.cluster",
      to: "/host/n1.cluster",
    });
  });
});
