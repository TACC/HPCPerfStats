/**
 * Job Detail browser-print helpers (Save as PDF via window.print).
 * In-scope: overview, scheduling, resources, Metrics, Summary, Roofline, Multiprecision Mix.
 */

/** Max wait for plots/embeds before opening the print dialog anyway. */
export const JOB_DETAIL_PRINT_READY_TIMEOUT_MS = 20_000;

/** Safety clear of print layout if afterprint never fires. */
export const JOB_DETAIL_PRINT_AFTERPRINT_FALLBACK_MS = 60_000;

export const JOB_DETAIL_PRINT_SCOPED_PANEL_IDS = [
  "job-detail-panel-metrics",
  "job-detail-panel-plot-summary",
  "job-detail-panel-plot-roofline",
  "job-detail-panel-multiprecision-mix",
] as const;

export const JOB_DETAIL_PRINT_OUT_OF_SCOPE_PANEL_IDS = [
  "job-detail-panel-processes",
  "job-detail-panel-exec-hosts",
  "job-detail-panel-device",
] as const;

export type JobDetailPrintPlotKey =
  | "summary_plot"
  | "roofline"
  | "gpu_roofline"
  | "multiprecision_cpu"
  | "multiprecision_gpu";

export const JOB_DETAIL_PRINT_PLOT_KEYS: readonly JobDetailPrintPlotKey[] = [
  "summary_plot",
  "roofline",
  "gpu_roofline",
  "multiprecision_cpu",
  "multiprecision_gpu",
] as const;

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
};

/** True when all in-scope print surfaces are ready enough to open the print dialog. */
export function isJobDetailPrintReady(input: JobDetailPrintReadinessInput): boolean {
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
