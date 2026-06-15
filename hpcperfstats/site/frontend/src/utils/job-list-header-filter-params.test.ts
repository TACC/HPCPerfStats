import { describe, expect, it, vi } from "vitest";
import {
  applyHeaderFilterChange,
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
});
