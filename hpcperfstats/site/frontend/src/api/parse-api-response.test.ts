import { describe, expect, it, vi } from "vitest";
import { parseApiResponse } from "@/api/parse-api-response";
import { resolveResponseSchema } from "@/api/response-schema-registry";
import { homeRetrieveResponse } from "@/api/generated-zod/home/home";
import { jobsRetrieveResponse } from "@/api/generated-zod/jobs/jobs";
import * as isDevEnvironmentModule from "@/utils/is-dev-environment";

const validHomePayload = {
  machine_name: "test-cluster",
  year_list: [2024, 2023],
  date_list: [
    ["2024-01", [["2024-01-15", "15"], ["2024-01-16", "16"]]],
    ["2023-12", [["2023-12-31", "31"]]],
  ],
  metrics: [
    { type: "cpu", metric: "avg_cpuusage", units: "%" },
    { type: "gpu", metric: "utilization", units: "%" },
  ],
  queues: ["normal", "debug"],
  states: ["COMPLETED", "RUNNING"],
};

describe("parse-api-response", () => {
  it("resolves admin monitor schema without ReferenceError", () => {
    expect(resolveResponseSchema("GET", "/api/admin_monitor/")).not.toBeNull();
    const wire = {
      host_stats: [{ host: "n001.cluster.example", last_time: "2024-01-01T00:00:00+00:00" }],
    };
    const parsed = parseApiResponse("GET", "/api/admin_monitor/", wire);
    expect(parsed).toEqual(wire);
  });

  it("accepts job monitor wire envelope", () => {
    const wire = {
      window_days: 30,
      start_time: "2024-01-01T00:00:00+00:00",
      end_time: "2024-02-01T00:00:00+00:00",
      results: [
        {
          username: "alice",
          total_jobs: 10,
          failed_jobs: 1,
          failed_rate: 10,
          timedout_jobs: 0,
          timedout_rate: 0,
        },
      ],
    };
    expect(parseApiResponse("GET", "/api/job_monitor/", wire)).toEqual(wire);
  });

  it("accepts job detail proc_list as string array", () => {
    const wire = {
      job_data: { jid: "123" },
      proc_list: ["python", "mpirun"],
      derived_data_status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/", wire)).toEqual(wire);
  });

  it("accepts type detail artifact wire keys", () => {
    const wire = {
      type_name: "cpu",
      jobid: "123",
      tplot_item: { type: "plot" },
      stats_data: [],
      schema: [],
      status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/cpu/", wire)).toEqual(wire);
  });

  it("accepts job plots legacy m/r/gr plot keys", () => {
    const wire = {
      mplot_item: { type: "plot" },
      mplot_unavailable_reason: null,
      rplot_item: null,
      rplot_unavailable_reason: "no data",
      grplot_item: null,
      grplot_unavailable_reason: null,
      status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/plots/", wire)).toEqual(wire);
  });

  it("accepts job list histogram metric envelope", () => {
    const wire = {
      group: "metric",
      metric: "runtime",
      nj: 5,
      title: "Runtime",
      plot_item_thumb: { type: "plot" },
      plot_item_full: { type: "plot" },
      plot_unavailable_reason: null,
    };
    expect(parseApiResponse("GET", "/api/jobs/histograms/", wire)).toEqual(wire);
  });

  it("validates pub cluster dashboard bundle", () => {
    expect(() =>
      parseApiResponse("GET", "/api/pub/cluster-dashboard/", {
        machine_name: 123,
      }),
    ).toThrow("API response validation failed: GET /api/pub/cluster-dashboard/");
  });

  it("passes through unmapped success payloads", () => {
    const payload = { custom: true };
    expect(parseApiResponse("GET", "/api/unknown/", payload)).toBe(payload);
  });

  it("throws validation error without ReferenceError when dev env is unavailable", () => {
    vi.spyOn(isDevEnvironmentModule, "isDevEnvironment").mockReturnValue(false);
    expect(() =>
      parseApiResponse("GET", "/api/pub/cluster-dashboard/", {
        machine_name: 123,
      }),
    ).toThrow("API response validation failed: GET /api/pub/cluster-dashboard/");
  });

  it("accepts realistic home options payload", () => {
    expect(homeRetrieveResponse.safeParse(validHomePayload).success).toBe(true);
    const parsed = parseApiResponse("GET", "/api/home/", validHomePayload);
    expect(parsed).toEqual(validHomePayload);
  });

  it("rejects legacy home metrics missing type with route in error message", () => {
    const legacyPayload = {
      ...validHomePayload,
      metrics: [{ metric: "runtime", units: "hours" }],
    };
    expect(homeRetrieveResponse.safeParse(legacyPayload).success).toBe(false);
    expect(() => parseApiResponse("GET", "/api/home/", legacyPayload)).toThrow(
      "API response validation failed: GET /api/home/",
    );
  });

  it("accepts job list datetimes without timezone suffix (DRF default)", () => {
    const payload = {
      nj: 1,
      job_list: [
        {
          jid: "j1",
          submit_time: "2024-01-01T00:00:00",
          start_time: "2024-01-01T00:00:00",
          end_time: "2024-01-01T01:00:00",
          runtime: 3600,
          host_list: ["n001.cluster.example"],
          performance: {
            label: "Summary available",
            tone: "success",
            aria_label: "Performance: Summary available",
            sort_rank: 0,
          },
        },
      ],
      filter_summary: ["Queue: normal"],
      aggregates: { total_node_hours: 64 },
      pagination: { page: 1, num_pages: 1 },
    };
    expect(jobsRetrieveResponse.safeParse(payload).success).toBe(true);
    const parsed = parseApiResponse<typeof payload>("GET", "/api/jobs/", payload);
    expect(parsed.nj).toBe(1);
    expect(parsed.job_list?.[0]?.host_list).toEqual(["n001.cluster.example"]);
    expect(parsed.filter_summary).toEqual(["Queue: normal"]);
  });
});
