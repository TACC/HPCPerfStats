import { describe, expect, it } from "vitest";
import {
  getDescriptionForVariable,
  normalizeVariableKey,
  VARIABLE_METADATA,
} from "./variableMetadata";

describe("normalizeVariableKey", () => {
  it("strips units suffix in brackets", () => {
    expect(normalizeVariableKey("avg_cpuusage [#cores]")).toBe("avg_cpuusage");
  });

  it("returns bare metric names unchanged", () => {
    expect(normalizeVariableKey("mem_hwm")).toBe("mem_hwm");
  });
});

describe("getDescriptionForVariable", () => {
  it("returns a description for a documented metric", () => {
    expect(getDescriptionForVariable("avg_cpuusage")).toBeTruthy();
    expect(getDescriptionForVariable("mem_hwm")).toBeTruthy();
    expect(getDescriptionForVariable("avg_fabric_mb_per_gflops")).toMatch(
      /fabric bandwidth/i,
    );
    expect(getDescriptionForVariable("flops_node_imbalance")).toMatch(/FLOP/i);
    expect(getDescriptionForVariable("avg_tensor_active")).toMatch(/tensor/i);
    expect(getDescriptionForVariable("dram_bw_node_imbalance")).toMatch(/DRAM/i);
    expect(getDescriptionForVariable("max_node_power_est_w")).toMatch(/node power/i);
    expect(getDescriptionForVariable("avg_node_power_est_w")).toMatch(/Mean estimated on-node power/i);
  });

  it("documents shared filesystem metrics with the job detail section", () => {
    expect(getDescriptionForVariable("avg_sharedfs_bw")).toMatch(
      /Shared File System section/i,
    );
    expect(getDescriptionForVariable("avg_sharedfs_iops")).toMatch(
      /Shared File System section/i,
    );
  });

  it("documents metrics_distinct_time_count for staff Sample Count help", () => {
    expect(getDescriptionForVariable("metrics_distinct_time_count")).toMatch(/distinct sample timestamps/i);
  });

  it("returns a description for code-derived definitions", () => {
    expect(getDescriptionForVariable("utilization")).toMatch(/GPU utilization/i);
    expect(getDescriptionForVariable("read_bytes")).toMatch(/Bytes read/i);
  });

  it("falls back for unknown variables", () => {
    expect(getDescriptionForVariable("unknown_metric_xyz")).toMatch(/Telemetry variable/i);
  });

  it("returns null for metrics with no doc text (sf evictrate)", () => {
    expect(VARIABLE_METADATA.avg_sf_evictrate).toBeUndefined();
    expect(getDescriptionForVariable("avg_sf_evictrate")).toMatch(/Telemetry variable/i);
  });
});
