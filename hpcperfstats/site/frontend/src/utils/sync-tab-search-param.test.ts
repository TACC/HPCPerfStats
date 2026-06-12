import { describe, expect, it } from "vitest";
import { readTabFromSearchParams, searchParamsWithTab } from "./sync-tab-search-param";

describe("searchParamsWithTab", () => {
  it("sets the tab param when a value is provided", () => {
    const next = searchParamsWithTab(new URLSearchParams("page=2"), "tab", "metrics");
    expect(next.get("tab")).toBe("metrics");
    expect(next.get("page")).toBe("2");
  });

  it("deletes the tab param when value is null or empty", () => {
    const base = new URLSearchParams("tab=plots&page=1");
    expect(searchParamsWithTab(base, "tab", null).has("tab")).toBe(false);
    expect(searchParamsWithTab(base, "tab", "").has("tab")).toBe(false);
    expect(searchParamsWithTab(base, "tab", null).get("page")).toBe("1");
  });
});

describe("readTabFromSearchParams", () => {
  it("returns the stored tab when present", () => {
    const params = new URLSearchParams("tab=roofline");
    expect(readTabFromSearchParams(params, "tab", "overview")).toBe("roofline");
  });

  it("falls back to the default tab when missing or blank", () => {
    expect(readTabFromSearchParams(new URLSearchParams(), "tab", "overview")).toBe(
      "overview",
    );
    expect(readTabFromSearchParams(new URLSearchParams("tab=   "), "tab", "overview")).toBe(
      "overview",
    );
  });
});
