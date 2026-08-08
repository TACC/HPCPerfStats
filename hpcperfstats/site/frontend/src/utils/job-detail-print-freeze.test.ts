import { describe, expect, it } from "vitest";
import {
  mergePrintMultiprecisionFreeze,
  mergePrintPlotsFreeze,
} from "./job-detail-print-freeze";
import { createEmptyJobPlotsState } from "./job-detail-plots";

describe("mergePrintPlotsFreeze", () => {
  it("freezes settled entries and ignores later churn on the same key", () => {
    const live1 = createEmptyJobPlotsState(false);
    live1.summary_plot = {
      loading: false,
      plotItem: { root_id: "a" },
      unavailableReason: null,
    };
    const frozen = mergePrintPlotsFreeze(null, live1);
    expect(frozen?.summary_plot.plotItem).toEqual({ root_id: "a" });

    const live2 = createEmptyJobPlotsState(false);
    live2.summary_plot = {
      loading: false,
      plotItem: { root_id: "b" },
      unavailableReason: null,
    };
    const again = mergePrintPlotsFreeze(frozen, live2);
    expect(again).toBe(frozen);
    expect(again?.summary_plot.plotItem).toEqual({ root_id: "a" });
  });

  it("skips still-loading keys", () => {
    const live = createEmptyJobPlotsState(true);
    expect(mergePrintPlotsFreeze(null, live)).toBeNull();
  });
});

describe("mergePrintMultiprecisionFreeze", () => {
  it("freezes once both sides settled", () => {
    expect(
      mergePrintMultiprecisionFreeze(null, {
        cpuItem: null,
        cpuReason: null,
        gpuItem: null,
        gpuReason: "missing",
      }),
    ).toBeNull();

    const frozen = mergePrintMultiprecisionFreeze(null, {
      cpuItem: { p: 1 },
      cpuReason: null,
      gpuItem: null,
      gpuReason: "missing",
    });
    expect(frozen?.cpuItem).toEqual({ p: 1 });
    expect(frozen?.gpuReason).toBe("missing");

    const again = mergePrintMultiprecisionFreeze(frozen, {
      cpuItem: { p: 2 },
      cpuReason: null,
      gpuItem: { g: 1 },
      gpuReason: null,
    });
    expect(again).toBe(frozen);
  });
});
