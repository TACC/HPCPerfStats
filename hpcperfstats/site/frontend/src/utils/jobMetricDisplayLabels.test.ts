import { describe, expect, it } from "vitest";
import { getJobMetricShortLabel, JOB_METRIC_SHORT_LABELS } from "./jobMetricDisplayLabels";

describe("getJobMetricShortLabel", () => {
  it("returns a known abbreviated label for catalog keys", () => {
    expect(getJobMetricShortLabel("avg_freq")).toBe("Average effective CPU frequency");
    expect(JOB_METRIC_SHORT_LABELS.avg_freq).toBe("Average effective CPU frequency");
  });

  it("uses CPU not Grace for ARM INT op rate labels", () => {
    expect(getJobMetricShortLabel("avg_arm_int8_ops")).toBe("Average CPU INT8 operation rate");
    expect(getJobMetricShortLabel("avg_arm_int16_ops")).toBe("Average CPU INT16 operation rate");
  });

  it("labels vector share metrics with percent wording", () => {
    expect(getJobMetricShortLabel("vecpercent_64b")).toMatch(/%/);
    expect(getJobMetricShortLabel("vecpercent_32b")).toMatch(/%/);
  });

  it("labels job CPU+GPU watt-hours", () => {
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
