import { describe, expect, it } from "vitest";
import { buildAsyncPageTitle } from "./async-page-title";

describe("buildAsyncPageTitle", () => {
  const base = {
    loadingTitle: "Loading…",
    readyTitle: "Job 42 · cpu",
    fallbackTitle: "Type detail",
  };

  it("returns the loading title while loading", () => {
    expect(
      buildAsyncPageTitle({ ...base, loading: true, hasError: false }),
    ).toBe("Loading…");
  });

  it("returns the ready title when loaded without error", () => {
    expect(
      buildAsyncPageTitle({ ...base, loading: false, hasError: false }),
    ).toBe("Job 42 · cpu");
  });

  it("returns the fallback title when there is an error", () => {
    expect(
      buildAsyncPageTitle({ ...base, loading: false, hasError: true }),
    ).toBe("Type detail");
  });

  it("returns the fallback title when readyTitle is empty", () => {
    expect(
      buildAsyncPageTitle({
        ...base,
        loading: false,
        hasError: false,
        readyTitle: "",
      }),
    ).toBe("Type detail");
  });
});
