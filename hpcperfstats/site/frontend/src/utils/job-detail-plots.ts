import type { BokehJsonItem } from "@/types/bokeh";
import type { JobPlotBatchResponse, JobPlotsState } from "@/types/view-models";

export type JobPlotConfigKey = "summary_plot" | "roofline" | "gpu_roofline";

type PlotBatchFields = { item: string; reason: string };

export const JOB_PLOT_CONFIGS = [
  {
    key: "summary_plot" as const,
    panelKey: "summary" as const,
    idPrefix: "job-mscript",
    plotName: "Summary plot",
  },
  {
    key: "roofline" as const,
    panelKey: "roofline-cpu" as const,
    idPrefix: "job-roofline",
    plotName: "CPU Roofline",
  },
  {
    key: "gpu_roofline" as const,
    panelKey: "roofline-gpu" as const,
    idPrefix: "job-gpu-roofline",
    plotName: "GPU Roofline (PCIe/NvLink)",
  },
] as const;

const JOB_PLOTS_BATCH_FIELDS: Record<JobPlotConfigKey, PlotBatchFields> = {
  summary_plot: { item: "mplot_item", reason: "mplot_unavailable_reason" },
  roofline: { item: "rplot_item", reason: "rplot_unavailable_reason" },
  gpu_roofline: { item: "grplot_item", reason: "grplot_unavailable_reason" },
};

export function createEmptyJobPlotsState(loading: boolean): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    acc[config.key] = {
      loading,
      plotItem: null,
      unavailableReason: null,
    };
    return acc;
  }, {});
}

export function plotsStateFromBatchResponse(resp: JobPlotBatchResponse): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    acc[config.key] = {
      loading: false,
      plotItem: (resp[fields.item] as BokehJsonItem | null | undefined) ?? null,
      unavailableReason: (resp[fields.reason] as string | null | undefined) ?? null,
    };
    return acc;
  }, {});
}

/** Merge a progressive `job_plots` partial payload into existing per-plot state. */
export function mergeProgressiveJobPlotsState(
  prevPlots: JobPlotsState | null,
  resp: JobPlotBatchResponse,
): JobPlotsState {
  const loadingSet = new Set(resp.loading_plots ?? []);
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    const previous = prevPlots?.[config.key] ?? {
      loading: true,
      plotItem: null,
      unavailableReason: null,
    };
    if (loadingSet.has(config.key)) {
      acc[config.key] = {
        loading: true,
        plotItem: previous.plotItem,
        unavailableReason: previous.unavailableReason,
      };
      return acc;
    }
    if (Object.hasOwn(resp, fields.item)) {
      acc[config.key] = {
        loading: false,
        plotItem: (resp[fields.item] as BokehJsonItem | null | undefined) ?? null,
        unavailableReason: (resp[fields.reason] as string | null | undefined) ?? null,
      };
      return acc;
    }
    acc[config.key] = { ...previous, loading: true };
    return acc;
  }, {});
}

export function jobPlotEntryEqual(
  p: JobPlotsState[string] | null | undefined,
  q: JobPlotsState[string] | null | undefined,
): boolean {
  if (p === q) return true;
  if (!p || !q) return false;
  if (p.loading !== q.loading || p.unavailableReason !== q.unavailableReason) return false;
  if (p.plotItem === q.plotItem) return true;
  if (p.plotItem == null && q.plotItem == null) return true;
  if (p.plotItem == null || q.plotItem == null) return false;
  const left = p.plotItem as Record<string, unknown>;
  const right = q.plotItem as Record<string, unknown>;
  return left.root_id === right.root_id && left.target_id === right.target_id;
}

export function jobPlotStatesEqual(
  a: JobPlotsState | null | undefined,
  b: JobPlotsState | null | undefined,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JOB_PLOT_CONFIGS.every((cfg) => jobPlotEntryEqual(a[cfg.key], b[cfg.key]));
}
