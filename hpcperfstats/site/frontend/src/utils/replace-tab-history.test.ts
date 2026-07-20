import { afterEach, describe, expect, it, vi } from "vitest";
import { replaceTabInHistory } from "./replace-tab-history";

describe("replaceTabInHistory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls history.replaceState when tab changes", () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      history: { state: { as: 1 }, replaceState },
    });
    replaceTabInHistory(
      "/machine/job/1/",
      new URLSearchParams(),
      "tab",
      "summary",
    );
    expect(replaceState).toHaveBeenCalledWith(
      { as: 1 },
      "",
      "/machine/job/1/?tab=summary",
    );
  });

  it("skips replaceState when href unchanged", () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      history: { state: null, replaceState },
    });
    replaceTabInHistory(
      "/machine/job/1/",
      new URLSearchParams("tab=summary"),
      "tab",
      "summary",
    );
    expect(replaceState).not.toHaveBeenCalled();
  });

  it("omits default tab from query", () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      history: { state: null, replaceState },
    });
    replaceTabInHistory(
      "/machine/job/1/",
      new URLSearchParams("tab=summary"),
      "tab",
      null,
    );
    expect(replaceState).toHaveBeenCalledWith(null, "", "/machine/job/1/");
  });
});
