/**
 * Freeze settled Job Detail plot payloads for a print session so progressive
 * refetch cannot remount Bokeh embeds mid-prep.
 */

import type { BokehJsonItem } from "@/types/bokeh";
import type { JobPlotsState } from "@/types/view-models";
import { JOB_PLOT_CONFIGS, createEmptyJobPlotsState } from "@/utils/job-detail-plots";

export type PrintMultiprecisionFreeze = {
  cpuItem: BokehJsonItem | null;
  cpuReason: string | null;
  gpuItem: BokehJsonItem | null;
  gpuReason: string | null;
};

/** Merge settled live plot entries into a print freeze (never un-freeze a key). */
export function mergePrintPlotsFreeze(
  prev: JobPlotsState | null,
  live: JobPlotsState | null,
): JobPlotsState | null {
  if (!live) return prev;
  const next: JobPlotsState = prev
    ? { ...prev }
    : createEmptyJobPlotsState(false);
  let changed = false;
  for (const config of JOB_PLOT_CONFIGS) {
    const L = live[config.key];
    if (!L || L.loading) continue;
    // Once a key was frozen into ``prev``, never replace it (including empty settle).
    if (prev?.[config.key] && !prev[config.key].loading) continue;
    next[config.key] = {
      loading: false,
      plotItem: L.plotItem ?? null,
      unavailableReason: L.unavailableReason ?? null,
      ...(config.key === "gpu_roofline" ? { bwAxis: L.bwAxis ?? null } : {}),
    };
    changed = true;
  }
  return changed ? next : prev;
}

/** Freeze multiprecision once both sides have an item or unavailable reason. */
export function mergePrintMultiprecisionFreeze(
  prev: PrintMultiprecisionFreeze | null,
  live: {
    cpuItem: BokehJsonItem | null | undefined;
    cpuReason: string | null | undefined;
    gpuItem: BokehJsonItem | null | undefined;
    gpuReason: string | null | undefined;
  },
): PrintMultiprecisionFreeze | null {
  if (prev) return prev;
  const cpuDone = !!live.cpuItem || !!live.cpuReason;
  const gpuDone = !!live.gpuItem || !!live.gpuReason;
  if (!cpuDone || !gpuDone) return null;
  return {
    cpuItem: live.cpuItem ?? null,
    cpuReason: live.cpuReason ?? null,
    gpuItem: live.gpuItem ?? null,
    gpuReason: live.gpuReason ?? null,
  };
}
