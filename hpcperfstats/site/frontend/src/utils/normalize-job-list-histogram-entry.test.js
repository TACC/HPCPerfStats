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
      plot_item_thumb: {
        doc: { roots: [{ id: "thumb-root" }] },
        root_ids: ["thumb-root"],
      },
      plot_item_full: {
        doc: { roots: [{ id: "full-root" }] },
        root_ids: ["full-root"],
      },
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

  it("treats invalid bokeh payload as unavailable instead of passing through", () => {
    const row = normalizeJobListHistogramEntry({
      title: "Jobs by queue",
      plot_item_thumb: { doc: {}, root_ids: [null] },
      plot_item_full: undefined,
    });
    expect(row.plot_item_thumb).toBeNull();
    expect(row.plot_item_full).toBeNull();
    expect(row.plot_unavailable_reason).toContain("invalid");
  });

  it("accepts doc.roots as list of root id strings (alternate Bokeh wire shape)", () => {
    const thumb = {
      doc: { roots: ["p2001"] },
      root_id: "p2001",
    };
    const row = normalizeJobListHistogramEntry({
      title: "Jobs by queue",
      plot_item_thumb: thumb,
      plot_item_full: thumb,
    });
    expect(row.plot_item_thumb).toBe(thumb);
    expect(row.plot_unavailable_reason).toBeNull();
  });

  it("accepts Bokeh 3 json_item shape with root_id", () => {
    const bokeh3Thumb = {
      doc: { roots: [{ id: "p1001" }] },
      root_id: "p1001",
      version: "3.9.0",
    };
    const bokeh3Full = {
      doc: { roots: [{ id: "p1002" }] },
      root_id: "p1002",
      version: "3.9.0",
    };
    const row = normalizeJobListHistogramEntry({
      title: "Jobs by queue",
      plot_item_thumb: bokeh3Thumb,
      plot_item_full: bokeh3Full,
    });
    expect(row.plot_item_thumb).toBe(bokeh3Thumb);
    expect(row.plot_item_full).toBe(bokeh3Full);
    expect(row.plot_unavailable_reason).toBeNull();
  });

  it("rejects payload when root id is not declared in doc roots", () => {
    const row = normalizeJobListHistogramEntry({
      title: "Jobs by queue",
      plot_item_thumb: {
        doc: { roots: [{ id: "different-root" }] },
        root_id: "thumb-root",
      },
      plot_item_full: null,
    });
    expect(row.plot_item_thumb).toBeNull();
    expect(row.plot_unavailable_reason).toContain("invalid");
  });
});
