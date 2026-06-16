/**
 * One-off audit: compare realistic Django wire payloads vs Orval Zod schemas
 * registered in response-schema-registry.ts. Run: npx tsx scripts/audit-wire-drift.mts
 */
import {
  adminMonitorRetrieveResponse,
  cacheInvalidatePageCreateResponse,
  sacctIngestCreateResponse,
} from "../src/api/generated-zod/admin/admin";
import { homeRetrieveResponse } from "../src/api/generated-zod/home/home";
import {
  jobsHistogramsBatchRetrieveResponse,
  jobsHistogramsRetrieveResponse,
  jobsFilterOptionsRetrieveResponse,
  jobsPlotsRetrieveResponse,
  jobsRetrieve2Response,
  jobsRetrieve3Response,
  jobsRetrieveResponse,
} from "../src/api/generated-zod/jobs/jobs";
import { hostPlotRetrieveResponse } from "../src/api/generated-zod/hosts/hosts";
import {
  jobMonitorGpuRetrieveResponse,
  jobMonitorRetrieveResponse,
} from "../src/api/generated-zod/monitor/monitor";
import { pubClusterDashboardRetrieveResponse } from "../src/api/generated-zod/public/public";
import {
  sessionDropStaffCreateResponse,
  sessionRetrieveResponse,
  userApiKeyRetrieveResponse,
  userApiKeyRotateCreateResponse,
} from "../src/api/generated-zod/session/session";
import type { z } from "zod";

type Case = {
  route: string;
  schema: z.ZodTypeAny;
  wire: unknown;
};

const cases: Case[] = [
  {
    route: "GET /api/session/",
    schema: sessionRetrieveResponse,
    wire: {
      logged_in: true,
      username: "alice",
      is_staff: false,
      machine_name: ".cluster.example",
    },
  },
  {
    route: "POST /api/session/drop-staff/",
    schema: sessionDropStaffCreateResponse,
    wire: { ok: true, message: "Staff removed", is_staff: false },
  },
  {
    route: "GET /api/user-api-key/",
    schema: userApiKeyRetrieveResponse,
    wire: { username: "alice", raw_key: null, key_prefix: "abc123" },
  },
  {
    route: "POST /api/user-api-key/rotate/",
    schema: userApiKeyRotateCreateResponse,
    wire: { username: "alice", raw_key: "secret", key_prefix: "xyz" },
  },
  {
    route: "POST /api/cache/invalidate-page/",
    schema: cacheInvalidatePageCreateResponse,
    wire: {
      ok: true,
      page_path: "/machine/jobs/",
      deleted_keys: 3,
      scanned_keys: 10,
    },
  },
  {
    route: "POST /api/sacct/ingest/",
    schema: sacctIngestCreateResponse,
    wire: { ok: true, message: "Ingested 5 jobs" },
  },
  {
    route: "GET /api/home/",
    schema: homeRetrieveResponse,
    wire: {
      machine_name: "test",
      year_list: [2024],
      date_list: [["2024-01", [["2024-01-15", "15"]]]],
      metrics: [{ type: "cpu", metric: "runtime", units: "s" }],
      queues: ["normal"],
      states: ["COMPLETED"],
    },
  },
  {
    route: "GET /api/jobs/",
    schema: jobsRetrieveResponse,
    wire: {
      nj: 1,
      job_list: [
        {
          jid: "1",
          account: null,
          queue: null,
          state: null,
          QOS: null,
          jobname: null,
          sample_count: null,
          host_list: ["n001"],
          performance: { label: "OK", tone: "success", aria_label: "OK", sort_rank: 0 },
        },
      ],
      filter_summary: ["User: alice"],
      filter_options: {
        usernames: ["alice"],
        accounts: ["proj"],
        queues: ["normal"],
        states: ["COMPLETED"],
        performance_statuses: [{ sort_rank: 0, label: "Summary available" }],
        truncated: {
          usernames: false,
          accounts: false,
          queues: false,
          states: false,
        },
      },
    },
  },
  {
    route: "GET /api/jobs/filter_options/",
    schema: jobsFilterOptionsRetrieveResponse,
    wire: {
      filter_options: {
        usernames: ["alice"],
        accounts: ["proj"],
        queues: ["normal"],
        states: ["COMPLETED"],
        performance_statuses: [{ sort_rank: 0, label: "Summary available" }],
        truncated: {
          usernames: false,
          accounts: false,
          queues: false,
          states: false,
        },
      },
    },
  },
  {
    route: "GET /api/jobs/histograms/",
    schema: jobsHistogramsRetrieveResponse,
    wire: {
      group: "metric",
      metric: "runtime",
      nj: 10,
      histogram_nj: 10,
      histogram_sampled: false,
      title: "Runtime",
      plot_item_thumb: { type: "plot" },
      plot_item_full: { type: "plot" },
      plot_unavailable_reason: null,
    },
  },
  {
    route: "GET /api/jobs/histograms/batch/",
    schema: jobsHistogramsBatchRetrieveResponse,
    wire: {
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
          title: "Number of jobs by cpu hours",
          plot_item_thumb: { type: "plot" },
          plot_item_full: { type: "plot" },
          plot_unavailable_reason: null,
        },
      ],
    },
  },
  {
    route: "GET /api/host_plot/",
    schema: hostPlotRetrieveResponse,
    wire: {
      host: "n001.cluster.example",
      plot_item: { type: "plot" },
      plot_unavailable_reason: null,
      end_time__gte: "2024-01-01T00:00:00+00:00",
      end_time__lte: "2024-01-02T00:00:00+00:00",
    },
  },
  {
    route: "GET /api/admin_monitor/",
    schema: adminMonitorRetrieveResponse,
    wire: { host_stats: [{ host: "n001", last_seen: "2024-01-01T00:00:00+00:00" }] },
  },
  {
    route: "GET /api/job_monitor/",
    schema: jobMonitorRetrieveResponse,
    wire: {
      window_days: 30,
      start_time: "2024-01-01T00:00:00+00:00",
      end_time: "2024-02-01T00:00:00+00:00",
      results: [
        {
          username: "alice",
          total_jobs: 10,
          failed_jobs: 1,
          failed_rate: 10.0,
          timedout_jobs: 0,
          timedout_rate: 0.0,
        },
      ],
    },
  },
  {
    route: "GET /api/job_monitor/gpu/",
    schema: jobMonitorGpuRetrieveResponse,
    wire: {
      username: "alice",
      gpu_count_total: 4,
      gpu_active_total: 2,
      gpu_active_percentage: 50.0,
      has_data: true,
    },
  },
  {
    route: "GET /api/jobs/{id}/",
    schema: jobsRetrieve2Response,
    wire: {
      job_data: { jid: "123", host_list: ["n001"] },
      host_list: ["n001"],
      proc_list: ["python", "mpirun"],
      gpu_active: 1,
      gpu_utilization_max: 95.0,
      gpu_utilization_mean: 80.0,
      gpu_count: 4,
      metrics_list: [
        {
          type: "cpu",
          metric: "avg_cpuusage",
          units: "#cores",
          value: 2.25,
          no_data_reason: null,
        },
      ],
      multiprecision_cpu_unavailable_reason: null,
      multiprecision_gpu_unavailable_reason: null,
      derived_data_status: "ready",
      staff_metrics_distinct_time_count: null,
    },
  },
  {
    route: "GET /api/jobs/{id}/plots/",
    schema: jobsPlotsRetrieveResponse,
    wire: {
      mplot_item: { type: "plot" },
      mplot_unavailable_reason: null,
      rplot_item: {},
      rplot_unavailable_reason: null,
      grplot_item: {},
      grplot_unavailable_reason: null,
      status: "ready",
    },
  },
  {
    route: "GET /api/jobs/{jid}/{type}/",
    schema: jobsRetrieve3Response,
    wire: {
      type_name: "cpu",
      jobid: "123",
      tplot_item: { type: "plot" },
      stats_data: [],
      schema: [],
      status: "ready",
    },
  },
  {
    route: "GET /api/pub/cluster-dashboard/",
    schema: pubClusterDashboardRetrieveResponse,
    wire: {
      status: "ready",
      machine_name: "test",
      expansion_factors: { cpu: 1.2 },
      monthly_metrics: [{ title: "Jan", bokeh_histogram_json_item: {} }],
    },
  },
];

function summarize(data: unknown): string {
  if (data === null || data === undefined) return String(data);
  if (typeof data !== "object") return JSON.stringify(data);
  const keys = Object.keys(data as object);
  if (keys.length === 0) return "(empty object — all wire fields stripped)";
  if (keys.length <= 6) return `{ ${keys.join(", ")} }`;
  return `{ ${keys.slice(0, 6).join(", ")}, … +${keys.length - 6} }`;
}

console.log("OpenAPI wire drift audit (Zod safeParse vs realistic Django payloads)\n");
console.log("| Route | Zod | Parsed output | Wire keys lost |");
console.log("|-------|-----|---------------|----------------|");

let fail = 0;
let silent = 0;
let ok = 0;

for (const { route, schema, wire } of cases) {
  const wireKeys =
    wire && typeof wire === "object" ? Object.keys(wire as object) : [];
  const result = schema.safeParse(wire);
  if (!result.success) {
    fail += 1;
    const issues = result.error.issues.map((i) => i.path.join(".") || i.message).slice(0, 3);
    console.log(`| ${route} | **FAIL** | — | ${issues.join("; ")} |`);
    continue;
  }
  const parsedKeys =
    result.data && typeof result.data === "object"
      ? Object.keys(result.data as object)
      : [];
  const lost = wireKeys.filter((k) => !parsedKeys.includes(k));
  if (lost.length > 0) {
    silent += 1;
    console.log(
      `| ${route} | pass (strip) | ${summarize(result.data)} | ${lost.slice(0, 8).join(", ")}${lost.length > 8 ? "…" : ""} |`,
    );
  } else {
    ok += 1;
    console.log(`| ${route} | OK | ${summarize(result.data)} | — |`);
  }
}

console.log(`\nSummary: ${ok} aligned, ${silent} silent strip, ${fail} hard fail (total ${cases.length})`);
