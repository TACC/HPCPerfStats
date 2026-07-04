import { useEffect, useState } from "react";
import {
  fetchJobMonitorGpuPatches,
  mergeJobMonitorGpuPatches,
} from "@/utils/job-monitor-gpu";

type GpuPatchRow = { username?: string };

/** Loads GPU rollup patches for job-monitor rows after base monitor data is ready. */
export function useJobMonitorGpuPatches<T extends GpuPatchRow>(
  rows: T[],
  responseDays: number | undefined,
  enabled: boolean,
) {
  const [patchedRows, setPatchedRows] = useState(rows);

  useEffect(() => {
    setPatchedRows(rows);
    if (!enabled || rows.length === 0) return;
    let cancelled = false;
    void fetchJobMonitorGpuPatches(rows, responseDays).then((patches) => {
      if (cancelled) return;
      setPatchedRows((prev) => mergeJobMonitorGpuPatches(prev, patches) as T[]);
    });
    return () => {
      cancelled = true;
    };
  }, [rows, responseDays, enabled]);

  return patchedRows;
}
