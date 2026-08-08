/**
 * Job Detail browser-print helpers (Save as PDF via window.print).
 * In-scope: overview, scheduling, resources, Metrics; plus Summary/Roofline/MP at rank 0.
 */

/** Max wait for plots/embeds before opening the print dialog anyway. */
export const JOB_DETAIL_PRINT_READY_TIMEOUT_MS = 20_000;

/** Safety clear of print layout if afterprint never fires. */
export const JOB_DETAIL_PRINT_AFTERPRINT_FALLBACK_MS = 60_000;

/** Copy for ranks that cannot print (2–6 / missing). */
export const JOB_DETAIL_PRINT_NO_DATA_MESSAGE = "There is no data to print.";

export type JobDetailPrintPlotKey =
  | "summary_plot"
  | "roofline"
  | "gpu_roofline"
  | "multiprecision_cpu"
  | "multiprecision_gpu";

export type JobDetailPrintClickAction =
  | "print_with_plots"
  | "print_metrics_only"
  | "no_data";

/**
 * Decide Print click behavior from Performance Data sort_rank.
 * Rank 0: metrics + plots; rank 1: metrics only; 2–6 / missing: no-data dialog.
 */
export function jobDetailPrintClickAction(
  sortRank: number | null,
): JobDetailPrintClickAction {
  if (sortRank === 0) return "print_with_plots";
  if (sortRank === 1) return "print_metrics_only";
  return "no_data";
}

/** A print-scoped plot is settled when not loading and either unavailable or embed-ready. */
export function isPrintScopedPlotSettled(opts: {
  loading: boolean;
  item: unknown;
  embedReady: boolean;
}): boolean {
  if (opts.loading) return false;
  if (!opts.item) return true;
  return opts.embedReady;
}

export type JobDetailPrintReadinessInput = {
  plotsLoading: boolean;
  plotsFetchFailed: boolean;
  summarySettled: boolean;
  rooflineSettled: boolean;
  gpuRooflineSettled: boolean;
  multiprecisionSettled: boolean;
  /** Rank 1: print Metrics (and overview) without waiting for plot embeds. */
  printMetricsOnly?: boolean;
  /**
   * When print includes plots: false until ``useJobPlotsQuery`` has a plots object
   * (or fetch failed). Prevents treating ``plots === null`` as all-settled empty.
   */
  plotsPayloadPresent?: boolean;
  /**
   * Metrics section ready for print: true when tables exist or we are not in a
   * loading-only blank state (empty list while still fetching).
   */
  printMetricsReady?: boolean;
};

/** True when all in-scope print surfaces are ready enough to open the print dialog. */
export function isJobDetailPrintReady(input: JobDetailPrintReadinessInput): boolean {
  if (input.printMetricsReady === false) {
    return false;
  }
  if (input.printMetricsOnly) {
    return true;
  }
  if (!input.plotsFetchFailed && input.plotsPayloadPresent === false) {
    return false;
  }
  if (!input.multiprecisionSettled) return false;
  if (input.plotsFetchFailed) {
    return input.multiprecisionSettled;
  }
  if (input.plotsLoading) return false;
  return (
    input.summarySettled &&
    input.rooflineSettled &&
    input.gpuRooflineSettled &&
    input.multiprecisionSettled
  );
}

/** Multiprecision is settled when detail is not busy fetching it, or both sides have item/reason. */
export function isMultiprecisionPrintSettled(opts: {
  detailsLoading: boolean;
  cpuItem: unknown;
  cpuReason: string | null | undefined;
  gpuItem: unknown;
  gpuReason: string | null | undefined;
}): boolean {
  const cpuDone = !!opts.cpuItem || !!opts.cpuReason;
  const gpuDone = !!opts.gpuItem || !!opts.gpuReason;
  if (cpuDone && gpuDone) return true;
  return !opts.detailsLoading;
}
