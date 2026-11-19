import { describe, expect, it } from "vitest";
import {
  getJobMetricShortLabel,
  getJobWattHoursResourcesTitle,
  getJobWattHoursShortLabel,
  jobHasGpuForWattHoursLabel,
  JOB_METRIC_SHORT_LABELS,
} from "./jobMetricDisplayLabels";

describe("getJobMetricShortLabel", () => {
  it("returns a known abbreviated label for catalog keys", () => {
    expect(getJobMetricShortLabel("avg_freq")).toBe("Average effective CPU frequency");
    expect(JOB_METRIC_SHORT_LABELS.avg_freq).toBe("Average effective CPU frequency");
  });

  it("uses CPU not Grace for ARM INT op rate labels", () => {
    expect(getJobMetricShortLabel("avg_arm_int8_ops")).toBe("Average CPU INT8 operation rate");
    expect(getJobMetricShortLabel("avg_arm_int16_ops")).toBe("Average CPU INT16 operation rate");
  });

  it("labels CPU FP instruction-rate metrics by precision width", () => {
    expect(getJobMetricShortLabel("avg_fp64_instr")).toBe("Average CPU FP64 instruction rate");
    expect(getJobMetricShortLabel("avg_fp32_instr")).toBe("Average CPU FP32 instruction rate");
    expect(getJobMetricShortLabel("avg_fp16_instr")).toBe("Average CPU FP16 instruction rate");
  });

  it("labels the GPU pipe and PVC compute engine metrics", () => {
    expect(getJobMetricShortLabel("avg_integer_active")).toBe(
      "Average GPU integer pipeline activity",
    );
    expect(getJobMetricShortLabel("avg_tensor_dmma_active")).toBe(
      "Average GPU tensor DMMA (FP64) activity",
    );
    expect(getJobMetricShortLabel("avg_tensor_hmma_active")).toBe(
      "Average GPU tensor HMMA (FP16/BF16/TF32/FP8) activity",
    );
    expect(getJobMetricShortLabel("avg_tensor_dfma_active")).toBe(
      "FP64 FMA pipe (same as FP64 on Hopper/Blackwell)",
    );
    expect(getJobMetricShortLabel("avg_compute_engine_util")).toBe(
      "Average Intel PVC compute engine busy",
    );
  });

  it("labels vector share metrics with percent wording", () => {
    expect(getJobMetricShortLabel("vecpercent_64b")).toMatch(/%/);
    expect(getJobMetricShortLabel("vecpercent_32b")).toMatch(/%/);
  });

  it("labels job CPU+GPU watt-hours by default map entry", () => {
    expect(getJobMetricShortLabel("job_cpu_gpu_watt_hours")).toBe("CPU+GPU watt-hours for job");
  });

  it("falls back to the raw metric key when unknown", () => {
    expect(getJobMetricShortLabel("future_metric_key")).toBe("future_metric_key");
  });

  it("returns empty string for null or non-string", () => {
    expect(getJobMetricShortLabel(null)).toBe("");
    expect(getJobMetricShortLabel(undefined)).toBe("");
    expect(getJobMetricShortLabel(1)).toBe("");
  });
});

describe("job watt-hours GPU-aware labels", () => {
  it("detects positive gpu_count", () => {
    expect(jobHasGpuForWattHoursLabel(4)).toBe(true);
    expect(jobHasGpuForWattHoursLabel("2")).toBe(true);
    expect(jobHasGpuForWattHoursLabel(0)).toBe(false);
    expect(jobHasGpuForWattHoursLabel(null)).toBe(false);
    expect(jobHasGpuForWattHoursLabel("")).toBe(false);
  });

  it("omits +GPU from short label and Resources title when no GPU", () => {
    expect(getJobWattHoursShortLabel(false)).toBe("CPU watt-hours for job");
    expect(getJobWattHoursResourcesTitle(false)).toBe("CPU Watt Hours for Job");
  });

  it("keeps +GPU wording when GPUs are present", () => {
    expect(getJobWattHoursShortLabel(true)).toBe("CPU+GPU watt-hours for job");
    expect(getJobWattHoursResourcesTitle(true)).toBe("CPU+GPU Watt Hours for Job");
  });
});
