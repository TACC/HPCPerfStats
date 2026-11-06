import type { BokehJsonItem } from "./bokeh";

export type FreshnessBucket = "ok" | "gt_10min" | "gt_hour" | "gt_day" | "gt_week";

export type AdminMonitorHostRow = {
  host: string;
  last_time?: string | null;
  age_bucket?: FreshnessBucket | string;
};

export type AdminMonitorSectionResponse = Record<string, unknown>;

export type AdminMonitorXaltStats = {
  total_jids?: number;
  jids_with_xalt_data?: number;
  jids_missing_xalt_data?: number;
  jids?: string[];
  error?: string;
  missing_jids?: string[];
  missing_jids_truncated?: boolean;
  missing_jids_limit?: number;
  found_jids?: string[];
  found_jids_truncated?: boolean;
  found_jids_limit?: number;
};

export type AdminMonitorTelemetryZeroEvent = {
  type: string;
  event: string;
  row_count: number;
};

export type AdminMonitorTelemetryFinding = {
  kind: string;
  severity: string;
  message: string;
  type?: string;
  event?: string;
  row_count?: number;
  fqdn?: string;
};

export type AdminMonitorTelemetryIdentity = {
  fqdn?: string;
  package_version?: string | null;
  uname?: string | null;
  capability_slug?: string | null;
  schema_types?: string[];
  updated_at?: number;
};

export type AdminMonitorTelemetryHealth = {
  window_hours?: number;
  computed_at?: string;
  timed_out?: boolean;
  error?: string | null;
  all_zero_events?: AdminMonitorTelemetryZeroEvent[];
  missing_core_types?: string[];
  truncated?: boolean;
  hosts_sampled_fqdns?: string[];
  monitor_identities?: AdminMonitorTelemetryIdentity[];
  findings?: AdminMonitorTelemetryFinding[];
  monitor_handoff_markdown?: string;
  ok_summary?: {
    nonzero_type_event_pairs?: number;
    scanned_note?: string;
    hosts_sampled?: number;
  };
};

export type JobMetricCell = {
  value?: string | number | null;
  no_data_reason?: string | null;
};

export type JobPlotStateEntry = {
  loading: boolean;
  plotItem: BokehJsonItem | null;
  unavailableReason: string | null;
  /** GPU roofline measured-axis mode from ``grplot_bw_axis``. */
  bwAxis?: "memory_bw" | "pcie_nvlink" | null;
};

export type JobPlotsState = Record<string, JobPlotStateEntry>;

export type JobPlotBatchResponse = Record<string, unknown> & {
  status?: string;
  progressive?: boolean;
  retry_after_seconds?: number;
  loading_plots?: string[];
};

export type JobListHistogramEntry = {
  title?: string;
  plot_item_thumb?: BokehJsonItem | null;
  plot_item_full?: BokehJsonItem | null;
  plot_unavailable_reason?: string | null;
};

export type MetricHistStatusEntry = {
  loading: boolean;
  error: string | null;
};

export type MetricHistStatusMap = Record<string, MetricHistStatusEntry>;

export type JobMonitorRow = Record<string, unknown>;

export type HostDetailData = Record<string, unknown>;

export type TypeDetailData = Record<string, unknown> & {
  type_name?: string;
  values?: unknown[];
};

export type PubDashboardHistogramBlock = {
  expansion_factor_definition?: string;
  histogram_bin_edges?: unknown[];
  histogram_counts?: unknown[];
  bokeh_histogram_json_item?: BokehJsonItem | null;
};

export type PubDashboardHistogramMap = Record<string, PubDashboardHistogramBlock>;

export type PubDashboardExpansionFactorSection = {
  monthly_daily_histograms?: PubDashboardHistogramMap;
  yearly_weekly_histograms?: PubDashboardHistogramMap;
};

export type PubDashboardBundle = Record<string, unknown> & {
  status?: string;
  detail?: string;
  retry_hint?: string;
  sections?: {
    expansion_factor?: PubDashboardExpansionFactorSection;
  };
};

/** ``section=ingest_coverage`` pair row (``admin_monitor_ingest_coverage``). */
export type IngestCoveragePairClass =
  | "derived"
  | "type_detail_only"
  | "type_detail_dropped"
  | "unused";

export type AdminMonitorIngestCoveragePair = {
  type: string;
  event: string;
  class: IngestCoveragePairClass;
  host_fraction: number;
  all_zero_fraction?: number;
  stages?: string[];
  last_consumed_at?: number | null;
};

export type AdminMonitorIngestCoverageDiff = {
  type: string;
  event: string;
  class: IngestCoveragePairClass | "not_sent";
  host_fraction: number;
  baseline_fraction: number;
};

export type AdminMonitorIngestCoverageHostRow = {
  fqdn?: string | null;
  status?: string;
  status_reason?: string | null;
  newest_sample?: number | null;
  lag_vs_fleet_s?: number | null;
  ingest_backlog_s?: number | null;
  open_segment_age_s?: number | null;
  scanned_at?: number | null;
  signature?: string | null;
  pair_count?: number | null;
  stale?: boolean;
};

export type AdminMonitorIngestCoverageGroup = {
  key: string;
  hosts: number;
  gap_signature?: string;
  baseline_share?: number;
  is_baseline?: boolean;
  slugs?: Record<string, number>;
  host_prefixes?: Record<string, number>;
  schema_variants?: Record<string, number>;
  class_counts?: Partial<Record<IngestCoveragePairClass, number>>;
  gap_count?: number;
  pairs?: AdminMonitorIngestCoveragePair[];
  pairs_total?: number;
  diff_vs_baseline?: AdminMonitorIngestCoverageDiff[];
  outlier_hosts?: {
    fqdn: string;
    extra_gaps?: string[][];
    missing_vs_group?: string[][];
  }[];
  host_rows?: AdminMonitorIngestCoverageHostRow[];
  consumed_not_ingested?: string[][];
};

export type AdminMonitorIngestCoverage = {
  computed_at?: number;
  window_minutes?: number;
  consumed_fresh_days?: number;
  group_by?: string;
  group_by_modes?: string[];
  sweeper_state?: "ok" | "sweeping" | "sweeper_stale" | "sweep_disabled";
  sweep?: {
    started_at?: number | null;
    finished_at?: number | null;
    hosts_done?: number | null;
    hosts_total?: number | null;
    interval_s?: number | null;
    last_error?: string | null;
  };
  last_sweep_at?: number | null;
  hosts_total?: number;
  hosts_scanned?: number;
  hosts_grouped?: number;
  incomplete?: boolean;
  errors?: string[];
  status_counts?: Record<string, number>;
  consumer_registry_empty?: boolean;
  consumed_pairs_fresh?: number;
  excluded_ingest_behind?: AdminMonitorIngestCoverageHostRow[];
  groups?: AdminMonitorIngestCoverageGroup[];
  groups_total?: number;
  markdown?: string;
};
