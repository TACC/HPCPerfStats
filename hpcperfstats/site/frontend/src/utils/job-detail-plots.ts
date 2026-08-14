import type { BokehJsonItem } from "@/types/bokeh";
import type { JobPlotBatchResponse, JobPlotsState } from "@/types/view-models";
import { fingerprintBokehJsonItem } from "@/utils/fingerprint-bokeh-json-item";

export type JobPlotConfigKey = "summary_plot" | "roofline" | "gpu_roofline";

export type GpuRooflineBwAxis = "memory_bw" | "pcie_nvlink";

type PlotBatchFields = { item: string; reason: string };

export const JOB_PLOT_CONFIGS = [
  {
    key: "summary_plot" as const,
    panelKey: "summary" as const,
    idPrefix: "job-mscript",
    plotName: "Summary plots",
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
    plotName: "GPU Roofline",
  },
] as const;

const JOB_PLOTS_BATCH_FIELDS: Record<JobPlotConfigKey, PlotBatchFields> = {
  summary_plot: { item: "mplot_item", reason: "mplot_unavailable_reason" },
  roofline: { item: "rplot_item", reason: "rplot_unavailable_reason" },
  gpu_roofline: { item: "grplot_item", reason: "grplot_unavailable_reason" },
};

/** Map API ``grplot_bw_axis`` to the Job Detail panel title. */
export function gpuRooflinePlotName(bwAxis: unknown): string {
  if (bwAxis === "memory_bw") return "GPU Roofline (Memory BW)";
  if (bwAxis === "pcie_nvlink") return "GPU Roofline (PCIe/NvLink)";
  return "GPU Roofline";
}

function plotEntryFromBatchFields(
  resp: JobPlotBatchResponse,
  fields: PlotBatchFields,
  key: JobPlotConfigKey,
): JobPlotsState[string] {
  const entry: JobPlotsState[string] = {
    loading: false,
    plotItem: (resp[fields.item] as BokehJsonItem | null | undefined) ?? null,
    unavailableReason: (resp[fields.reason] as string | null | undefined) ?? null,
  };
  if (key === "gpu_roofline") {
    entry.bwAxis = (resp.grplot_bw_axis as GpuRooflineBwAxis | null | undefined) ?? null;
  }
  return entry;
}

export function createEmptyJobPlotsState(loading: boolean): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    acc[config.key] = {
      loading,
      plotItem: null,
      unavailableReason: null,
      ...(config.key === "gpu_roofline" ? { bwAxis: null } : {}),
    };
    return acc;
  }, {});
}

export function plotsStateFromBatchResponse(resp: JobPlotBatchResponse): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    acc[config.key] = plotEntryFromBatchFields(resp, fields, config.key);
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
      ...(config.key === "gpu_roofline" ? { bwAxis: null } : {}),
    };
    if (loadingSet.has(config.key)) {
      acc[config.key] = {
        loading: true,
        plotItem: previous.plotItem,
        unavailableReason: previous.unavailableReason,
        ...(config.key === "gpu_roofline"
          ? { bwAxis: previous.bwAxis ?? null }
          : {}),
      };
      return acc;
    }
    if (Object.hasOwn(resp, fields.item)) {
      acc[config.key] = plotEntryFromBatchFields(resp, fields, config.key);
      return acc;
    }
    // Kind absent from loading_plots and missing item field: do not re-spin completed slots.
    const completed =
      previous.plotItem != null || previous.unavailableReason != null;
    acc[config.key] = {
      ...previous,
      loading: completed ? false : previous.loading,
    };
    return acc;
  }, {});
}

/** Clear progressive loading flags while retaining last good items (poll-cap fail-closed). */
export function clearJobPlotsLoadingFlags(plots: JobPlotsState | null): JobPlotsState {
  const base = plots ?? createEmptyJobPlotsState(false);
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const previous = base[config.key] ?? {
      loading: false,
      plotItem: null,
      unavailableReason: null,
    };
    acc[config.key] = { ...previous, loading: false };
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
  if ((p.bwAxis ?? null) !== (q.bwAxis ?? null)) return false;
  if (p.plotItem === q.plotItem) return true;
  if (p.plotItem == null && q.plotItem == null) return true;
  if (p.plotItem == null || q.plotItem == null) return false;
  return (
    fingerprintBokehJsonItem(p.plotItem) === fingerprintBokehJsonItem(q.plotItem)
  );
}

export function jobPlotStatesEqual(
  a: JobPlotsState | null | undefined,
  b: JobPlotsState | null | undefined,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JOB_PLOT_CONFIGS.every((cfg) => jobPlotEntryEqual(a[cfg.key], b[cfg.key]));
}
