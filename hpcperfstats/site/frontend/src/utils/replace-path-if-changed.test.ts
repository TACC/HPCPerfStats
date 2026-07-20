import { describe, expect, it, vi } from "vitest";
import { hrefFromPathAndSearch, replacePathIfChanged } from "./replace-path-if-changed";

describe("replacePathIfChanged", () => {
  it("hrefFromPathAndSearch omits empty query string", () => {
    expect(hrefFromPathAndSearch("/machine/jobs/", new URLSearchParams())).toBe(
      "/machine/jobs/",
    );
    expect(
      hrefFromPathAndSearch("/machine/jobs/", new URLSearchParams("queue=normal")),
    ).toBe("/machine/jobs/?queue=normal");
  });

  it("skips replace when href unchanged", () => {
    const replace = vi.fn();
    const params = new URLSearchParams("order_by=-end_time");
    replacePathIfChanged(
      { replace },
      "/machine/jobs/",
      params,
      "/machine/jobs/",
      new URLSearchParams("order_by=-end_time"),
    );
    expect(replace).not.toHaveBeenCalled();
  });

  it("calls replace when href differs", () => {
    const replace = vi.fn();
    replacePathIfChanged(
      { replace },
      "/machine/jobs/",
      new URLSearchParams("queue=normal"),
      "/machine/jobs/",
      new URLSearchParams(),
    );
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/machine/jobs/?queue=normal");
  });

  it("passes scroll:false when requested", () => {
    const replace = vi.fn();
    replacePathIfChanged(
      { replace },
      "/machine/jobs/",
      new URLSearchParams("order_by=username"),
      "/machine/jobs/",
      new URLSearchParams(),
      { scroll: false },
    );
    expect(replace).toHaveBeenCalledWith("/machine/jobs/?order_by=username", {
      scroll: false,
    });
  });
});
