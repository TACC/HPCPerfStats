import { describe, expect, it } from "vitest";
import {
  isJobDetailPrintReady,
  isMultiprecisionPrintSettled,
  isPrintScopedPlotSettled,
} from "./job-detail-print";

describe("isPrintScopedPlotSettled", () => {
  it("waits while loading", () => {
    expect(
      isPrintScopedPlotSettled({ loading: true, item: { root: 1 }, embedReady: false }),
    ).toBe(false);
  });

  it("treats missing item as settled (unavailable)", () => {
    expect(
      isPrintScopedPlotSettled({ loading: false, item: null, embedReady: false }),
    ).toBe(true);
  });

  it("requires embedReady when an item is present", () => {
    expect(
      isPrintScopedPlotSettled({ loading: false, item: { root: 1 }, embedReady: false }),
    ).toBe(false);
    expect(
      isPrintScopedPlotSettled({ loading: false, item: { root: 1 }, embedReady: true }),
    ).toBe(true);
  });
});

describe("isMultiprecisionPrintSettled", () => {
  it("settles when both sides have reasons without waiting on detailsLoading", () => {
    expect(
      isMultiprecisionPrintSettled({
        detailsLoading: true,
        cpuItem: null,
        cpuReason: "Missing CPU",
        gpuItem: null,
        gpuReason: "Missing GPU",
      }),
    ).toBe(true);
  });

  it("waits on detailsLoading when reasons/items are absent", () => {
    expect(
      isMultiprecisionPrintSettled({
        detailsLoading: true,
        cpuItem: null,
        cpuReason: null,
        gpuItem: null,
        gpuReason: null,
      }),
    ).toBe(false);
    expect(
      isMultiprecisionPrintSettled({
        detailsLoading: false,
        cpuItem: null,
        cpuReason: null,
        gpuItem: null,
        gpuReason: null,
      }),
    ).toBe(true);
  });
});

describe("isJobDetailPrintReady", () => {
  const allSettled = {
    plotsLoading: false,
    plotsFetchFailed: false,
    summarySettled: true,
    rooflineSettled: true,
    gpuRooflineSettled: true,
    multiprecisionSettled: true,
  };

  it("is ready when all plots and multiprecision are settled", () => {
    expect(isJobDetailPrintReady(allSettled)).toBe(true);
  });

  it("waits while plots are loading", () => {
    expect(isJobDetailPrintReady({ ...allSettled, plotsLoading: true })).toBe(false);
  });

  it("allows print when plots fetch failed if multiprecision settled", () => {
    expect(
      isJobDetailPrintReady({
        ...allSettled,
        plotsFetchFailed: true,
        summarySettled: false,
        rooflineSettled: false,
        gpuRooflineSettled: false,
      }),
    ).toBe(true);
  });

  it("blocks when multiprecision is not settled", () => {
    expect(
      isJobDetailPrintReady({ ...allSettled, multiprecisionSettled: false }),
    ).toBe(false);
  });
});
