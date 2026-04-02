const JOB_MONITOR_GPU_SORT_KEYS = new Set([
  "gpu_active_percentage",
  "gpu_count_total",
  "gpu_active_total",
]);

export const JOB_MONITOR_GPU_NO_DATA_ROW = {
  gpu_count_total: null,
  gpu_active_total: null,
  gpu_active_percentage: null,
  gpuLoadingState: "no_data",
};

export function patchJobMonitorGpuRowByUsername(prev, rawUsername, patch) {
  if (!rawUsername) {
    return prev.map((r) =>
      r.username === "" ? { ...r, ...patch } : r,
    );
  }
  return prev.map((r) =>
    (r.username || "") !== rawUsername ? r : { ...r, ...patch },
  );
}

export function jobMonitorSortComparable(row, key) {
  if (key === "username") {
    return (row.username || "").toLowerCase();
  }
  if (JOB_MONITOR_GPU_SORT_KEYS.has(key)) {
    if (row.gpuLoadingState === "loading") return Number.NEGATIVE_INFINITY;
    const v = row[key];
    if (v === null || v === undefined || v === "") return Number.NEGATIVE_INFINITY;
    const n = Number(v);
    return Number.isFinite(n) ? n : Number.NEGATIVE_INFINITY;
  }
  return Number(row[key] ?? 0);
}
