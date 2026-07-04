import { describe, expect, it, vi } from "vitest";
import {
  JOB_MONITOR_GPU_NO_DATA_ROW,
  fetchJobMonitorGpuPatches,
  jobMonitorSortComparable,
  mergeJobMonitorGpuPatches,
  patchJobMonitorGpuRowByUsername,
} from "./job-monitor-gpu";

vi.mock("@/api/generated/monitor/monitor", () => ({
  jobMonitorGpuRetrieve: vi.fn(),
}));

import { orvalOkEnvelope } from "@/api/orval-response";
import { jobMonitorGpuRetrieve } from "@/api/generated/monitor/monitor";

describe("mergeJobMonitorGpuPatches", () => {
  it("applies all username patches in one pass", () => {
    const prev = [
      { username: "alice", gpuLoadingState: "loading" },
      { username: "bob", gpuLoadingState: "loading" },
    ];
    const patches = new Map<string, Record<string, unknown>>([
      ["alice", { gpu_count_total: 2, gpuLoadingState: "loaded" }],
      ["bob", JOB_MONITOR_GPU_NO_DATA_ROW],
    ]);
    const next = mergeJobMonitorGpuPatches(prev, patches);
    expect(next[0].gpu_count_total).toBe(2);
    expect(next[0].gpuLoadingState).toBe("loaded");
    expect(next[1].gpuLoadingState).toBe("no_data");
  });
});

describe("fetchJobMonitorGpuPatches", () => {
  it("uses batch usernames param for multiple rows", async () => {
    vi.mocked(jobMonitorGpuRetrieve).mockResolvedValue(
      orvalOkEnvelope({
      results: [
        { username: "a", has_data: false },
        { username: "b", has_data: false },
      ],
      }),
    );
    const patches = await fetchJobMonitorGpuPatches(
      [{ username: "a" }, { username: "b" }],
      30,
    );
    expect(patches.size).toBe(2);
    expect(jobMonitorGpuRetrieve).toHaveBeenCalledTimes(1);
    expect(jobMonitorGpuRetrieve).toHaveBeenCalledWith({
      usernames: "a,b",
      days: 30,
    });
  });
});

describe("patchJobMonitorGpuRowByUsername", () => {
  it("patches only rows with empty string username when raw username is empty", () => {
    const prev = [
      { username: "", gpuLoadingState: "loading" },
      { username: "alice", gpuLoadingState: "loading" },
    ];
    const next = patchJobMonitorGpuRowByUsername(prev, "", JOB_MONITOR_GPU_NO_DATA_ROW);
    expect(next[0].gpuLoadingState).toBe("no_data");
    expect(next[1].gpuLoadingState).toBe("loading");
  });

  it("patches rows matching non-empty username using normalized match", () => {
    const prev = [
      { username: "bob", gpu_count_total: null },
      { username: "ann", gpu_count_total: null },
    ];
    const next = patchJobMonitorGpuRowByUsername(prev, "bob", {
      gpu_count_total: 4,
      gpuLoadingState: "loaded",
    });
    expect(next[0].gpu_count_total).toBe(4);
    expect(next[0].gpuLoadingState).toBe("loaded");
    expect(next[1].gpu_count_total).toBeNull();
  });
});

describe("jobMonitorSortComparable", () => {
  it("treats loading GPU columns as negative infinity", () => {
    const row = { gpuLoadingState: "loading", gpu_count_total: 99 };
    expect(jobMonitorSortComparable(row, "gpu_count_total")).toBe(
      Number.NEGATIVE_INFINITY,
    );
  });

  it("parses numeric GPU columns when loaded", () => {
    const row = {
      gpuLoadingState: "loaded",
      gpu_active_percentage: "12.5",
    };
    expect(jobMonitorSortComparable(row, "gpu_active_percentage")).toBe(12.5);
  });
});
