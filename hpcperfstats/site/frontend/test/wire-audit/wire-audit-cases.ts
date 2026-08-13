/**
 * Realistic Django wire payloads shared by audit-wire-drift.mts and parse-api-response tests.
 * Keep in sync with hpcperfstats/site/lib/machine/tests/test_openapi_wire_contracts.py fixtures.
 */
import { OPENAPI_BOKEH_JSON_ITEM } from "./bokeh-wire-fixtures";

export type WireAuditCase = {
  method: "GET" | "POST";
  path: string;
  wire: unknown;
  /** Human label for audit output (optional). */
  label?: string;
};

export const WIRE_AUDIT_CASES: WireAuditCase[] = [
  {
    method: "GET",
    path: "/api/session/",
    wire: {
      logged_in: true,
      username: "alice",
      is_staff: false,
      machine_name: ".cluster.example",
    },
  },
  {
    method: "POST",
    path: "/api/session/drop-staff/",
    wire: { ok: true, message: "Staff removed", is_staff: false },
  },
  {
    method: "GET",
    path: "/api/user-api-key/",
    wire: { username: "alice", raw_key: null, key_prefix: "abc123" },
  },
  {
    method: "POST",
    path: "/api/user-api-key/rotate/",
    wire: { username: "alice", raw_key: "secret", key_prefix: "xyz" },
  },
  {
    method: "POST",
    path: "/api/cache/invalidate-page/",
    wire: {
      ok: true,
      page_path: "/machine/jobs/",
      deleted_keys: 3,
      scanned_keys: 10,
    },
  },
  {
    method: "POST",
    path: "/api/sacct/ingest/",
    wire: { ok: true, message: "Ingested 5 jobs" },
  },
  {
    method: "GET",
    path: "/api/home/",
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
    method: "GET",
    path: "/api/jobs/",
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
        performance_statuses: [{ sort_rank: 0, label: "Metrics & Plots available" }],
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
    method: "GET",
    path: "/api/jobs/filter_options/",
    wire: {
      filter_options: {
        usernames: ["alice"],
        accounts: ["proj"],
        queues: ["normal"],
        states: ["COMPLETED"],
        performance_statuses: [{ sort_rank: 0, label: "Metrics & Plots available" }],
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
    method: "GET",
    path: "/api/jobs/histograms/",
    wire: {
      group: "metric",
      metric: "runtime",
      nj: 10,
      histogram_nj: 10,
      histogram_sampled: false,
      title: "Runtime",
      plot_item_thumb: OPENAPI_BOKEH_JSON_ITEM,
      plot_item_full: OPENAPI_BOKEH_JSON_ITEM,
      plot_unavailable_reason: null,
    },
  },
  {
    method: "GET",
    path: "/api/jobs/histograms/batch/",
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
          plot_item_thumb: OPENAPI_BOKEH_JSON_ITEM,
          plot_item_full: OPENAPI_BOKEH_JSON_ITEM,
          plot_unavailable_reason: null,
        },
      ],
    },
  },
  {
    method: "GET",
    path: "/api/host_plot/",
    wire: {
      host: "n001.cluster.example",
      plot_item: OPENAPI_BOKEH_JSON_ITEM,
      plot_unavailable_reason: null,
      end_time__gte: "2024-01-01T00:00:00+00:00",
      end_time__lte: "2024-01-02T00:00:00+00:00",
    },
  },
  {
    method: "GET",
    path: "/api/admin_monitor/",
    wire: {
      host_stats: [
        {
          host: "n001",
          last_time: "2024-01-01T00:00:00+00:00",
          age_bucket: "ok",
        },
      ],
      telemetry_health: {
        window_hours: 12,
        computed_at: "2024-01-01T00:00:00+00:00",
        timed_out: false,
        error: null,
        all_zero_events: [
          { type: "host_cpu", event: "user", row_count: 42 },
        ],
        missing_core_types: ["host_mem"],
        truncated: false,
        hosts_sampled_fqdns: ["n001.cluster.example"],
        monitor_identities: [
          {
            fqdn: "n001.cluster.example",
            package_version: "3.0",
            uname: "Linux x86_64",
            capability_slug: null,
            schema_types: ["host_cpu", "host_mem"],
            updated_at: 1710000000,
          },
        ],
        findings: [
          {
            kind: "all_zero_core_event",
            severity: "high",
            message: "Core type/event is all-zero over the sampled window.",
            type: "host_cpu",
            event: "user",
            row_count: 42,
          },
        ],
        monitor_handoff_markdown:
          "# Telemetry health handoff (Admin Monitor)\n\n## Actionable findings\n",
        ok_summary: {
          nonzero_type_event_pairs: 17,
          scanned_note: "Scanned non-error (type, event) pairs in the last 12 hours.",
          hosts_sampled: 1,
        },
      },
    },
  },
  {
    method: "GET",
    path: "/api/job_monitor/",
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
    method: "GET",
    path: "/api/job_monitor/gpu/",
    label: "single user",
    wire: {
      username: "alice",
      gpu_count_total: 4,
      gpu_active_total: 2,
      gpu_active_percentage: 50.0,
      has_data: true,
    },
  },
  {
    method: "GET",
    path: "/api/job_monitor/gpu/",
    label: "batch",
    wire: {
      results: [
        {
          username: "alice",
          gpu_count_total: 4,
          gpu_active_total: 2,
          gpu_active_percentage: 50.0,
          has_data: true,
        },
        {
          username: "bob",
          gpu_count_total: 0,
          gpu_active_total: 0,
          gpu_active_percentage: 0.0,
          has_data: false,
        },
      ],
    },
  },
  {
    method: "GET",
    path: "/api/jobs/123/",
    wire: {
      job_data: { jid: "123", host_list: ["n001"] },
      host_list: ["n001"],
      proc_list: [
        {
          host: "n001",
          proc: "python",
          device: "python/1234/0-31/0",
          uid: 1000,
          vm_rss: 102400,
          vm_hwm: 204800,
          vm_size: 512000,
          threads: 4,
        },
        {
          host: "n001",
          proc: "mpirun",
          device: "mpirun/5678/0-31/0",
          uid: 1000,
          vm_rss: 8192,
          vm_hwm: 8192,
          vm_size: 16384,
          threads: 1,
        },
      ],
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
      staff_artifact_contract: {
        current_plot: 11,
        current_detail: 8,
        db_plot: [11],
        db_detail: [8],
      },
    },
  },
  {
    method: "GET",
    path: "/api/jobs/123/plots/",
    wire: {
      mplot_item: OPENAPI_BOKEH_JSON_ITEM,
      mplot_unavailable_reason: null,
      rplot_item: null,
      rplot_unavailable_reason: null,
      grplot_item: null,
      grplot_unavailable_reason: null,
      grplot_bw_axis: null,
      status: "ready",
    },
  },
  {
    method: "GET",
    path: "/api/jobs/123/cpu/",
    wire: {
      type_name: "cpu",
      jobid: "123",
      tplot_item: OPENAPI_BOKEH_JSON_ITEM,
      stats_data: [],
      schema: [],
      status: "ready",
    },
  },
  {
    method: "GET",
    path: "/api/pub/cluster-dashboard/",
    label: "meta",
    wire: {
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
    },
  },
  {
    method: "GET",
    path: "/api/pub/cluster-dashboard/",
    label: "lazy period",
    wire: {
      status: "ready",
      machine_name: "test.cluster.example",
      section: "expansion_factor",
      grouping: "monthly",
      period_key: "2024-01",
      block: {
        scheduler_expansion_factor_daily_means_in_month_count: 28,
        histogram_bin_edges: [0.0, 1.0, 2.0],
        histogram_counts: [5, 10, 3],
        expansion_factor_definition:
          "(queue_wait_seconds + runtime_seconds) / (ncores * runtime_seconds)",
        bokeh_histogram_json_item: OPENAPI_BOKEH_JSON_ITEM,
      },
    },
  },
  {
    method: "GET",
    path: "/api/pub/cluster-dashboard/",
    label: "legacy full",
    wire: {
      status: "ready",
      machine_name: "test",
      expansion_factors: { cpu: 1.2 },
      monthly_metrics: [{ title: "Jan", bokeh_histogram_json_item: OPENAPI_BOKEH_JSON_ITEM }],
    },
  },
];

/** Unique exact registry paths exercised by wire audit cases. */
export const WIRE_AUDIT_EXACT_PATHS = [
  ...new Set(WIRE_AUDIT_CASES.map(({ method, path }) => `${method} ${path}`)),
];
