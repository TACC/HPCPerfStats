import { describe, expect, it } from "vitest";
import {
  createEmptyJobPlotsState,
  jobPlotEntryEqual,
  mergeProgressiveJobPlotsState,
} from "./job-detail-plots";

describe("jobPlotEntryEqual", () => {
  it("treats identical semantic json_item fingerprints as equal when refs differ", () => {
    const plot = { root_id: "r1", target_id: "t1", doc: { a: 1 }, version: "3.0.0" };
    const left = {
      loading: false,
      unavailableReason: null,
      plotItem: { ...plot },
    };
    const right = {
      loading: false,
      unavailableReason: null,
      plotItem: { ...plot },
    };
    expect(left.plotItem).not.toBe(right.plotItem);
    expect(jobPlotEntryEqual(left, right)).toBe(true);
  });

  it("returns false when semantic doc content differs even if root_id matches", () => {
    const left = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1", doc: { a: 1 } },
    };
    const right = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1", doc: { b: 2 } },
    };
    expect(jobPlotEntryEqual(left, right)).toBe(false);
  });

  it("returns false when root_id differs", () => {
    const base = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r1", target_id: "t1" },
    };
    const other = {
      loading: false,
      unavailableReason: null,
      plotItem: { root_id: "r2", target_id: "t1" },
    };
    expect(jobPlotEntryEqual(base, other)).toBe(false);
  });
});

describe("mergeProgressiveJobPlotsState fallthrough", () => {
  it("keeps completed summary/roofline/gpu_roofline loaded when later partial omits their item fields", () => {
    const prev = {
      summary_plot: {
        loading: false,
        plotItem: { root_id: "s" },
        unavailableReason: null,
      },
      roofline: {
        loading: false,
        plotItem: { root_id: "r" },
        unavailableReason: null,
      },
      gpu_roofline: {
        loading: false,
        plotItem: { root_id: "g" },
        unavailableReason: null,
      },
    };
    const resp = {
      status: "partial",
      progressive: true,
      loading_plots: ["roofline"],
      // summary and gpu items omitted (fallthrough path)
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.summary_plot).toEqual({
      loading: false,
      plotItem: { root_id: "s" },
      unavailableReason: null,
    });
    expect(next.gpu_roofline).toEqual({
      loading: false,
      plotItem: { root_id: "g" },
      unavailableReason: null,
    });
    expect(next.roofline.loading).toBe(true);
    expect(next.roofline.plotItem).toEqual({ root_id: "r" });
  });

  it("keeps loading true only when a kind never completed and is omitted", () => {
    const prev = createEmptyJobPlotsState(true);
    const resp = {
      loading_plots: ["summary_plot"],
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.roofline.loading).toBe(true);
    expect(next.gpu_roofline.loading).toBe(true);
    expect(next.summary_plot.loading).toBe(true);
  });
});
