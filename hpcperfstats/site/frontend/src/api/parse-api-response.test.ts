import { describe, expect, it, vi } from "vitest";
import { OPENAPI_BOKEH_JSON_ITEM } from "@test/vitest/test-utils/bokeh-fixtures";
import { parseApiResponse } from "@/api/parse-api-response";
import { resolveResponseSchema } from "@/api/response-schema-registry";
import { HomeRetrieveResponse } from "@/api/generated-zod/home/home";
import { JobsRetrieveResponse } from "@/api/generated-zod/jobs/jobs";
import { SessionRetrieveResponse } from "@/api/generated-zod/session/session";
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

  it("accepts job monitor gpu batch results envelope", () => {
    const wire = {
      results: [
        {
          username: "alice",
          gpu_count_total: 4,
          gpu_active_total: 2,
          gpu_active_percentage: 50,
          has_data: true,
        },
      ],
    };
    expect(parseApiResponse("GET", "/api/job_monitor/gpu/", wire)).toEqual(wire);
  });

  it("accepts pub dashboard meta bundle with sections period keys", () => {
    const wire = {
      status: "ready",
      machine_name: "test.cluster.example",
      detail: null,
      retry_hint: null,
      schema_version: 1,
      sections: {
        expansion_factor: {
          monthly_period_keys: ["2024-02", "2024-01"],
          yearly_period_keys: ["2024", "2023"],
        },
      },
    };
    expect(parseApiResponse("GET", "/api/pub/cluster-dashboard/", wire)).toEqual(wire);
  });

  it("accepts pub dashboard lazy period with block", () => {
    const wire = {
      status: "ready",
      machine_name: "test.cluster.example",
      section: "expansion_factor",
      grouping: "monthly",
      period_key: "2024-01",
      block: {
        histogram_bin_edges: [0.0, 1.0, 2.0],
        histogram_counts: [5, 10, 3],
        bokeh_histogram_json_item: OPENAPI_BOKEH_JSON_ITEM,
      },
    };
    expect(parseApiResponse("GET", "/api/pub/cluster-dashboard/", wire)).toEqual(wire);
  });

  it("accepts job detail proc_list as string array", () => {
    const wire = {
      job_data: { jid: "123" },
      proc_list: ["python", "mpirun"],
      derived_data_status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/", wire)).toEqual(wire);
  });

  it("accepts job detail metrics_list with numeric metric value", () => {
    const wire = {
      job_data: { jid: "737412" },
      metrics_list: [
        {
          type: "cpu",
          metric: "avg_cpuusage",
          units: "#cores",
          value: 2.25,
          no_data_reason: null,
        },
      ],
      derived_data_status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/737412/", wire)).toEqual(wire);
  });

  it("accepts job detail metrics_list with null metric value and no_data_reason", () => {
    const wire = {
      job_data: { jid: "123" },
      metrics_list: [
        {
          type: "mem",
          metric: "mem_hwm",
          units: "GiB",
          value: null,
          no_data_reason: "No usable memory telemetry for high-water mark",
        },
      ],
      derived_data_status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/", wire)).toEqual(wire);
  });

  it("accepts job detail with null staff_metrics_distinct_time_count for staff", () => {
    const wire = {
      job_data: { jid: "745354" },
      derived_data_status: "ready",
      staff_metrics_distinct_time_count: null,
    };
    expect(parseApiResponse("GET", "/api/jobs/745354/", wire)).toEqual(wire);
  });

  it("accepts job detail with staff_artifact_contract", () => {
    const wire = {
      job_data: { jid: "745355" },
      derived_data_status: "ready",
      staff_metrics_distinct_time_count: 1250,
      staff_artifact_contract: {
        current_plot: 11,
        current_detail: 8,
        db_plot: [10, 11],
        db_detail: [],
      },
    };
    expect(parseApiResponse("GET", "/api/jobs/745355/", wire)).toEqual(wire);
  });

  it("accepts type detail artifact wire keys", () => {
    const wire = {
      type_name: "cpu",
      jobid: "123",
      tplot_item: OPENAPI_BOKEH_JSON_ITEM,
      stats_data: [],
      schema: [],
      status: "ready",
    };
    expect(parseApiResponse("GET", "/api/jobs/123/cpu/", wire)).toEqual(wire);
  });

  it("accepts job plots legacy m/r/gr plot keys", () => {
    const wire = {
      mplot_item: OPENAPI_BOKEH_JSON_ITEM,
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
      histogram_nj: 5,
      histogram_sampled: false,
      title: "Runtime",
      plot_item_thumb: OPENAPI_BOKEH_JSON_ITEM,
      plot_item_full: OPENAPI_BOKEH_JSON_ITEM,
      plot_unavailable_reason: null,
    };
    expect(parseApiResponse("GET", "/api/jobs/histograms/", wire)).toEqual(wire);
  });

  it("accepts job list histogram batch envelope", () => {
    const wire = {
      nj: 12000,
      histogram_nj: 5000,
      histogram_sampled: true,
      histograms: [
        {
          group: "metric",
          metric: "runtime",
          nj: 12000,
          histogram_nj: 5000,
          histogram_sampled: true,
          title: "Runtime",
          plot_item_thumb: OPENAPI_BOKEH_JSON_ITEM,
          plot_item_full: OPENAPI_BOKEH_JSON_ITEM,
          plot_unavailable_reason: null,
        },
      ],
    };
    expect(parseApiResponse("GET", "/api/jobs/histograms/batch/", wire)).toEqual(wire);
  });

  it("preserves Bokeh root type/attributes through histogram batch parse (no Zod strip)", () => {
    const richThumb = {
      doc: {
        root_ids: ["p1006"],
        roots: [
          {
            id: "p1006",
            type: "object",
            name: "Figure",
            attributes: { title: "Number of jobs by cpu hours" },
          },
        ],
      },
      root_id: "p1006",
    };
    const wire = {
      nj: 3,
      histogram_nj: 3,
      histogram_sampled: false,
      histograms: [
        {
          group: "metric",
          metric: "runtime",
          title: "Number of jobs by cpu hours",
          plot_item_thumb: richThumb,
          plot_item_full: richThumb,
          plot_unavailable_reason: null,
        },
      ],
    };
    const parsed = parseApiResponse("GET", "/api/jobs/histograms/batch/", wire) as typeof wire;
    const thumb = parsed.histograms[0].plot_item_thumb as typeof richThumb;
    expect(thumb.doc.roots[0].type).toBe("object");
    expect(thumb.doc.roots[0].attributes).toEqual({
      title: "Number of jobs by cpu hours",
    });
    expect(thumb.root_id).toBe("p1006");
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
    expect(HomeRetrieveResponse.safeParse(validHomePayload).success).toBe(true);
    const parsed = parseApiResponse("GET", "/api/home/", validHomePayload);
    expect(parsed).toEqual(validHomePayload);
  });

  it("accepts session payload without machine_name", () => {
    const payload = {
      logged_in: true,
      username: "alice",
      is_staff: false,
    };
    expect(SessionRetrieveResponse.safeParse(payload).success).toBe(true);
    const parsed = parseApiResponse("GET", "/api/session/", payload);
    expect(parsed).toEqual(payload);
  });

  it("accepts session payload with machine_name", () => {
    const payload = {
      logged_in: true,
      username: "alice",
      is_staff: true,
      machine_name: "cluster.test",
    };
    expect(SessionRetrieveResponse.safeParse(payload).success).toBe(true);
    expect(parseApiResponse("GET", "/api/session/", payload)).toEqual(payload);
  });

  it("rejects legacy home metrics missing type with route in error message", () => {
    const legacyPayload = {
      ...validHomePayload,
      metrics: [{ metric: "runtime", units: "hours" }],
    };
    expect(HomeRetrieveResponse.safeParse(legacyPayload).success).toBe(false);
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
    expect(JobsRetrieveResponse.safeParse(payload).success).toBe(true);
    const parsed = parseApiResponse<typeof payload>("GET", "/api/jobs/", payload);
    expect(parsed.nj).toBe(1);
    expect(parsed.job_list?.[0]?.host_list).toEqual(["n001.cluster.example"]);
    expect(parsed.filter_summary).toEqual(["Queue: normal"]);
  });

  it("accepts nullable account, queue, state, QOS, and jobname on job list entries", () => {
    const payload = {
      nj: 1,
      job_list: [
        {
          jid: "99999",
          submit_time: "2024-06-01T12:00:00",
          start_time: "2024-06-01T12:05:00",
          end_time: "2024-06-01T14:00:00",
          runtime: 6900,
          username: "bob",
          account: null,
          queue: null,
          state: null,
          QOS: null,
          jobname: null,
          sample_count: null,
          host_list: ["n003.cluster.example"],
          performance: {
            label: "Summary available",
            tone: "success",
            aria_label: "Performance: Summary available",
            sort_rank: 0,
          },
        },
      ],
      filter_summary: ["User: bob"],
      aggregates: { total_node_hours: 12 },
      pagination: { page: 1, num_pages: 1 },
    };
    expect(JobsRetrieveResponse.safeParse(payload).success).toBe(true);
    const parsed = parseApiResponse<typeof payload>("GET", "/api/jobs/", payload);
    expect(parsed.job_list?.[0]?.account).toBeNull();
    expect(parsed.job_list?.[0]?.queue).toBeNull();
    expect(parsed.job_list?.[0]?.sample_count).toBeNull();
  });
});
