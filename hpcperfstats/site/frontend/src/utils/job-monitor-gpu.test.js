import { describe, expect, it } from "vitest";
import {
  JOB_MONITOR_GPU_NO_DATA_ROW,
  jobMonitorSortComparable,
  patchJobMonitorGpuRowByUsername,
} from "./job-monitor-gpu";

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
