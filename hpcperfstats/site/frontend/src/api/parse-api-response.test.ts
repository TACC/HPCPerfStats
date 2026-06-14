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
    const parsed = parseApiResponse("GET", "/api/admin_monitor/", {
      section: "hosts",
      data: { host_stats: [] },
    });
    expect(parsed).toEqual({ section: "hosts", data: { host_stats: [] } });
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
          performance: {
            label: "Summary available",
            tone: "success",
            sort_rank: 0,
          },
        },
      ],
      filter_summary: { nj: 1, filter_text: "Jobs" },
      aggregates: { total_node_hours: 64 },
      pagination: { page: 1, num_pages: 1 },
    };
    expect(jobsRetrieveResponse.safeParse(payload).success).toBe(true);
    const parsed = parseApiResponse<typeof payload>("GET", "/api/jobs/", payload);
    expect(parsed.nj).toBe(1);
    expect(parsed.job_list?.[0]?.jid).toBe("j1");
    expect(parsed.job_list?.[0]?.end_time).toBe("2024-01-01T01:00:00");
  });
});
