import { describe, expect, it } from "vitest";
import {
  isHollowBokehJsonItem,
  restoreStrippedBokehFields,
} from "@/api/restore-stripped-bokeh-fields";
import { parseApiResponse } from "@/api/parse-api-response";

const richThumb = {
  doc: {
    root_ids: ["p1006"],
    roots: [
      {
        id: "p1006",
        type: "object",
        name: "Figure",
        attributes: { title: "Number of jobs by cpu hours" },
      },
    ],
  },
  root_id: "p1006",
};

const hollowThumb = {
  doc: {
    root_ids: ["p1006"],
    roots: [{ id: "p1006" }],
  },
  root_id: "p1006",
};

describe("restore-stripped-bokeh-fields", () => {
  it("detects hollow id-only roots (Orval strip failure mode)", () => {
    expect(isHollowBokehJsonItem(hollowThumb)).toBe(true);
    expect(isHollowBokehJsonItem(richThumb)).toBe(false);
  });

  it("restores plot_item_thumb from raw when Zod left hollow stubs", () => {
    const raw = {
      nj: 3,
      histograms: [
        {
          metric: "runtime",
          plot_item_thumb: richThumb,
          plot_item_full: richThumb,
        },
      ],
    };
    const parsedHollow = {
      nj: 3,
      histograms: [
        {
          metric: "runtime",
          plot_item_thumb: hollowThumb,
          plot_item_full: hollowThumb,
        },
      ],
    };
    const restored = restoreStrippedBokehFields(raw, parsedHollow) as typeof raw;
    expect(restored.histograms[0].plot_item_thumb).toEqual(richThumb);
    expect(restored.histograms[0].plot_item_full).toEqual(richThumb);
  });

  it("parseApiResponse keeps rich Bokeh when registry Zod is opaque", () => {
    const wire = {
      nj: 3,
      histogram_nj: 3,
      histogram_sampled: false,
      histograms: [
        {
          group: "metric",
          metric: "runtime",
          title: "Runtime",
          plot_item_thumb: richThumb,
          plot_item_full: richThumb,
          plot_unavailable_reason: null,
        },
      ],
    };
    const parsed = parseApiResponse("GET", "/api/jobs/histograms/batch/", wire) as typeof wire;
    expect(parsed.histograms[0].plot_item_thumb).toEqual(richThumb);
  });
});
