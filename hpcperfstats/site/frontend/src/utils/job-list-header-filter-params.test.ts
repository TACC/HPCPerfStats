import { describe, expect, it, vi } from "vitest";
import {
  applyHeaderFilterChange,
  buildHeaderFilterHref,
  clearAllHeaderFilters,
  hrefFromPathAndSearch,
  parseHeaderFilterSet,
  serializeHeaderFilterSet,
  toggleHeaderFilterValue,
} from "./job-list-header-filter-params";

describe("job-list-header-filter-params", () => {
  it("parseHeaderFilterSet splits comma-separated values", () => {
    const params = new URLSearchParams("queue=normal,debug,normal");
    expect([...parseHeaderFilterSet(params, "queue")]).toEqual(["normal", "debug"]);
  });

  it("toggleHeaderFilterValue adds and removes values", () => {
    const first = toggleHeaderFilterValue(new Set(["a"]), "b");
    expect([...first].sort()).toEqual(["a", "b"]);
    const second = toggleHeaderFilterValue(first, "a");
    expect([...second]).toEqual(["b"]);
  });

  it("serializeHeaderFilterSet returns null for empty set", () => {
    expect(serializeHeaderFilterSet(new Set())).toBeNull();
    expect(serializeHeaderFilterSet(new Set(["x", "y"]))).toBe("x,y");
  });

  it("applyHeaderFilterChange updates queue param and resets page", () => {
    const replace = vi.fn();
    applyHeaderFilterChange({
      router: { replace },
      pathname: "/machine/jobs/",
      searchParams: new URLSearchParams("page=3&order_by=-end_time"),
      routeParams: {},
      key: "queue",
      nextValues: new Set(["normal", "debug"]),
    });
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("queue=normal");
    expect(href).toContain("debug");
    expect(href).not.toContain("page=3");
  });

  it("clearAllHeaderFilters skips replace when filters already cleared", () => {
    const replace = vi.fn();
    const args = {
      router: { replace },
      pathname: "/machine/jobs/",
      searchParams: new URLSearchParams("queue=normal&order_by=-end_time"),
      routeParams: {},
    };
    clearAllHeaderFilters(args);
    expect(replace).toHaveBeenCalledTimes(1);
    clearAllHeaderFilters({
      ...args,
      searchParams: new URLSearchParams("order_by=-end_time"),
    });
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("applyHeaderFilterChange skips replace when href unchanged", () => {
    const replace = vi.fn();
    applyHeaderFilterChange({
      router: { replace },
      pathname: "/machine/jobs/",
      searchParams: new URLSearchParams("queue=normal"),
      routeParams: {},
      key: "queue",
      nextValues: new Set(["normal"]),
    });
    expect(replace).not.toHaveBeenCalled();
  });

  it("date browse route preserves end_time__date when toggling queue", () => {
    const { targetPath, params } = buildHeaderFilterHref({
      pathname: "/machine/date/2024-01-15",
      searchParams: new URLSearchParams(),
      routeParams: { date: "2024-01-15" },
      mutate: (next) => {
        next.set("queue", "normal");
      },
    });
    const href = hrefFromPathAndSearch(targetPath, params);
    expect(targetPath).toBe("/machine/jobs/");
    expect(href).toContain("end_time__date=2024-01-15");
    expect(href).toContain("queue=normal");
  });

  it("year browse route preserves end_time__date when toggling username", () => {
    const { targetPath, params } = buildHeaderFilterHref({
      pathname: "/machine/year/2024",
      searchParams: new URLSearchParams(),
      routeParams: { year: "2024" },
      mutate: (next) => {
        next.set("username", "alice");
      },
    });
    const href = hrefFromPathAndSearch(targetPath, params);
    expect(targetPath).toBe("/machine/jobs/");
    expect(href).toContain("end_time__date=2024");
    expect(href).toContain("username=alice");
  });

  it("extended-search end_time gte/lte preserved when toggling queue", () => {
    const { targetPath, params } = buildHeaderFilterHref({
      pathname: "/machine/jobs/",
      searchParams: new URLSearchParams(
        "end_time__gte=2024-01-01T00%3A00%3A00Z&end_time__lte=2024-01-31T23%3A59%3A59Z",
      ),
      routeParams: {},
      mutate: (next) => {
        next.set("queue", "debug");
      },
    });
    const href = hrefFromPathAndSearch(targetPath, params);
    expect(targetPath).toBe("/machine/jobs/");
    expect(href).toContain("end_time__gte=");
    expect(href).toContain("end_time__lte=");
    expect(href).toContain("queue=debug");
  });

  it("clearAllHeaderFilters preserves time selection keys", () => {
    const replace = vi.fn();
    clearAllHeaderFilters({
      router: { replace },
      pathname: "/machine/jobs/",
      searchParams: new URLSearchParams(
        "end_time__date=2024-01-15&queue=normal&state=completed",
      ),
      routeParams: {},
    });
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("end_time__date=2024-01-15");
    expect(href).not.toContain("queue=");
    expect(href).not.toContain("state=");
  });
});
