/**
 * Job Detail plot-tab gate from job_data.performance.sort_rank.
 * Copy is plot-tab specific (not Performance Data column labels).
 */

export const PLOT_TAB_NO_PLOTS_AVAILABLE = "No plots available for this job";
export const PLOT_TAB_PLOTS_NOT_YET_COMPLETED = "Plots not yet completed.";

/** Poll job_detail while rank is transitional (ranks 1 / 6). */
export const JOB_DETAIL_PERFORMANCE_POLL_INTERVAL_MS = 60_000;
export const JOB_DETAIL_PERFORMANCE_POLL_MAX_ATTEMPTS = 20;

export type JobDetailPerformanceBadge = {
  sort_rank?: number | null;
  label?: string | null;
  tone?: string | null;
  aria_label?: string | null;
};

export function jobDetailPlotPerformanceSortRank(
  performance: JobDetailPerformanceBadge | null | undefined,
): number | null {
  const rank = performance?.sort_rank;
  if (typeof rank !== "number" || !Number.isFinite(rank)) return null;
  return rank;
}

/** Rank 0 = Metrics & Plots available — only then load plot payloads. */
export function isJobDetailPlotsPerformanceReady(rank: number | null): boolean {
  return rank === 0;
}

/** Ranks 1 and 6: wait / refetch detail until rank 0 or give up. */
export function isJobDetailPlotPerformanceTransitional(rank: number | null): boolean {
  return rank === 1 || rank === 6;
}

/** Ranks 2–5 or missing: terminal — no plot fetch. */
export function isJobDetailPlotPerformanceTerminalUnavailable(
  rank: number | null,
): boolean {
  if (rank === null) return true;
  return rank >= 2 && rank <= 5;
}

export function isJobDetailAnalysisPlotTab(tab: string): boolean {
  return tab === "summary" || tab === "roofline" || tab === "multiprecisionMix";
}

/**
 * Message shown inside Summary / Roofline / Multiprecision Mix when not rank 0.
 * Returns null when plots may load (rank 0).
 */
export function jobDetailPlotGateMessage(rank: number | null): string | null {
  if (rank === 0) return null;
  if (rank === 1 || rank === 6) return PLOT_TAB_PLOTS_NOT_YET_COMPLETED;
  return PLOT_TAB_NO_PLOTS_AVAILABLE;
}
