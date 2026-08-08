import { describe, expect, it } from "vitest";
import {
  JOB_DETAIL_PERFORMANCE_POLL_INTERVAL_MS,
  JOB_DETAIL_PERFORMANCE_POLL_MAX_ATTEMPTS,
  PLOT_TAB_NO_PLOTS_AVAILABLE,
  PLOT_TAB_PLOTS_NOT_YET_COMPLETED,
  isJobDetailAnalysisPlotTab,
  isJobDetailPlotPerformanceTerminalUnavailable,
  isJobDetailPlotPerformanceTransitional,
  isJobDetailPlotsPerformanceReady,
  jobDetailPlotGateMessage,
  jobDetailPlotPerformanceSortRank,
} from "./job-detail-performance-gate";

describe("job-detail-performance-gate", () => {
  it("polls job detail every 60s for up to 20 attempts while transitional", () => {
    expect(JOB_DETAIL_PERFORMANCE_POLL_INTERVAL_MS).toBe(60_000);
    expect(JOB_DETAIL_PERFORMANCE_POLL_MAX_ATTEMPTS).toBe(20);
  });

  it("parses finite sort_rank and fail-closes otherwise", () => {
    expect(jobDetailPlotPerformanceSortRank({ sort_rank: 0 })).toBe(0);
    expect(jobDetailPlotPerformanceSortRank({ sort_rank: 1 })).toBe(1);
    expect(jobDetailPlotPerformanceSortRank(null)).toBeNull();
    expect(jobDetailPlotPerformanceSortRank(undefined)).toBeNull();
    expect(jobDetailPlotPerformanceSortRank({})).toBeNull();
    expect(jobDetailPlotPerformanceSortRank({ sort_rank: Number.NaN })).toBeNull();
  });

  it("treats only rank 0 as plots performance ready", () => {
    expect(isJobDetailPlotsPerformanceReady(0)).toBe(true);
    expect(isJobDetailPlotsPerformanceReady(1)).toBe(false);
    expect(isJobDetailPlotsPerformanceReady(null)).toBe(false);
  });

  it("marks ranks 1 and 6 transitional and 2–5/missing terminal", () => {
    expect(isJobDetailPlotPerformanceTransitional(1)).toBe(true);
    expect(isJobDetailPlotPerformanceTransitional(6)).toBe(true);
    expect(isJobDetailPlotPerformanceTransitional(0)).toBe(false);
    expect(isJobDetailPlotPerformanceTerminalUnavailable(2)).toBe(true);
    expect(isJobDetailPlotPerformanceTerminalUnavailable(5)).toBe(true);
    expect(isJobDetailPlotPerformanceTerminalUnavailable(null)).toBe(true);
    expect(isJobDetailPlotPerformanceTerminalUnavailable(0)).toBe(false);
    expect(isJobDetailPlotPerformanceTerminalUnavailable(1)).toBe(false);
  });

  it("returns user-specified plot-tab gate messages", () => {
    expect(jobDetailPlotGateMessage(0)).toBeNull();
    expect(jobDetailPlotGateMessage(1)).toBe(PLOT_TAB_PLOTS_NOT_YET_COMPLETED);
    expect(jobDetailPlotGateMessage(6)).toBe(PLOT_TAB_PLOTS_NOT_YET_COMPLETED);
    expect(jobDetailPlotGateMessage(2)).toBe(PLOT_TAB_NO_PLOTS_AVAILABLE);
    expect(jobDetailPlotGateMessage(5)).toBe(PLOT_TAB_NO_PLOTS_AVAILABLE);
    expect(jobDetailPlotGateMessage(null)).toBe(PLOT_TAB_NO_PLOTS_AVAILABLE);
  });

  it("identifies Summary, Roofline, and Multiprecision Mix as plot tabs", () => {
    expect(isJobDetailAnalysisPlotTab("summary")).toBe(true);
    expect(isJobDetailAnalysisPlotTab("roofline")).toBe(true);
    expect(isJobDetailAnalysisPlotTab("multiprecisionMix")).toBe(true);
    expect(isJobDetailAnalysisPlotTab("metrics")).toBe(false);
    expect(isJobDetailAnalysisPlotTab("device")).toBe(false);
  });
});
