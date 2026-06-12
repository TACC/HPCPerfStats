import { describe, expect, it } from "vitest";
import { API_PATHS, HISTOGRAM_EMBED_VERSION } from "./api-paths";

describe("API_PATHS", () => {
  it("uses leading-slash API routes under the machine site", () => {
    for (const path of Object.values(API_PATHS)) {
      expect(path.startsWith("/")).toBe(true);
      expect(path.endsWith("/")).toBe(true);
    }
  });

  it("exposes stable endpoints used by the SPA", () => {
    expect(API_PATHS.session).toBe("/session/");
    expect(API_PATHS.home).toBe("/home/");
    expect(API_PATHS.jobs).toBe("/jobs/");
    expect(API_PATHS.jobsHistograms).toBe("/jobs/histograms/");
    expect(API_PATHS.hostPlot).toBe("/host_plot/");
    expect(API_PATHS.jobMonitorGpu).toBe("/job_monitor/gpu/");
  });
});

describe("HISTOGRAM_EMBED_VERSION", () => {
  it("is a non-empty version string for histogram cache busting", () => {
    expect(HISTOGRAM_EMBED_VERSION).toMatch(/^\d+$/);
  });
});
