import { jobMonitorGpuRetrieve } from "@/api/generated/monitor/monitor";
import type { JobMonitorRow } from "@/types/view-models";

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

type JobMonitorGpuApiResponse = {
  has_data?: unknown;
  gpu_count_total?: unknown;
  gpu_active_total?: unknown;
  gpu_active_percentage?: unknown;
  username?: unknown;
};

type JobMonitorGpuBatchResponse = {
  results?: JobMonitorGpuApiResponse[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function gpuPatchFromResponse(gpuRes: unknown): Record<string, unknown> {
  const gpu = (isRecord(gpuRes) ? gpuRes : {}) as JobMonitorGpuApiResponse;
  if (gpu.has_data === true) {
    return {
      gpu_count_total: gpu.gpu_count_total,
      gpu_active_total: gpu.gpu_active_total,
      gpu_active_percentage: gpu.gpu_active_percentage,
      gpuLoadingState: "loaded",
    };
  }
  return JOB_MONITOR_GPU_NO_DATA_ROW;
}

async function fetchJobMonitorGpuPatchForUsername(
  username: string,
  days: number | undefined,
): Promise<Record<string, unknown>> {
  if (!username) {
    return JOB_MONITOR_GPU_NO_DATA_ROW;
  }
  try {
    const gpuRes = await jobMonitorGpuRetrieve({ username, days });
    return gpuPatchFromResponse(gpuRes);
  } catch {
    return JOB_MONITOR_GPU_NO_DATA_ROW;
  }
}

/** Batch GPU rollup via ``usernames`` query param (one HTTP request). */
export async function fetchJobMonitorGpuPatches(
  rows: Array<{ username?: string }>,
  days: number | undefined,
): Promise<Map<string, Record<string, unknown>>> {
  const usernames = rows.map((row) => String(row.username || "")).filter(Boolean);
  if (usernames.length === 0) {
    return new Map();
  }

  if (usernames.length === 1) {
    const patch = await fetchJobMonitorGpuPatchForUsername(usernames[0], days);
    return new Map([[usernames[0], patch]]);
  }

  try {
    const batchRes = await jobMonitorGpuRetrieve({
      usernames: usernames.join(","),
      days,
    });

    if (
      batchRes &&
      typeof batchRes === "object" &&
      "results" in batchRes &&
      Array.isArray((batchRes as JobMonitorGpuBatchResponse).results)
    ) {
      const pairs = (batchRes as JobMonitorGpuBatchResponse).results!.map((row) => {
        const username = String(row?.username || "");
        return [username, gpuPatchFromResponse(row)] as const;
      });
      return new Map(pairs);
    }

    if (isRecord(batchRes) && "username" in batchRes) {
      const username = String((batchRes as JobMonitorGpuApiResponse).username || "");
      return new Map([[username, gpuPatchFromResponse(batchRes)]]);
    }
  } catch {
    // Fall through to per-user fetches on batch failure.
  }

  const pairs = await Promise.all(
    usernames.map(async (username) => {
      const patch = await fetchJobMonitorGpuPatchForUsername(username, days);
      return [username, patch] as const;
    }),
  );
  return new Map(pairs);
}

export function mergeJobMonitorGpuPatches(
  prev: JobMonitorRow[],
  patches: Map<string, Record<string, unknown>>,
): JobMonitorRow[] {
  if (patches.size === 0) return prev;
  return prev.map((row) => {
    const username = String(row.username ?? "");
    const patch = patches.get(username);
    if (!patch) return row;
    return { ...row, ...patch };
  });
}

export function patchJobMonitorGpuRowByUsername(
  prev: JobMonitorRow[],
  rawUsername: string,
  patch: Record<string, unknown>,
): JobMonitorRow[] {
  if (!rawUsername) {
    return prev.map((r) =>
      r.username === "" ? { ...r, ...patch } : r,
    );
  }
  return prev.map((r) =>
    (r.username || "") !== rawUsername ? r : { ...r, ...patch },
  );
}

export function jobMonitorSortComparable(row: JobMonitorRow, key: string): string | number {
  if (key === "username") {
    return String(row.username || "").toLowerCase();
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
