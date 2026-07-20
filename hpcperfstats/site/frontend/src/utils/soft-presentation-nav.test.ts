import type { MouseEvent } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  searchParamsFromSamePathHref,
  softPresentationClick,
  softReplacePresentationParams,
} from "./soft-presentation-nav";

describe("soft-presentation-nav", () => {
  it("searchParamsFromSamePathHref parses pathname query", () => {
    const params = searchParamsFromSamePathHref(
      "/machine/jobs/?order_by=username&page=1",
      "/machine/jobs/",
    );
    expect(params.get("order_by")).toBe("username");
    expect(params.get("page")).toBe("1");
  });

  it("softReplacePresentationParams passes scroll:false", () => {
    const replace = vi.fn();
    softReplacePresentationParams(
      { replace },
      "/machine/jobs/",
      new URLSearchParams("order_by=username"),
      new URLSearchParams(),
    );
    expect(replace).toHaveBeenCalledWith("/machine/jobs/?order_by=username", {
      scroll: false,
    });
  });

  it("softPresentationClick prevents default and soft-replaces", () => {
    const replace = vi.fn();
    const preventDefault = vi.fn();
    softPresentationClick(
      {
        defaultPrevented: false,
        button: 0,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
        preventDefault,
      } as unknown as MouseEvent<HTMLAnchorElement>,
      { replace },
      "/machine/jobs/",
      "/machine/jobs/?page=2",
      new URLSearchParams("page=1"),
    );
    expect(preventDefault).toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith("/machine/jobs/?page=2", { scroll: false });
  });
});
