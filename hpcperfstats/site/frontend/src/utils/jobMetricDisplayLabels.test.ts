import { describe, expect, it } from "vitest";
import { getJobMetricShortLabel, JOB_METRIC_SHORT_LABELS } from "./jobMetricDisplayLabels";

describe("getJobMetricShortLabel", () => {
  it("returns a known abbreviated label for catalog keys", () => {
    expect(getJobMetricShortLabel("avg_freq")).toBe("Average effective CPU frequency");
    expect(JOB_METRIC_SHORT_LABELS.avg_freq).toBe("Average effective CPU frequency");
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
