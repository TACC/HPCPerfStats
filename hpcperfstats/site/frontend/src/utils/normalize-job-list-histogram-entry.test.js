import { describe, expect, it } from "vitest";
import { normalizeJobListHistogramEntry } from "./normalize-job-list-histogram-entry";

describe("normalizeJobListHistogramEntry", () => {
  it("returns null for nullish entry", () => {
    expect(normalizeJobListHistogramEntry(null)).toBeNull();
    expect(normalizeJobListHistogramEntry(undefined)).toBeNull();
  });

  it("uses title from queue plot shape", () => {
    const row = normalizeJobListHistogramEntry({
      title: "Jobs by queue",
      plot_item_thumb: { a: 1 },
      plot_item_full: { b: 2 },
      plot_unavailable_reason: "x",
    });
    expect(row.title).toBe("Jobs by queue");
    expect(row.plot_unavailable_reason).toBe("x");
  });

  it("falls back to metric field then fallbackTitle", () => {
    const row = normalizeJobListHistogramEntry(
      {
        metric: "runtime",
        plot_item_thumb: null,
        plot_item_full: null,
      },
      "runtime",
    );
    expect(row.title).toBe("runtime");
  });
});
