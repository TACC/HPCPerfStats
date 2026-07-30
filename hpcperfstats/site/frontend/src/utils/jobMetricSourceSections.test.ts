import { describe, expect, it } from "vitest";
import {
  groupJobMetricsBySourceSection,
  jobMetricSourceSectionId,
  JOB_METRIC_SOURCE_SECTION_LABELS,
} from "./jobMetricSourceSections";

describe("jobMetricSourceSectionId", () => {
  it("maps host_mem and host_numa to CPU", () => {
    expect(jobMetricSourceSectionId({ type: "host_mem", metric: "mem_hwm" })).toBe("cpu");
    expect(
      jobMetricSourceSectionId({ type: "host_numa", metric: "max_numa_remote_rate" }),
    ).toBe("cpu");
  });

  it("maps Intel IMC and AMD DF family types to CPU via prefix", () => {
    expect(
      jobMetricSourceSectionId({ type: "intel_x86_uncore_imc_spr", metric: "avg_mbw" }),
    ).toBe("cpu");
    expect(
      jobMetricSourceSectionId({ type: "amd_x86_uncore_df_genoa", metric: "avg_mbw" }),
    ).toBe("cpu");
    expect(jobMetricSourceSectionId({ type: "imc", metric: "dram_bw_node_imbalance" })).toBe(
      "cpu",
    );
  });

  it("maps nvidia_gpu, amd_gpu, intel_gpu, and gpu to GPU", () => {
    expect(jobMetricSourceSectionId({ type: "nvidia_gpu", metric: "avg_tensor_active" })).toBe(
      "gpu",
    );
    expect(jobMetricSourceSectionId({ type: "amd_gpu", metric: "avg_gpuutil" })).toBe("gpu");
    expect(jobMetricSourceSectionId({ type: "intel_gpu", metric: "detail_gpu_count" })).toBe(
      "gpu",
    );
    expect(jobMetricSourceSectionId({ type: "gpu", metric: "detail_gpu_util_mean" })).toBe(
      "gpu",
    );
  });

  it("maps filesystem and network types", () => {
    expect(jobMetricSourceSectionId({ type: "lustre_llite", metric: "avg_sharedfs_bw" })).toBe(
      "filesystem",
    );
    expect(jobMetricSourceSectionId({ type: "host_block", metric: "avg_blockbw" })).toBe(
      "filesystem",
    );
    expect(jobMetricSourceSectionId({ type: "host_ib", metric: "avg_ibbw" })).toBe("network");
    expect(jobMetricSourceSectionId({ type: "lnet", metric: "max_lnetbw" })).toBe("network");
  });

  it("maps job power to Misc and unknown types to Misc", () => {
    expect(jobMetricSourceSectionId({ type: "job", metric: "job_cpu_gpu_watt_hours" })).toBe(
      "misc",
    );
    expect(jobMetricSourceSectionId({ type: "future_widget", metric: "avg_widget" })).toBe(
      "misc",
    );
  });

  it("falls back from metric name when type is missing", () => {
    expect(jobMetricSourceSectionId({ type: null, metric: "detail_gpu_count" })).toBe("gpu");
    expect(jobMetricSourceSectionId({ type: "", metric: "detail_fsio_llite_read_mb" })).toBe(
      "filesystem",
    );
    expect(
      jobMetricSourceSectionId({ type: undefined, metric: "avg_vector_width_combined" }),
    ).toBe("cpu");
  });
});

describe("groupJobMetricsBySourceSection", () => {
  it("orders CPU → GPU → File System → Network → Misc and always includes Network", () => {
    const sections = groupJobMetricsBySourceSection([
      { type: "job", metric: "job_cpu_gpu_watt_hours", value: 1 },
      { type: "nvidia_gpu", metric: "avg_tensor_active", value: 10 },
      { type: "host_cpu", metric: "avg_cpuusage", value: 32 },
      { type: "lustre_llite", metric: "avg_sharedfs_bw", value: 100 },
      { type: "host_ib", metric: "avg_ibbw", value: 200 },
    ]);

    expect(sections.map((s) => s.id)).toEqual([
      "cpu",
      "gpu",
      "filesystem",
      "network",
      "misc",
    ]);
    expect(sections.map((s) => s.label)).toEqual([
      JOB_METRIC_SOURCE_SECTION_LABELS.cpu,
      JOB_METRIC_SOURCE_SECTION_LABELS.gpu,
      JOB_METRIC_SOURCE_SECTION_LABELS.filesystem,
      JOB_METRIC_SOURCE_SECTION_LABELS.network,
      JOB_METRIC_SOURCE_SECTION_LABELS.misc,
    ]);
    expect(sections.find((s) => s.id === "cpu")?.rows.map((r) => r.metric)).toEqual([
      "avg_cpuusage",
    ]);
  });

  it("always emits Network even when empty; omits empty GPU/FS/Misc", () => {
    const sections = groupJobMetricsBySourceSection([
      { type: "pmc", metric: "avg_freq", value: 2.5 },
      { type: "host_mem", metric: "mem_hwm", value: 64 },
    ]);

    expect(sections.map((s) => s.id)).toEqual(["cpu", "network"]);
    expect(sections.find((s) => s.id === "network")?.rows).toEqual([]);
    expect(sections.find((s) => s.id === "cpu")?.rows.map((r) => r.metric)).toEqual([
      "avg_freq",
      "mem_hwm",
    ]);
  });

  it("preserves within-section input order", () => {
    const sections = groupJobMetricsBySourceSection([
      { type: "pmc", metric: "avg_freq", value: 1 },
      { type: "pmc", metric: "avg_flops64b", value: 2 },
      { type: "gpu", metric: "detail_gpu_count", value: 4 },
      { type: "nvidia_gpu", metric: "avg_tensor_active", value: 5 },
    ]);
    expect(sections.find((s) => s.id === "cpu")?.rows.map((r) => r.metric)).toEqual([
      "avg_freq",
      "avg_flops64b",
    ]);
    expect(sections.find((s) => s.id === "gpu")?.rows.map((r) => r.metric)).toEqual([
      "detail_gpu_count",
      "avg_tensor_active",
    ]);
  });
});
